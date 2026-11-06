import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("WAYBILL_DB", "waybill.db"))

    # How many agent steps may run at the same time across all shipments.
    max_concurrency: int = field(default_factory=lambda: _env_int("WAYBILL_CONCURRENCY", 6))

    # Chance that any single call into the outside world (rate API, phone line,
    # GPS feed) fails with a transient error. Turn it up to watch retries work.
    failure_rate: float = field(default_factory=lambda: _env_float("WAYBILL_FAILURE_RATE", 0.12))

    # 1.0 is "demo speed". Tests crank this way up so a full lifecycle takes milliseconds.
    sim_speed: float = field(default_factory=lambda: _env_float("WAYBILL_SIM_SPEED", 1.0))

    # Business rules
    min_margin: float = 0.12          # below this, a human signs off on the booking
    min_carrier_rating: float = 4.2   # riskier carriers also need a human
    max_attempts: int = 3             # per stage, before we give up and escalate

    # If set, pending approvals are auto-approved after this many seconds.
    # Only used to record the hosted demo; leave it off when running for real.
    auto_approve_after: float | None = None

    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    model: str = field(default_factory=lambda: os.getenv("WAYBILL_MODEL", "claude-sonnet-5-5"))

    seed: int | None = None

    @property
    def llm_mode(self) -> str:
        return "anthropic" if self.anthropic_api_key else "mock"
