import asyncio
import time

from .models import Shipment
from .store import Store


class EventBus:
    """
    Every agent action becomes an event. Events are written to the store
    first (so the trace survives restarts) and then fanned out to anyone
    listening, which is how the dashboard gets live updates over SSE.
    """

    def __init__(self, store: Store):
        self.store = store
        self.subscribers: set[asyncio.Queue] = set()

    async def emit(self, subject: Shipment, agent: str, kind: str, message: str, **data) -> dict:
        event = {
            "shipment_id": subject.id,
            "ts": time.time(),
            "agent": agent,
            "kind": kind,
            "message": message,
            "data": data,
        }
        event["seq"] = self.store.append_event(event)
        for q in list(self.subscribers):
            q.put_nowait(event)
        return event

    async def emit_state(self, shipment: Shipment, agent: str = "orchestrator") -> None:
        await self.emit(shipment, agent, "state", f"status: {shipment.status.value}", shipment=shipment.snapshot())

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)
