import pytest

from waybill.config import Settings
from waybill.events import EventBus
from waybill.orchestrator import Orchestrator
from waybill.store import Store


def fast_settings(tmp_path, **overrides) -> Settings:
    base = dict(db_path=str(tmp_path / "test.db"), sim_speed=500.0, failure_rate=0.0,
                seed=7, anthropic_api_key=None)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def make_orch(tmp_path):
    def _make(**overrides):
        settings = fast_settings(tmp_path, **overrides)
        store = Store(settings.db_path)
        return Orchestrator(settings, store, EventBus(store))
    return _make
