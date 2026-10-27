from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gridforge_stress.ercot import ercot_status
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.scenarios_data import (
    rank_stability,
    validate_cambium_record,
    validate_tariff,
    validate_weather,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def tariff():
    return json.loads((fixtures_dir() / "tariff_synthetic.json").read_text())


def test_ercot_status_never_claims_live_access():
    s = ercot_status({})
    assert not s["can_fetch_live"] and s["credentials_missing"]
    full = ercot_status({k: "x" for k in s["credentials_missing"]})
    assert not full["can_fetch_live"], "credentials alone must not unblock a blocked source"


def good_cambium():
    return {
        "scenario_id": "s",
        "model_release": "r",
        "region": "g",
        "resolution": "hourly",
        "kind": "modeled",
        "units": {"energy_cost": "$/MWh", "emissions": "kg/MWh"},
    }


def test_cambium_must_be_labelled_modeled_with_units():
    assert validate_cambium_record(good_cambium()) == []
    bad = good_cambium() | {"kind": "observed"}
    assert any(f.code == "mislabeled" for f in validate_cambium_record(bad))
    bad = good_cambium() | {"units": {"energy_cost": "$/MW", "emissions": "kg/MWh"}}
    assert any(f.code == "units" for f in validate_cambium_record(bad))


def test_rank_stability_detects_assumption_sensitivity():
    stable = rank_stability({"a": {"x": 1, "y": 2}, "b": {"x": 1.5, "y": 3}})
    flip = rank_stability({"a": {"x": 1, "y": 2}, "b": {"x": 4, "y": 3}})
    assert stable["stable"] and not flip["stable"] and "modeled" in flip["label"]


def test_good_tariff_and_defects():
    assert validate_tariff(tariff()) == []
    t = tariff()
    t["effective_end"] = "2025-01-01"
    assert {f.code for f in validate_tariff(t)} == {"date_order"}
    t = tariff()
    t["energy_rates_usd_per_kwh"] = [-1]
    assert "energy_rates" in {f.code for f in validate_tariff(t)}
    t = tariff()
    t["tou_weekday"][0][0] = 9
    assert "tou_period" in {f.code for f in validate_tariff(t)}
    t = tariff()
    t["tou_weekday"] = t["tou_weekday"][:11]
    assert "tou_shape" in {f.code for f in validate_tariff(t)}
    t = tariff()
    t["customer_class"] = ""
    assert "missing_field" in {f.code for f in validate_tariff(t)}
    t = tariff()
    t["demand_charge_usd_per_kw"] = -3
    assert "demand_charge" in {f.code for f in validate_tariff(t)}


def test_weather_kinds_staleness_future_and_missing():
    ok = {"kind": "observation", "timestamp": "2026-05-31T23:00:00Z", "temperature_c": 20}
    assert validate_weather(ok, as_of=NOW, max_age=timedelta(hours=3)) == []
    old = ok | {"timestamp": "2026-05-30T00:00:00Z"}
    assert any(
        f.code == "stale" for f in validate_weather(old, as_of=NOW, max_age=timedelta(hours=3))
    )
    fut = ok | {"timestamp": "2026-06-02T00:00:00Z"}
    assert any(
        f.code == "future_observation"
        for f in validate_weather(fut, as_of=NOW, max_age=timedelta(hours=3))
    )
    assert any(
        f.code == "kind"
        for f in validate_weather(ok | {"kind": "guess"}, as_of=NOW, max_age=timedelta(hours=3))
    )
    assert any(
        f.code == "missing_value"
        for f in validate_weather(
            ok | {"temperature_c": None}, as_of=NOW, max_age=timedelta(hours=3)
        )
    )
