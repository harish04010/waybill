import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .events import EventBus
from .orchestrator import Orchestrator
from .store import Store


class SimulateRequest(BaseModel):
    count: int = Field(default=6, ge=1, le=40)


class Decision(BaseModel):
    decision: str  # "approve" or "reject"
    note: str = ""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.db_path)
        bus = EventBus(store)
        orch = Orchestrator(settings, store, bus)
        app.state.store, app.state.bus, app.state.orch = store, bus, orch
        await orch.resume()
        yield
        await orch.shutdown()

    app = FastAPI(title="Waybill", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # it's a demo API with no auth; lock this down for anything real
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health(request: Request):
        return {"ok": True, "llm": request.app.state.orch.llm.name}

    @app.get("/shipments")
    def list_shipments(request: Request):
        return [s.snapshot() for s in request.app.state.store.all()]

    @app.get("/shipments/{shipment_id}")
    def get_shipment(shipment_id: str, request: Request):
        s = request.app.state.store.get(shipment_id)
        if not s:
            raise HTTPException(404, "No shipment with that id")
        return {"shipment": s.snapshot(), "events": request.app.state.store.events(shipment_id)}

    @app.post("/shipments", status_code=201)
    async def create_shipment(request: Request):
        s = await request.app.state.orch.create()
        return s.snapshot()

    @app.post("/simulate", status_code=201)
    async def simulate(body: SimulateRequest, request: Request):
        created = [await request.app.state.orch.create() for _ in range(body.count)]
        return {"created": [s.id for s in created]}

    @app.post("/shipments/{shipment_id}/decision")
    async def decide(shipment_id: str, body: Decision, request: Request):
        try:
            s = await request.app.state.orch.decide(shipment_id, body.decision, body.note)
        except KeyError:
            raise HTTPException(404, "No shipment with that id")
        except ValueError as e:
            raise HTTPException(409, str(e))
        return s.snapshot()

    @app.get("/metrics")
    def metrics(request: Request):
        return request.app.state.orch.metrics()

    @app.post("/reset")
    async def reset(request: Request):
        await request.app.state.orch.shutdown()
        request.app.state.orch.tasks.clear()
        request.app.state.store.reset()
        return {"ok": True}

    @app.get("/events")
    async def stream(request: Request, since: int = 0):
        """Server-sent events. Replays history after `since`, then streams live."""
        bus: EventBus = request.app.state.bus
        queue = bus.subscribe()

        async def gen():
            try:
                last = since
                for e in request.app.state.store.events(since=since):
                    last = e["seq"]
                    yield f"id: {e['seq']}\ndata: {json.dumps(e)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        e = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if e["seq"] <= last:
                        continue
                    last = e["seq"]
                    yield f"id: {e['seq']}\ndata: {json.dumps(e)}\n\n"
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
