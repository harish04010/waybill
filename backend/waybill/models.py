import time
import uuid
from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    NEW = "new"
    QUOTED = "quoted"
    NEEDS_APPROVAL = "needs_approval"
    BOOKED = "booked"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    FAILED = "failed"


TERMINAL = {Status.DELIVERED, Status.FAILED}


class Carrier(BaseModel):
    name: str
    mc_number: str
    rating: float
    on_time_rate: float
    asking_rate: float


class Approval(BaseModel):
    reason: str
    carrier: Carrier
    rate: float
    margin: float


class Shipment(BaseModel):
    id: str = Field(default_factory=lambda: "SHP-" + uuid.uuid4().hex[:6].upper())
    customer: str
    vehicle: str
    vin: str
    trailer: str  # "open" or "enclosed"
    origin: str
    destination: str
    miles: int

    status: Status = Status.NEW
    market_rate: float | None = None
    quote: float | None = None
    carrier: Carrier | None = None
    carrier_rate: float | None = None
    excluded_carriers: list[str] = Field(default_factory=list)
    approval: Approval | None = None

    progress: int = 0            # 0-100, from GPS pings
    last_location: str | None = None
    eta_days: float | None = None
    delayed: bool = False

    attempts: dict[str, int] = Field(default_factory=dict)
    failure_reason: str | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @property
    def lane(self) -> str:
        return f"{self.origin} to {self.destination}"

    @property
    def margin(self) -> float | None:
        if self.quote and self.carrier_rate:
            return round((self.quote - self.carrier_rate) / self.quote, 4)
        return None

    def snapshot(self) -> dict:
        data = self.model_dump(mode="json")
        data["margin"] = self.margin
        data["lane"] = self.lane
        return data
