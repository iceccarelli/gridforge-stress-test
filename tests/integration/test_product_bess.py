"""BESS power vs energy vs duration on the real /v1/power/deploy/assess. Physical bounds and monotonicity only: no product constant is assumed.

A battery with enough MW but too little MWh must not be credited for a long ride-through (TTP-P007)."""

from __future__ import annotations

import pytest

from gridforge_stress.oracles import bess_bound_violations

from ._product_helpers import require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]

GRID = 8.0


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


def assess(*, power=2.0, energy=8.0, hours=4.0, target=10.0):
    body = {
        "load_profile": {"flat_kW": 10000, "flat_hours": 24},
        "grid_firm_MW": GRID,
        "target_MW": target,
        "bess": [{"id": "B", "power_MW": power, "energy_MWh": energy}],
    }
    if hours is not None:
        body["ride_through_hours"] = hours
    out = run("/v1/power/deploy/assess", body)
    arch = next(a for a in out["architectures"] if a["label"] == "GRID + BESS")
    return arch, out


HOURS = (0.5, 1, 2, 4, 6, 12, 24)


@pytest.mark.parametrize("hours", HOURS)
def test_no_physical_bound_is_violated(hours):
    arch, _ = assess(hours=hours)
    assert not bess_bound_violations(
        grid_firm_mw=GRID,
        power_mw=2.0,
        energy_mwh=8.0,
        ride_through_h=hours,
        available_mw=arch["available_MW"],
    )


def test_a_longer_ride_through_never_helps():
    got = [assess(hours=h)[0]["available_MW"] for h in HOURS]
    assert got == sorted(got, reverse=True), dict(zip(HOURS, got, strict=True))
    assert len(set(got)) > 2, f"the sweep must actually move, else monotonicity is vacuous: {got}"


def test_more_stored_energy_never_hurts():
    energies = (0.5, 2, 8, 32, 128)
    got = [assess(energy=e)[0]["available_MW"] for e in energies]
    assert got == sorted(got), dict(zip(energies, got, strict=True))
    assert len(set(got)) > 2, f"the sweep must actually move, else monotonicity is vacuous: {got}"


def test_more_power_never_hurts_at_fixed_energy():
    powers = (0.5, 1, 2, 4, 8)
    got = [assess(power=p)[0]["available_MW"] for p in powers]
    assert got == sorted(got), dict(zip(powers, got, strict=True))
    assert len(set(got)) > 2, f"the sweep must actually move, else monotonicity is vacuous: {got}"


def test_enough_mw_but_too_little_mwh_is_not_credited_for_the_duration():
    """20 MW for 4 h would need 80 MWh; the battery holds 1. It cannot close a 2 MW gap, so the target must not pass."""
    arch, _ = assess(power=20.0, energy=1.0, hours=4.0)
    assert arch["available_MW"] <= GRID + 1.0 / 4.0 + 1e-6
    assert arch["status"] != "pass"


def test_the_same_battery_is_credited_when_the_duration_is_short_enough():
    """Guards against a product (or a test) that simply refuses batteries: 20 MW x 0.05 h = 1 MWh is within the 1 MWh stored."""
    arch, _ = assess(power=20.0, energy=1.0, hours=0.05, target=10.0)
    assert arch["available_MW"] > GRID + 0.5


def test_an_undeclared_ride_through_is_not_a_pass():
    arch, out = assess(hours=None)
    assert out["capacity"]["ride_through_declared"] is False
    assert arch["status"] != "pass"
