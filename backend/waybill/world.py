"""
A small, deterministic-ish stand-in for the outside world.

Real freight brokerages talk to rate APIs, load boards, carriers' phones and
ELD/GPS feeds. None of that is available in a portfolio repo, so this module
fakes it with enough texture to be interesting: carriers have hidden price
floors, phones go unanswered, GPS feeds time out, and drivers send messy texts.

Everything flows through one random.Random so a seeded run is reproducible.
"""
import asyncio
import math
import random
import string

from .models import Carrier, Shipment


class TransientError(Exception):
    """Something outside our control hiccuped. Safe to retry."""


# (city, lat, lon). Mostly dealership-heavy metros.
CITIES = [
    ("Oakland, CA", 37.80, -122.27),
    ("Los Angeles, CA", 34.05, -118.24),
    ("Scottsdale, AZ", 33.49, -111.93),
    ("Dallas, TX", 32.78, -96.80),
    ("Houston, TX", 29.76, -95.37),
    ("Denver, CO", 39.74, -104.99),
    ("Seattle, WA", 47.61, -122.33),
    ("Chicago, IL", 41.88, -87.63),
    ("Nashville, TN", 36.16, -86.78),
    ("Knoxville, TN", 35.96, -83.92),
    ("Atlanta, GA", 33.75, -84.39),
    ("Charlotte, NC", 35.23, -80.84),
    ("Miami, FL", 25.76, -80.19),
    ("Newark, NJ", 40.74, -74.17),
]

VEHICLES = [
    ("2024 Mercedes-Benz GLE 450", "enclosed"),
    ("2025 Land Rover Defender 110", "enclosed"),
    ("2023 Cadillac Escalade ESV", "open"),
    ("2024 Cadillac Lyriq", "open"),
    ("2022 Porsche 911 Carrera S", "enclosed"),
    ("2024 BMW X5 xDrive40i", "open"),
    ("2023 Lexus LX 600", "open"),
    ("2024 Genesis GV80", "open"),
    ("2021 Mercedes-AMG G 63", "enclosed"),
    ("2024 Range Rover Sport", "enclosed"),
    ("2023 Chevrolet Tahoe", "open"),
]

CUSTOMERS = [
    "Bayview Motors", "Summit Luxury Imports", "Prestige Auto Group",
    "Lakeside Cadillac", "Westgate Mercedes-Benz", "Crown Land Rover",
    "Ridgeline Motor Club", "Harbor Point Auto", "Copper State Motors",
]

CARRIER_NAMES = [
    "Lone Star Auto Transport", "Blue Ridge Haulers", "Great Lakes Car Carriers",
    "Desert Line Transport", "Iron Horse Logistics", "Bayou Auto Movers",
    "Summit Car Carriers", "Redline Hauling", "Prairie Wind Transport",
    "Coastal Vehicle Express", "Cumberland Auto Freight", "Pacific Crest Carriers",
]

_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # no I, O, Q
_VIN_VALUES = {**{str(d): d for d in range(10)},
               **dict(zip("ABCDEFGH", range(1, 9))),
               **dict(zip("JKLMN", range(1, 6))), "P": 7, "R": 9,
               **dict(zip("STUVWXYZ", range(2, 10)))}
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def make_vin(rng: random.Random) -> str:
    """Random VIN with a valid check digit, because someone will paste one into a decoder."""
    chars = [rng.choice(_VIN_CHARS) for _ in range(17)]
    chars[8] = "0"
    total = sum(_VIN_VALUES[c] * w for c, w in zip(chars, _VIN_WEIGHTS))
    check = total % 11
    chars[8] = "X" if check == 10 else str(check)
    return "".join(chars)


def road_miles(a: tuple, b: tuple) -> int:
    """Great-circle distance with a fudge factor, since trucks can't fly."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[1], a[2], b[1], b[2]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return int(3959 * 2 * math.asin(math.sqrt(h)) * 1.18)


def market_rate(miles: int, trailer: str) -> float:
    """
    Rough car-haul market rate. Short runs cost more per mile, long runs less,
    and enclosed trailers carry a premium. Numbers are in the right neighborhood
    for single-vehicle moves, not pulled from any real index.
    """
    if miles < 500:
        per_mile = 1.55
    elif miles < 1500:
        per_mile = 1.05
    else:
        per_mile = 0.82
    rate = max(375.0, miles * per_mile)
    if trailer == "enclosed":
        rate *= 1.45
    return round(rate, 2)


class World:
    def __init__(self, failure_rate: float = 0.12, sim_speed: float = 1.0, seed: int | None = None):
        self.failure_rate = failure_rate
        self.sim_speed = sim_speed
        self.rng = random.Random(seed)
        self._floors: dict[tuple[str, str], float] = {}
        self._trip: dict[str, dict] = {}

    # ---- helpers -----------------------------------------------------------

    async def pause(self, seconds: float) -> None:
        await asyncio.sleep(seconds / self.sim_speed)

    def _maybe_fail(self, what: str) -> None:
        if self.rng.random() < self.failure_rate:
            raise TransientError(what)

    def new_shipment(self) -> Shipment:
        origin, dest = self.rng.sample(CITIES, 2)
        vehicle, trailer = self.rng.choice(VEHICLES)
        return Shipment(
            customer=self.rng.choice(CUSTOMERS),
            vehicle=vehicle,
            vin=make_vin(self.rng),
            trailer=trailer,
            origin=origin[0],
            destination=dest[0],
            miles=road_miles(origin, dest),
        )

    # ---- rate data ---------------------------------------------------------

    async def get_market_data(self, s: Shipment) -> dict:
        await self.pause(self.rng.uniform(0.3, 0.8))
        self._maybe_fail("rate API timed out")
        base = market_rate(s.miles, s.trailer)
        # Lanes drift a little day to day.
        today = round(base * self.rng.uniform(0.93, 1.08), 2)
        return {
            "lane": s.lane,
            "miles": s.miles,
            "trailer": s.trailer,
            "market_rate": today,
            "per_mile": round(today / s.miles, 2),
            "trucks_posted": self.rng.randint(2, 14),
        }

    # ---- carriers ----------------------------------------------------------

    async def find_carriers(self, s: Shipment) -> list[Carrier]:
        await self.pause(self.rng.uniform(0.3, 0.7))
        self._maybe_fail("load board search failed")
        base = s.market_rate or market_rate(s.miles, s.trailer)
        names = [n for n in CARRIER_NAMES if n not in s.excluded_carriers]
        carriers = []
        for name in self.rng.sample(names, k=min(len(names), self.rng.randint(3, 5))):
            asking = round(base * self.rng.uniform(0.92, 1.25), 0)
            c = Carrier(
                name=name,
                mc_number=str(self.rng.randint(100000, 1299999)),
                rating=round(self.rng.uniform(3.6, 5.0), 1),
                on_time_rate=round(self.rng.uniform(0.78, 0.99), 2),
                asking_rate=asking,
            )
            # The carrier's real walk-away number. The agent never sees this.
            self._floors[(s.id, c.mc_number)] = round(asking * self.rng.uniform(0.84, 0.96), 0)
            carriers.append(c)
        return carriers

    async def call_carrier(self, s: Shipment, carrier: Carrier, offer: float, round_no: int) -> dict:
        """One round of a phone negotiation."""
        await self.pause(self.rng.uniform(0.4, 1.0))
        self._maybe_fail("phone provider returned 503")
        if round_no == 1 and self.rng.random() < 0.15:
            return {"answered": False, "reply": "voicemail"}

        floor = self._floors.get((s.id, carrier.mc_number), carrier.asking_rate * 0.9)
        if offer >= floor:
            return {"answered": True, "reply": "accept", "rate": offer}
        if round_no >= 4:
            return {"answered": True, "reply": "decline"}
        # Counter somewhere between their floor and what they asked, drifting down each round.
        anchor = max(floor, carrier.asking_rate - (carrier.asking_rate - floor) * 0.35 * round_no)
        counter = round(max(floor, (anchor + offer) / 2 + self.rng.uniform(0, 25)), 0)
        return {"answered": True, "reply": "counter", "rate": counter}

    # ---- tracking ----------------------------------------------------------

    async def tracking_update(self, s: Shipment) -> dict:
        """
        Returns a GPS ping (structured, trustworthy) and a driver text
        (unstructured, sometimes useful). The agent has to make sense of both.
        """
        await self.pause(self.rng.uniform(0.6, 1.4))
        self._maybe_fail("GPS feed timed out")

        trip = self._trip.setdefault(s.id, {"progress": 0, "delay_hours": 0})
        if trip["progress"] == 0:
            trip["progress"] = 8
            text = self.rng.choice([
                f"loaded the {s.vehicle.split()[-2]} at {s.origin.split(',')[0]}, heading out",
                f"picked up, vin matches. rolling out of {s.origin.split(',')[0]} now",
                f"got it on the truck. {s.origin.split(',')[0]} pickup done",
            ])
        else:
            step = self.rng.randint(14, 30)
            delayed = self.rng.random() < 0.18 and trip["progress"] < 80
            if delayed:
                step = self.rng.randint(2, 6)
                trip["delay_hours"] += self.rng.choice([6, 12, 24])
                text = self.rng.choice([
                    "weather on I-40, sitting tight a few hrs",
                    "dot inspection at the scale, gonna be late. eta tmrw",
                    "blew a tire on the trailer, shop says couple hours",
                    "other dropoff ran long, behind schedule now",
                ])
            else:
                text = self.rng.choice([
                    "making good time",
                    "on schedule",
                    "fueling up then back on the road",
                    "all good, unit secured",
                    "",
                ])
            trip["progress"] = min(100, trip["progress"] + step)
            if trip["progress"] >= 100:
                text = self.rng.choice([
                    f"delivered, dealer signed the BOL in {s.destination.split(',')[0]}",
                    "dropped off, no damage, pics uploaded",
                    "unit delivered. signed + photos sent",
                ])

        remaining_miles = s.miles * (100 - trip["progress"]) / 100
        eta_days = round(remaining_miles / 450 + trip["delay_hours"] / 24, 1)
        return {
            "gps": {
                "progress": trip["progress"],
                "miles_remaining": int(remaining_miles),
                "near": self._near(s, trip["progress"]),
            },
            "driver_text": text,
            "eta_days": eta_days if trip["progress"] < 100 else 0,
        }

    def _near(self, s: Shipment, progress: int) -> str:
        if progress < 15:
            return s.origin
        if progress >= 100:
            return s.destination
        return f"{int(s.miles * progress / 100)} mi along the {s.lane} route"

    # Mostly for tests: peek at a hidden floor.
    def floor_for(self, shipment_id: str, mc_number: str) -> float | None:
        return self._floors.get((shipment_id, mc_number))
