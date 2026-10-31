"""A battery's firm credit on the hall path (/v1/screen, /v1/compare), against physics. Company PR #114 made the credit depend on stored energy and window.

Independent bound, from energy conservation and nothing the product computes: a battery of P MW and E MWh can add at most min(P, E / h) MW of firm
supply over an h-hour window, so racks can never exceed floor((grid headroom + that) / per-rack kW). Grid headroom comes from the independent oracle;
per-rack kW (rack kW x PUE) is the product's own printed value, a shared input."""

from __future__ import annotations

import copy
import math

import pytest

from gridforge_stress.oracles import _declared, grid_headroom_kw
from gridforge_stress.product_adapter import call_batch

from ._product_helpers import mutated, require_product

pytestmark = [pytest.mark.integration, pytest.mark.product]

P = 2.0


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


def intake(energy=None, window=None, factor=None, power=P, batteries=True):
    def edit(d):
        b = copy.deepcopy(d["btm_options"][0])
        b["capacity_MW"] = power
        for k in ("firm_capacity_factor", "energy_MWh", "ride_through_hours"):
            b.pop(k, None)
        if energy is not None:
            b["energy_MWh"] = energy
        if window is not None:
            b["ride_through_hours"] = window
        if factor is not None:
            b["firm_capacity_factor"] = factor
        d["btm_options"] = [b] if batteries else []

    return mutated(edit)


def screen(d):
    return call_batch(
        [
            {
                "id": "x",
                "route": "/v1/screen",
                "tier": "client",
                "body": {"intake": d},
                "want_result": True,
            }
        ]
    )["x"]


def racks(d):
    r = screen(d)
    assert r["status"] == "ok", r
    s = next(x for x in r["result"]["scenarios"] if x["id"] == "full_dlc_btm")
    rack_kw, pue = _declared(s)
    return s["after_ladder"]["racks"], rack_kw * pue, r["result"]


BASELINE = None


def baseline():
    global BASELINE
    if BASELINE is None:
        BASELINE = racks(intake(batteries=False))
    return BASELINE


ENERGIES = (0.1, 0.5, 2, 8, 32)
WINDOWS = (0.5, 1, 2, 4, 8, 24)


@pytest.mark.parametrize("window", WINDOWS)
@pytest.mark.parametrize("energy", ENERGIES)
def test_racks_never_exceed_what_the_stored_energy_could_sustain(energy, window):
    d = intake(energy, window)
    got, per_rack, _ = racks(d)
    credit_mw = min(P, energy / window)
    ceiling = math.floor((grid_headroom_kw(d) + credit_mw * 1000) / per_rack)
    assert got <= ceiling, (
        f"{got} racks, but {energy} MWh over {window} h supports at most {credit_mw:g} MW: ceiling {ceiling}"
    )


@pytest.mark.parametrize("window", WINDOWS)
def test_a_battery_never_makes_things_worse(window):
    base, _, _ = baseline()
    assert all(racks(intake(e, window))[0] >= base for e in ENERGIES)


def test_more_stored_energy_never_hurts_and_the_sweep_moves():
    got = [racks(intake(e, 4))[0] for e in ENERGIES]
    assert got == sorted(got), dict(zip(ENERGIES, got, strict=True))
    assert len(set(got)) >= 4, f"the sweep must actually move, else monotonicity is vacuous: {got}"


def test_a_longer_window_never_helps_and_the_sweep_moves():
    got = [racks(intake(8, h))[0] for h in WINDOWS]
    assert got == sorted(got, reverse=True), dict(zip(WINDOWS, got, strict=True))
    assert len(set(got)) >= 3, got


@pytest.mark.parametrize("window", [4, None])
def test_a_five_minute_battery_is_not_credited_like_an_eight_hour_one(window):
    """The bug PR #114 names: a 2 MW / 0.1 MWh battery (about three minutes at full power) was credited exactly as a 2 MW / 8 MWh one.
    With a declared window the engine already got this right; the undeclared-window case is the one that changed."""
    tiny, big = racks(intake(0.1, window))[0], racks(intake(8, window))[0]
    assert tiny < big, (tiny, big)
    assert tiny <= baseline()[0] + 3, (
        "a battery that holds 0.1 MWh cannot be credited with a rack-hall's worth of firm supply"
    )


def test_an_undeclared_window_never_raises_the_credit_above_the_flat_default():
    """PR #114's stated rule: with energy but no window the credit is the LESSER of the flat default and what the energy supports, so an assumed window cannot raise it."""
    flat = racks(intake(None, None))[0]  # no energy, no window: the flat library factor
    assert all(racks(intake(e, None))[0] <= flat for e in ENERGIES), (
        flat,
        [racks(intake(e, None))[0] for e in ENERGIES],
    )
    assert racks(intake(0.1, None))[0] < flat, (
        "a tiny battery with no window must be credited less than the flat default"
    )


def test_a_factor_that_needs_more_energy_than_is_stored_is_refused_and_the_boundary_is_not():
    """P=2 MW x factor 0.5 = 1 MW for 4 h needs 4 MWh. 1 MWh stored: impossible. Exactly 4 MWh: allowed. Just under: refused."""
    refused = screen(intake(1, 4, 0.5))
    assert refused["status"] == "clean" and refused["error"] == "IntakeError", refused
    assert "stores only" in refused["message"] or "contradict" in refused["message"]
    assert screen(intake(4, 4, 0.5))["status"] == "ok"
    assert screen(intake(3.99, 4, 0.5))["status"] == "clean"


def test_a_declared_factor_with_enough_energy_is_kept_as_declared():
    """The customer's own figure outranks the library's, so more declared credit gives at least as many racks."""
    low, high = racks(intake(32, 4, 0.1))[0], racks(intake(32, 4, 0.9))[0]
    assert low <= high and low < high, (low, high)


def test_every_answer_here_is_a_controlled_answer():
    """No combination of energy and window escapes as anything but ok or a clean refusal (never a server fault)."""
    cases = [
        {
            "id": f"{e}|{w}",
            "route": "/v1/screen",
            "tier": "client",
            "body": {"intake": intake(e, w)},
        }
        for e in (0.001, 1e6)
        for w in (0.001, 1e4)
    ]
    assert {v["status"] for v in call_batch(cases).values()} <= {"ok", "clean"}
