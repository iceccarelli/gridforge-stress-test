"""P1-3: equipment the product itself calls "assumed, not installed" must not be dated at week 0. Opt in with `pytest -m product`.

Defect (verified on GridForge-AI b649b3e7): with `thermal.cdus` omitted or `[]`, the product silently assumes three 1,350 kW N+1 CDUs
(`installed=False`, "assumed, not installed"), PRICES them (/v1/screen economics.capex_components.initial_cdu_kit_eur = EUR 540,000 for 3 CDU
units) and then dates the racks they carry at week 0: on a hall where power is not limiting, /v1/compare returns `hybrid_dlc` with
as_found racks > 0, `first_rack_weeks` 0.0, and /v1/screen's energisation curve opens with {weeks 0.0, unlocked_by "available as found"}.
The CDU relief the same ladder takes elsewhere carries a 26-week lead time. A hall that declares the same three CDUs gets the identical
answer, so withholding the schedule is indistinguishable from owning the kit.

Invariants (not the product's arithmetic):
  1. A week-0 point requires installed equipment: for a hall with no declared CDU schedule, no liquid scenario may have first_rack_weeks == 0.0
     (it must be withheld, or later than the declared-and-installed hall), and /v1/screen's curve may not open with "available as found".
  2. What the product charges money to buy cannot be energised before it is bought: a scenario that prices an initial CDU kit may not date
     racks at week 0.
Control arm (satisfied on the base product): a hall that declares installed CDUs may legitimately have a week-0 point and is not charged a kit.
Not xfail'd.
"""

from __future__ import annotations

from typing import Any

import pytest

from ._common import LIQUID_SCENARIOS, ample_hall, answered, mutated, scenarios

pytestmark = [pytest.mark.integration, pytest.mark.product]

INSTALLED_BANK = [{"id": "CDU-ROW-1", "rated_capacity_kW": 1350, "units": 3, "redundancy": "N+1"}]


def _hall(kind: str) -> dict[str, Any]:
    doc = ample_hall(
        1000, 630
    )  # busway 1000 A, tap-off 630 A: the LV chain is not what decides the cooling question
    if kind == "absent":
        return mutated(doc, lambda d: d["thermal"].pop("cdus", None))
    if kind == "empty":
        return mutated(doc, lambda d: d["thermal"].__setitem__("cdus", []))
    return mutated(doc, lambda d: d["thermal"].__setitem__("cdus", INSTALLED_BANK))


KINDS = ["absent", "empty", "declared-installed"]


@pytest.fixture(scope="module")
def results(intake: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        doc = _hall(kind)
        out[kind] = {
            "compare": scenarios(answered("compare", doc)),
            "screen": scenarios(answered("screen", doc)),
        }
    return out


@pytest.mark.parametrize("kind", KINDS)
def test_a_dated_week_zero_point_requires_installed_cdus(results, kind):
    installed = kind == "declared-installed"
    for sid in LIQUID_SCENARIOS:
        s = results[kind]["compare"][sid]
        tp = s["time_to_power"]
        if tp["status"] != "dated" or tp["first_rack_weeks"] != 0.0:
            continue  # no week-0 claim: nothing to check (withholding the date is an acceptable fix)
        assert installed, (
            f"{sid}: no CDU schedule was declared (thermal.cdus {kind}) yet {s['as_found']['racks']} racks are dated at "
            f"first_rack_weeks=0.0 ({tp['date_label']!r}); they rest on CDUs the engine assumes (installed=False) and elsewhere "
            "gives a 26-week lead time"
        )


@pytest.mark.parametrize("kind", ["absent", "empty"])
def test_the_screen_curve_does_not_open_with_available_as_found_on_assumed_cdus(results, kind):
    for sid in LIQUID_SCENARIOS:
        curve = results[kind]["screen"][sid]["time_to_power"]["curve"]
        week_zero = [p for p in curve if p["weeks"] == 0.0 and p["racks"] > 0]
        assert not week_zero, (
            f"{sid}: no CDU schedule was declared, yet the energisation curve starts with {week_zero[0]['racks']} racks "
            f"at week 0 ({week_zero[0]['unlocked_by']!r})"
        )


@pytest.mark.parametrize("kind", KINDS)
def test_equipment_the_product_prices_as_bought_is_not_energised_at_week_zero(results, kind):
    for sid in LIQUID_SCENARIOS:
        s = results[kind]["screen"][sid]
        kit = s["economics"]["capex_components"].get("initial_cdu_kit_eur")
        kit_eur = kit["value"] if isinstance(kit, dict) else None
        week_zero = [p for p in s["time_to_power"]["curve"] if p["weeks"] == 0.0 and p["racks"] > 0]
        if kit_eur and week_zero:
            raise AssertionError(
                f"{sid}: the product charges EUR {kit_eur:,.0f} for an initial CDU kit "
                f"({s['economics']['capex_components'].get('initial_cdu_kit_scope')}) and still has "
                f"{week_zero[0]['racks']} racks energised at week 0"
            )


def test_control_declared_installed_cdus_keep_a_week_zero_point_and_no_kit_charge(results):
    """The path the invariants above are written around is real: installed CDUs may carry racks at week 0, and are not priced as bought."""
    hybrid = results["declared-installed"]["compare"]["hybrid_dlc"]
    assert hybrid["as_found"]["racks"] > 0
    assert hybrid["time_to_power"]["status"] == "dated"
    assert hybrid["time_to_power"]["first_rack_weeks"] == 0.0
    screen = results["declared-installed"]["screen"]["hybrid_dlc"]
    assert not screen["economics"]["capex_components"].get("initial_cdu_kit_eur")
