from waybill.agents import DispatchAgent, TrackingAgent
from waybill.models import Carrier, Status


async def test_happy_path_runs_every_shipment_to_an_end_state(make_orch):
    orch = make_orch()
    for _ in range(8):
        await orch.create()
    await orch.wait_idle()

    statuses = {s.status for s in orch.store.all()}
    assert statuses <= {Status.DELIVERED, Status.NEEDS_APPROVAL, Status.FAILED}
    assert Status.DELIVERED in statuses


async def test_delivered_shipments_keep_their_margin_floor_unless_a_human_approved(make_orch):
    orch = make_orch(auto_approve_after=0.0)
    for _ in range(10):
        await orch.create()
    await orch.wait_idle()

    for s in orch.store.all():
        if s.status != Status.DELIVERED:
            continue
        approved = any(e["kind"] == "approved" for e in orch.store.events(s.id))
        if not approved:
            assert s.margin >= orch.settings.min_margin - 0.005, s.id


async def test_flaky_world_triggers_retries_and_still_finishes(make_orch):
    orch = make_orch(failure_rate=0.35, max_attempts=4)
    for _ in range(6):
        await orch.create()
    await orch.wait_idle()

    events = orch.store.events()
    assert any(e["kind"] == "retry" for e in events)
    assert all(s.status in {Status.DELIVERED, Status.NEEDS_APPROVAL, Status.FAILED} for s in orch.store.all())


async def test_always_failing_world_escalates_instead_of_looping(make_orch):
    orch = make_orch(failure_rate=1.0)
    s = await orch.create()
    await orch.wait_idle()

    final = orch.store.get(s.id)
    assert final.status == Status.FAILED
    assert final.attempts["quote"] == orch.settings.max_attempts
    assert any(e["kind"] == "escalated" for e in orch.store.events(s.id))


async def test_resume_picks_up_where_it_left_off(make_orch):
    orch = make_orch()
    s = orch.world.new_shipment()
    s.status, s.market_rate, s.quote = Status.QUOTED, 1000.0, 1200.0
    orch.store.save(s)  # pretend we crashed right after quoting

    resumed = await orch.resume()
    await orch.wait_idle()

    assert resumed == 1
    events = orch.store.events(s.id)
    assert events[0]["kind"] == "resumed"
    assert not any(e["agent"] == "quote" for e in events)  # didn't redo the quote
    assert orch.store.get(s.id).status != Status.QUOTED


async def test_reject_sends_dispatch_back_out_without_that_carrier(make_orch):
    orch = make_orch()
    s = orch.world.new_shipment()
    s.status, s.market_rate, s.quote = Status.QUOTED, 1000.0, 1200.0
    await DispatchAgent(orch.ctx)._ask_human(
        s, Carrier(name="Redline Hauling", mc_number="123456", rating=3.9, on_time_rate=0.8, asking_rate=1100),
        1080, "test",
    )
    orch.store.save(s)

    await orch.decide(s.id, "reject")
    await orch.wait_idle()

    final = orch.store.get(s.id)
    assert "Redline Hauling" in final.excluded_carriers
    assert final.carrier is None or final.carrier.name != "Redline Hauling"


def test_negotiation_policy_never_offers_above_ceiling():
    c = Carrier(name="X", mc_number="1", rating=4.5, on_time_rate=0.9, asking_rate=2000)
    history = []
    for counter in (1900, 1800, 1750):
        move = DispatchAgent._mock_move(c, 1500, history)
        if move["action"] != "offer":
            break
        assert move["amount"] <= 1500
        history.append({"round": len(history) + 1, "we_offered": move["amount"], "counter": counter})


def test_driver_text_parser():
    p = TrackingAgent._mock_parse
    assert p("dot inspection at the scale, gonna be late")["event"] == "delayed"
    assert p("dropped off, no damage, pics uploaded")["event"] == "delivered"
    assert p("picked up, vin matches")["event"] == "picked_up"
    assert p("")["event"] == "none"


class WildLLM:
    """Stands in for a model having a bad day."""
    name = "wild"

    async def decide(self, *, system, prompt, mock):
        out = mock()
        if "price" in out:
            out["price"] = 99_999  # absurd quote
        if out.get("action") == "offer":
            out["amount"] = 50_000  # way over the ceiling
        if "body" in out:
            out["body"] = "FYI we're paying the carrier " + prompt  # tries to leak internals
        return out


async def test_guardrails_hold_when_the_model_misbehaves(make_orch):
    orch = make_orch()
    orch.llm = WildLLM()
    from waybill.agents import AgentContext, DispatchAgent, QuoteAgent
    orch.ctx = AgentContext(orch.world, orch.llm, orch.bus, orch.settings)

    s = orch.world.new_shipment()
    orch.store.save(s)
    await QuoteAgent(orch.ctx).run(s)
    assert s.quote <= s.market_rate * 1.6 + 5

    ceiling = round(s.quote * (1 - orch.settings.min_margin))
    try:
        await DispatchAgent(orch.ctx).run(s)
    except Exception:
        pass  # might not find a carrier; we only care about what was offered
    offers = [e for e in orch.store.events(s.id) if e["kind"] == "decision" and e["message"].startswith("Offered")]
    for e in offers:
        assert float(e["message"].split("$")[1].replace(",", "")) <= ceiling
    assert any(e["kind"] == "guardrail" for e in orch.store.events(s.id))
