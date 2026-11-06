"""
The four agents that move a shipment.

Each agent owns one job and talks to the world only through tools. Decisions
that need judgment go to the LLM; anything with money or safety attached is
checked afterwards by plain code, because a model being confident is not the
same as a model being right.
"""
import json
from dataclasses import dataclass

from .config import Settings
from .events import EventBus
from .llm import LLM
from .models import Approval, Carrier, Shipment, Status
from .world import TransientError, World


@dataclass
class AgentContext:
    world: World
    llm: LLM
    bus: EventBus
    settings: Settings


class Agent:
    name = "agent"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx
        self.world = ctx.world
        self.llm = ctx.llm
        self.settings = ctx.settings

    async def emit(self, s: Shipment, kind: str, message: str, **data):
        return await self.ctx.bus.emit(s, self.name, kind, message, **data)

    async def tool(self, s: Shipment, name: str, fn, *args, **kwargs):
        """Call a tool and log both sides of it, so the trace shows exactly what the agent saw."""
        await self.emit(s, "tool_call", f"{name}()", tool=name)
        try:
            result = await fn(*args, **kwargs)
        except TransientError as e:
            await self.emit(s, "tool_error", f"{name} failed: {e}", tool=name)
            raise
        await self.emit(s, "tool_result", f"{name} returned", tool=name, result=_jsonable(result))
        return result


def _jsonable(x):
    if isinstance(x, list):
        return [_jsonable(i) for i in x]
    if hasattr(x, "model_dump"):
        return x.model_dump()
    return x


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

class NotifyAgent(Agent):
    name = "notify"
    SYSTEM = (
        "You write short, plain updates for a vehicle transport brokerage. "
        "Customers are car dealerships; be direct and friendly, no fluff, under 45 words. "
        "Never mention what we pay the carrier or our margin. "
        'Return {"body": "..."}'
    )

    TEMPLATES = {
        "quote": "Hi {customer} team, your quote for the {vehicle} ({origin} to {destination}, {trailer} trailer) is ${quote:,.0f}. Reply to confirm and we'll lock in a carrier.",
        "booked": "Good news: {carrier} is assigned to your {vehicle}. Pickup in {origin} is being scheduled now and we'll text you once it's on the truck.",
        "picked_up": "Your {vehicle} was picked up in {origin}. Current estimate is about {eta} days to {destination}.",
        "delayed": "Heads up: your {vehicle} is running behind ({reason}). New estimate is about {eta} days. We're watching it closely.",
        "delivered": "Your {vehicle} was delivered in {destination} and signed for. Photos are on file if you need them. Thanks for shipping with us.",
    }

    async def send(self, s: Shipment, purpose: str, **facts) -> None:
        fields = {
            "customer": s.customer, "vehicle": s.vehicle, "origin": s.origin,
            "destination": s.destination, "trailer": s.trailer, "quote": s.quote or 0,
            "carrier": s.carrier.name if s.carrier else "a carrier",
            "eta": s.eta_days, "reason": facts.get("reason", "a delay on the road"),
        }
        fallback = self.TEMPLATES[purpose].format(**fields)
        prompt = (
            f"Write a customer update. Purpose: {purpose}.\n"
            f"Facts: {json.dumps({k: v for k, v in fields.items() if k != 'quote' or purpose == 'quote'})}"
        )
        decision = await self.llm.decide(system=self.SYSTEM, prompt=prompt, mock=lambda: {"body": fallback})
        body = str(decision.get("body", "")).strip() or fallback

        # Guardrail: the carrier's rate must never leak to the customer.
        if s.carrier_rate and f"{s.carrier_rate:,.0f}" in body:
            await self.emit(s, "guardrail", "Draft mentioned the carrier rate, swapped in the template")
            body = fallback

        await self.emit(s, "message", f"Texted {s.customer}", channel="sms", to=s.customer, purpose=purpose, body=body)


# --------------------------------------------------------------------------
# Quoting
# --------------------------------------------------------------------------

class QuoteAgent(Agent):
    name = "quote"
    SYSTEM = (
        "You price single-vehicle auto transport for a freight brokerage. "
        "Given today's market rate for a lane, pick a customer price that wins the "
        "business and leaves room to pay a good carrier. Typical markup is 15-25%. "
        'Return {"price": number, "reasoning": "one or two sentences", "confidence": 0-1}'
    )

    async def run(self, s: Shipment) -> None:
        await self.emit(s, "started", f"Pricing a {s.vehicle} on {s.lane}")
        market = await self.tool(s, "get_market_data", self.world.get_market_data, s)
        s.market_rate = market["market_rate"]

        prompt = (
            f"Vehicle: {s.vehicle}\nTrailer: {s.trailer}\nLane: {s.lane} ({s.miles} mi)\n"
            f"Market data: {json.dumps(market)}"
        )
        decision = await self.llm.decide(system=self.SYSTEM, prompt=prompt, mock=lambda: self._mock(s, market))
        price = float(decision["price"])

        # Guardrail: don't quote below cost or absurdly high, whatever the model says.
        lo, hi = s.market_rate * 1.08, s.market_rate * 1.6
        if not lo <= price <= hi:
            clamped = min(max(price, lo), hi)
            await self.emit(s, "guardrail", f"Price ${price:,.0f} outside ${lo:,.0f}-${hi:,.0f}, clamped to ${clamped:,.0f}")
            price = clamped

        s.quote = round(price / 5) * 5
        await self.emit(s, "decision", f"Quoted ${s.quote:,.0f}", reasoning=decision.get("reasoning"),
                        confidence=decision.get("confidence"))
        s.status = Status.QUOTED
        await NotifyAgent(self.ctx).send(s, "quote")

    def _mock(self, s: Shipment, market: dict) -> dict:
        markup = 0.18
        notes = []
        if s.trailer == "enclosed":
            markup += 0.03
            notes.append("enclosed carriers are scarcer, so I padded it a little")
        if market["trucks_posted"] < 5:
            markup += 0.04
            notes.append(f"only {market['trucks_posted']} trucks posted on this lane")
        if s.miles > 1800:
            notes.append("long haul, per-mile cost is already low")
        reasoning = f"Market is ${market['market_rate']:,.0f} (${market['per_mile']}/mi). Using {markup:.0%} markup"
        reasoning += (": " + "; ".join(notes) + ".") if notes else "."
        return {"price": market["market_rate"] * (1 + markup), "reasoning": reasoning, "confidence": 0.8}


# --------------------------------------------------------------------------
# Dispatch: find a carrier and negotiate
# --------------------------------------------------------------------------

class DispatchAgent(Agent):
    name = "dispatch"
    MAX_CARRIERS = 3
    SYSTEM = (
        "You negotiate carrier rates by phone for a vehicle transport brokerage. "
        "You get the carrier's asking rate, your ceiling (never offer above it), and the "
        "conversation so far. Open low but credible, move in shrinking steps, and accept "
        "a counter that is under the ceiling once the gap is small. "
        'Return {"action": "offer" | "accept" | "walk", "amount": number, "say": "what you tell the dispatcher"}'
    )

    async def run(self, s: Shipment) -> None:
        ceiling = round(s.quote * (1 - self.settings.min_margin), 0)
        await self.emit(s, "started", f"Finding a carrier. Ceiling is ${ceiling:,.0f} to keep a {self.settings.min_margin:.0%} margin")

        carriers = await self.tool(s, "find_carriers", self.world.find_carriers, s)
        ranked = sorted(carriers, key=lambda c: self._score(c, s), reverse=True)
        await self.emit(s, "thought", "Ranked carriers by rating, on-time history and asking rate",
                        ranking=[{"name": c.name, "asking": c.asking_rate, "rating": c.rating,
                                  "on_time": c.on_time_rate} for c in ranked])

        best_over_ceiling: tuple[Carrier, float] | None = None
        for carrier in ranked[: self.MAX_CARRIERS]:
            outcome = await self._negotiate(s, carrier, ceiling)
            if outcome["result"] == "booked":
                rate = outcome["rate"]
                if carrier.rating < self.settings.min_carrier_rating:
                    return await self._ask_human(s, carrier, rate,
                                                 f"{carrier.name} agreed at ${rate:,.0f} but is rated {carrier.rating}, under our {self.settings.min_carrier_rating} bar")
                return await self._book(s, carrier, rate)
            if outcome.get("last_counter"):
                lc = outcome["last_counter"]
                if best_over_ceiling is None or lc < best_over_ceiling[1]:
                    best_over_ceiling = (carrier, lc)

        # Nobody came in under the ceiling. If the best counter still makes us
        # *some* money, let a human decide instead of losing the load.
        if best_over_ceiling:
            carrier, rate = best_over_ceiling
            margin = (s.quote - rate) / s.quote
            if margin >= 0.03:
                return await self._ask_human(
                    s, carrier, rate,
                    f"Best offer is {carrier.name} at ${rate:,.0f}, a {margin:.1%} margin, under the {self.settings.min_margin:.0%} floor",
                )
        raise TransientError("no carrier took the load at a workable rate")

    def _score(self, c: Carrier, s: Shipment) -> float:
        price_penalty = c.asking_rate / (s.market_rate or c.asking_rate)
        score = c.rating + c.on_time_rate * 3 - price_penalty * 2
        if c.rating < self.settings.min_carrier_rating:
            score -= 1.5  # still callable, but only after the carriers we'd book without asking
        return score

    async def _negotiate(self, s: Shipment, carrier: Carrier, ceiling: float) -> dict:
        history: list[dict] = []
        await self.emit(s, "started", f"Calling {carrier.name} (MC {carrier.mc_number}), asking ${carrier.asking_rate:,.0f}")
        for round_no in range(1, 5):
            prompt = (
                f"Carrier: {carrier.name}, asking ${carrier.asking_rate:,.0f}, rating {carrier.rating}\n"
                f"Your ceiling: ${ceiling:,.0f}\nRound {round_no}. History: {json.dumps(history)}"
            )
            move = await self.llm.decide(system=self.SYSTEM, prompt=prompt,
                                         mock=lambda: self._mock_move(carrier, ceiling, history))
            action = move.get("action")
            amount = float(move.get("amount") or 0)

            if action == "walk":
                await self.emit(s, "decision", f"Walked away from {carrier.name}", say=move.get("say"))
                return {"result": "walked", "last_counter": history[-1].get("counter") if history else None}

            if amount > ceiling:
                await self.emit(s, "guardrail", f"Model wanted to offer ${amount:,.0f}, above the ${ceiling:,.0f} ceiling. Capped it.")
                amount = ceiling

            await self.emit(s, "decision", f"Offered ${amount:,.0f}" if action == "offer" else f"Accepted their ${amount:,.0f}",
                            say=move.get("say"), round=round_no)
            reply = await self.tool(s, "call_carrier", self.world.call_carrier, s, carrier, amount, round_no)

            if not reply["answered"]:
                await self.emit(s, "thought", f"{carrier.name} didn't pick up. Moving on.")
                return {"result": "no_answer"}
            if reply["reply"] == "accept":
                return {"result": "booked", "rate": reply["rate"]}
            if reply["reply"] == "decline":
                await self.emit(s, "thought", f"{carrier.name} passed on the load")
                return {"result": "declined", "last_counter": history[-1].get("counter") if history else None}
            history.append({"round": round_no, "we_offered": amount, "counter": reply["rate"]})
            await self.emit(s, "thought", f"{carrier.name} countered at ${reply['rate']:,.0f}")
        return {"result": "stalled", "last_counter": history[-1]["counter"] if history else None}

    @staticmethod
    def _mock_move(carrier: Carrier, ceiling: float, history: list[dict]) -> dict:
        if not history:
            opening = round(min(carrier.asking_rate * 0.86, ceiling * 0.9))
            return {"action": "offer", "amount": opening, "say": f"I can do ${opening:,} for this one, pickup is ready."}
        last = history[-1]
        counter, ours = last["counter"], last["we_offered"]
        if counter <= ceiling and (counter - ours <= 40 or len(history) >= 2):
            return {"action": "accept", "amount": counter, "say": f"${counter:,.0f} works. Let's book it."}
        nxt = round(ours + (counter - ours) * 0.55)
        if ours >= ceiling - 1:
            return {"action": "walk", "amount": 0, "say": "That's more than I can do on this lane. Thanks anyway."}
        nxt = min(nxt, ceiling)
        return {"action": "offer", "amount": nxt, "say": f"Meet me at ${nxt:,}?"}

    async def _book(self, s: Shipment, carrier: Carrier, rate: float) -> None:
        s.carrier, s.carrier_rate, s.approval = carrier, rate, None
        s.status = Status.BOOKED
        await self.emit(s, "decision", f"Booked {carrier.name} at ${rate:,.0f} ({s.margin:.1%} margin)")
        await NotifyAgent(self.ctx).send(s, "booked")

    async def _ask_human(self, s: Shipment, carrier: Carrier, rate: float, reason: str) -> None:
        margin = round((s.quote - rate) / s.quote, 4)
        s.approval = Approval(reason=reason, carrier=carrier, rate=rate, margin=margin)
        s.status = Status.NEEDS_APPROVAL
        await self.emit(s, "approval_requested", reason, carrier=carrier.name, rate=rate, margin=margin)


# --------------------------------------------------------------------------
# Tracking
# --------------------------------------------------------------------------

class TrackingAgent(Agent):
    name = "tracking"
    SYSTEM = (
        "You read short texts from truck drivers hauling cars. Classify what happened. "
        'Return {"event": "picked_up" | "moving" | "delayed" | "delivered" | "none", '
        '"reason": "short reason if delayed, else null"}'
    )

    async def run(self, s: Shipment) -> None:
        update = await self.tool(s, "tracking_update", self.world.tracking_update, s)
        text = update["driver_text"]
        parsed = await self.llm.decide(system=self.SYSTEM, prompt=f"Driver text: {text!r}",
                                       mock=lambda: self._mock_parse(text))
        event = parsed.get("event", "none")
        gps = update["gps"]
        s.progress, s.last_location, s.eta_days = gps["progress"], gps["near"], update["eta_days"]
        await self.emit(s, "thought", f"Driver said {text!r}" if text else "No text with this ping, going off GPS",
                        parsed=parsed, gps=gps)

        if s.status == Status.BOOKED:
            s.status = Status.IN_TRANSIT
            await NotifyAgent(self.ctx).send(s, "picked_up")
            return

        if gps["progress"] >= 100:
            if event != "delivered":
                await self.emit(s, "thought", "GPS says we're at the drop but the driver didn't confirm. Trusting GPS.")
            s.status = Status.DELIVERED
            s.delayed = False
            await NotifyAgent(self.ctx).send(s, "delivered")
            return

        if event == "delayed" and not s.delayed:
            s.delayed = True
            await NotifyAgent(self.ctx).send(s, "delayed", reason=parsed.get("reason") or "a delay on the road")
        elif event in ("moving", "none") and s.delayed:
            s.delayed = False

    @staticmethod
    def _mock_parse(text: str) -> dict:
        t = text.lower()
        if any(w in t for w in ("delivered", "dropped off", "signed")):
            return {"event": "delivered", "reason": None}
        if any(w in t for w in ("loaded", "picked up", "on the truck")):
            return {"event": "picked_up", "reason": None}
        delay_words = {"weather": "weather", "inspection": "DOT inspection", "tire": "equipment issue",
                       "late": "running late", "behind": "earlier stop ran long"}
        for word, reason in delay_words.items():
            if word in t:
                return {"event": "delayed", "reason": reason}
        if t.strip():
            return {"event": "moving", "reason": None}
        return {"event": "none", "reason": None}
