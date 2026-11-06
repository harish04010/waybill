r"""
The orchestrator decides which agent works on a shipment next and keeps the
whole thing dependable.

The model is a state machine with checkpoints:

    new -> quoted -> booked -> in_transit -> delivered
                 \-> needs_approval (waits for a person)
    any stage -> failed (after retries run out)

Each step loads the shipment fresh from the store, lets one agent work on it,
and saves it only if the step finished. A crash or a transient error halfway
through a step leaves the last good checkpoint untouched, and the step runs
again from there.
"""
import asyncio
import random

from .agents import AgentContext, DispatchAgent, NotifyAgent, QuoteAgent, TrackingAgent
from .config import Settings
from .events import EventBus
from .llm import LLM, make_llm
from .models import Shipment, Status, TERMINAL
from .store import Store
from .world import TransientError, World


class Orchestrator:
    def __init__(self, settings: Settings, store: Store, bus: EventBus,
                 world: World | None = None, llm: LLM | None = None):
        self.settings = settings
        self.store = store
        self.bus = bus
        self.world = world or World(settings.failure_rate, settings.sim_speed, settings.seed)
        self.llm = llm or make_llm(settings)
        self.ctx = AgentContext(self.world, self.llm, bus, settings)
        self.slots = asyncio.Semaphore(settings.max_concurrency)
        self.tasks: dict[str, asyncio.Task] = {}
        self._jitter = random.Random(settings.seed)

        self.handlers = {
            Status.NEW: ("quote", QuoteAgent(self.ctx).run),
            Status.QUOTED: ("dispatch", DispatchAgent(self.ctx).run),
            Status.BOOKED: ("tracking", TrackingAgent(self.ctx).run),
            Status.IN_TRANSIT: ("tracking", TrackingAgent(self.ctx).run),
        }

    # ---- lifecycle ---------------------------------------------------------

    async def resume(self) -> int:
        """Pick up anything that was mid-flight when the process last stopped."""
        pending = self.store.unfinished()
        for s in pending:
            await self.bus.emit(s, "orchestrator", "resumed", f"Resuming from '{s.status.value}' after restart")
            self.submit(s.id)
        return len(pending)

    async def create(self, shipment: Shipment | None = None) -> Shipment:
        s = shipment or self.world.new_shipment()
        self.store.save(s)
        await self.bus.emit(s, "orchestrator", "created", f"New shipment: {s.vehicle}, {s.lane}")
        await self.bus.emit_state(s)
        self.submit(s.id)
        return s

    def submit(self, shipment_id: str) -> None:
        task = self.tasks.get(shipment_id)
        if task and not task.done():
            return
        self.tasks[shipment_id] = asyncio.create_task(self._drive(shipment_id), name=shipment_id)

    async def wait_idle(self) -> None:
        """Mostly for tests and scripts: block until nothing is running."""
        while any(not t.done() for t in self.tasks.values()):
            await asyncio.gather(*[t for t in self.tasks.values() if not t.done()], return_exceptions=True)

    async def shutdown(self) -> None:
        for t in self.tasks.values():
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    # ---- the loop ----------------------------------------------------------

    async def _drive(self, shipment_id: str) -> None:
        while True:
            s = self.store.get(shipment_id)
            if s is None or s.status in TERMINAL:
                return
            if s.status == Status.NEEDS_APPROVAL:
                if self.settings.auto_approve_after is not None:
                    await self.world.pause(self.settings.auto_approve_after)
                    await self.decide(shipment_id, "approve", note="auto-approved for demo", actor="ops (demo)")
                    continue
                return  # parked until a person decides

            stage, handler = self.handlers[s.status]
            ok = await self._run_step(shipment_id, stage, handler)
            if not ok:
                return
            s = self.store.get(shipment_id)
            if s and s.status == Status.IN_TRANSIT:
                await self.world.pause(1.2)  # time between GPS pings

    async def _run_step(self, shipment_id: str, stage: str, handler) -> bool:
        for attempt in range(1, self.settings.max_attempts + 1):
            s = self.store.get(shipment_id)  # always start from the last checkpoint
            try:
                async with self.slots:
                    await handler(s)
            except TransientError as e:
                checkpoint = self.store.get(shipment_id)
                checkpoint.attempts[stage] = checkpoint.attempts.get(stage, 0) + 1
                self.store.save(checkpoint)
                if attempt == self.settings.max_attempts:
                    return await self._give_up(checkpoint, stage, str(e))
                delay = (2 ** attempt) * 0.5 + self._jitter.uniform(0, 0.5)
                await self.bus.emit(checkpoint, "orchestrator", "retry",
                                    f"{stage} failed ({e}). Retry {attempt}/{self.settings.max_attempts - 1} in {delay:.1f}s",
                                    stage=stage, attempt=attempt, error=str(e))
                await self.world.pause(delay)
                continue
            except Exception as e:  # a bug, not the weather. Don't retry blindly.
                checkpoint = self.store.get(shipment_id)
                return await self._give_up(checkpoint, stage, f"unexpected error: {type(e).__name__}: {e}")

            self.store.save(s)  # commit the checkpoint
            await self.bus.emit_state(s, stage)
            return True
        return False

    async def _give_up(self, s: Shipment, stage: str, reason: str) -> bool:
        s.status = Status.FAILED
        s.failure_reason = f"{stage}: {reason}"
        self.store.save(s)
        await self.bus.emit(s, "orchestrator", "escalated",
                            f"Gave up on {stage} after {self.settings.max_attempts} tries. Flagged for the ops team.",
                            reason=reason)
        await self.bus.emit_state(s)
        return False

    # ---- human in the loop -------------------------------------------------

    async def decide(self, shipment_id: str, decision: str, note: str = "", actor: str = "ops") -> Shipment:
        s = self.store.get(shipment_id)
        if s is None:
            raise KeyError(shipment_id)
        if s.status != Status.NEEDS_APPROVAL or s.approval is None:
            raise ValueError(f"{shipment_id} isn't waiting on a decision")

        if decision == "approve":
            a = s.approval
            s.carrier, s.carrier_rate, s.approval = a.carrier, a.rate, None
            s.status = Status.BOOKED
            await self.bus.emit(s, actor, "approved", f"Approved {a.carrier.name} at ${a.rate:,.0f}", note=note)
            self.store.save(s)
            await NotifyAgent(self.ctx).send(s, "booked")
        elif decision == "reject":
            rejected = s.approval.carrier.name
            s.excluded_carriers.append(rejected)
            s.approval = None
            s.status = Status.QUOTED  # back to dispatch, minus that carrier
            await self.bus.emit(s, actor, "rejected", f"Rejected {rejected}. Dispatch will try other carriers.", note=note)
            self.store.save(s)
        else:
            raise ValueError("decision must be 'approve' or 'reject'")

        await self.bus.emit_state(s, actor)
        self.submit(s.id)
        return s

    # ---- numbers for the dashboard -----------------------------------------

    def metrics(self) -> dict:
        shipments = self.store.all()
        by_status = {st.value: 0 for st in Status}
        for s in shipments:
            by_status[s.status.value] += 1
        margins = [s.margin for s in shipments if s.margin is not None]
        retries = sum(sum(s.attempts.values()) for s in shipments)
        return {
            "total": len(shipments),
            "by_status": by_status,
            "avg_margin": round(sum(margins) / len(margins), 4) if margins else None,
            "retries": retries,
            "llm": self.llm.name,
        }
