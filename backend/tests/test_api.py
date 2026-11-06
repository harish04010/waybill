from fastapi.testclient import TestClient

from waybill.api import create_app
from tests.conftest import fast_settings


def test_simulate_and_read_back(tmp_path):
    app = create_app(fast_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health").json()["llm"] == "mock"
        created = client.post("/simulate", json={"count": 3}).json()["created"]
        assert len(created) == 3

        detail = client.get(f"/shipments/{created[0]}").json()
        assert detail["shipment"]["id"] == created[0]
        assert detail["events"][0]["kind"] == "created"

        assert client.get("/metrics").json()["total"] == 3
        assert client.get("/shipments/nope").status_code == 404


def test_decision_on_shipment_that_is_not_waiting_is_a_conflict(tmp_path):
    app = create_app(fast_settings(tmp_path))
    with TestClient(app) as client:
        sid = client.post("/shipments").json()["id"]
        r = client.post(f"/shipments/{sid}/decision", json={"decision": "approve"})
        assert r.status_code == 409
