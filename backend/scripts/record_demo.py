"""
Run a batch of shipments headless and save the event log as JSON.

The hosted dashboard replays this file when it isn't connected to a live
backend, so anyone clicking the Vercel link sees a real run instead of a
blank screen. Timestamps are kept, so the replay has realistic pacing.

    python scripts/record_demo.py --count 10 --out ../frontend/public/demo-run.json
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from waybill.config import Settings  # noqa: E402
from waybill.events import EventBus  # noqa: E402
from waybill.orchestrator import Orchestrator  # noqa: E402
from waybill.store import Store  # noqa: E402


async def main(count: int, out: str, seed: int, speed: float) -> None:
    db = os.path.join(tempfile.mkdtemp(), "demo.db")
    settings = Settings(db_path=db, seed=seed, sim_speed=speed, failure_rate=0.14,
                        auto_approve_after=4.0, anthropic_api_key=None)
    store = Store(db)
    orch = Orchestrator(settings, store, EventBus(store))

    for _ in range(count):
        await orch.create()
        await asyncio.sleep(0.4 / speed)  # stagger arrivals a little
    await orch.wait_idle()

    events = store.events()
    t0 = events[0]["ts"]
    for e in events:
        e["t"] = round((e["ts"] - t0) * speed, 3)  # seconds at 1x speed
        del e["ts"]

    with open(out, "w") as f:
        json.dump({"seed": seed, "events": events}, f, separators=(",", ":"))

    finals = Counter(s.status.value for s in store.all())
    kinds = Counter(e["kind"] for e in events)
    print(f"{len(events)} events -> {out}")
    print("final:", dict(finals))
    print("retries:", kinds["retry"], "guardrails:", kinds["guardrail"],
          "approvals:", kinds["approval_requested"], "escalations:", kinds["escalated"])
    print("replay length: %.0fs" % events[-1]["t"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--out", default="demo-run.json")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--speed", type=float, default=20.0, help="record faster than real time")
    args = ap.parse_args()
    asyncio.run(main(args.count, args.out, args.seed, args.speed))
