import random

from waybill.world import CITIES, make_vin, market_rate, road_miles, _VIN_VALUES, _VIN_WEIGHTS


def test_vin_check_digit_is_valid():
    rng = random.Random(0)
    for _ in range(200):
        vin = make_vin(rng)
        assert len(vin) == 17 and not set("IOQ") & set(vin)
        total = sum(_VIN_VALUES[c] * w for c, w in zip(vin, _VIN_WEIGHTS))
        expected = "X" if total % 11 == 10 else str(total % 11)
        assert vin[8] == expected


def test_enclosed_costs_more_and_short_runs_cost_more_per_mile():
    assert market_rate(1000, "enclosed") > market_rate(1000, "open")
    assert market_rate(300, "open") / 300 > market_rate(2000, "open") / 2000
    assert market_rate(50, "open") == 375.0  # minimum charge


def test_road_miles_ballpark():
    oakland, la = CITIES[0], CITIES[1]
    assert 380 < road_miles(oakland, la) < 500
