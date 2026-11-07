"""P1-3: equipment the product itself calls "assumed, not installed" must not be dated at week 0. Opt in with `pytest -m product`.

Base behaviour (verified on GridForge-AI b649b3e7): with `thermal.cdus` omitted or `[]`, the product silently assumes three 1,350 kW N+1
CDUs (`installed=False`, "assumed, not installed"), PRICES them (/v1/screen economics.capex_components.initial_cdu_kit_eur = EUR 540,000 for
3 CDU units) and then dates the racks they carry at week 0: on a hall whose busway, not the cooling, limits (`ample_hall(1000, 630)`),
/v1/compare returns `hybrid_dlc` with 10 racks as found, `first_rack_weeks` 0.0, and /v1/screen's energisation curve opens with
{weeks 0.0, racks 10, unlocked_by "available as found"}. The CDU relief the same ladder takes elsewhere carries a 26-week lead time. A hall
that declares the same three CDUs gets the identical answer, so withholding the schedule is indistinguishable from owning the kit. Also
verified: a CDU bank declared with `"installed": false` is silently read as installed (the loader never reads the key) and dated at week 0.

Invariants (not the product's arithmetic):
  1. A week-0 point requires installed equipment: for a hall with no declared installed CDU schedule (absent, [] or "installed": false) no
     liquid scenario may have first_rack_weeks == 0.0 and /v1/screen's curve may not open with racks at week 0; an unsupported
     "installed" key may instead be REFUSED by name.
  2. What the product charges money to buy cannot be energised before it is bought.
  3. Withdrawing the week-0 claim must not withdraw the capacity: with the schedule withheld the scenario still reaches at least the racks
     of the declared-installed hall after the ladder, a dated answer is no earlier than the declared hall's and its first rack no earlier than
     the CDU relief's own lead time, and the liquid scenarios still host racks.
Control arm (satisfied on the base product): a hall that declares installed CDUs keeps its week-0 point in compare AND its screen curve
opens with the as-found racks at week 0, and it is not charged a kit. Not xfail'd.
"""

from __future__ import annotations

from typing import Any

import pytest

from ._common import (
    LIQUID_SCENARIOS,
    ample_hall,
    answered_many,
    mutated,
    run_many,
    scenarios,
)

pytestmark = [pytest.mark.integration, pytest.mark.product]

INSTALLED_BANK = [{"id": "CDU-ROW-1", "rated_capacity_kW": 1350, "units": 3, "redundancy": "N+1"}]
NOT_INSTALLED_BANK = [{**INSTALLED_BANK[0], "installed": False}]
KINDS = ["absent", "empty", "declared-installed"]


def _hall(kind: str) -> dict[str, Any]:
    doc = ample_hall(
        1000, 630
    )  # busway 1000 A, tap-off 630 A: the LV chain is not what decides the cooling question
    if kind == "absent":
        return mutated(doc, lambda d: d["thermal"].pop("cdus", None))
    if kind == "empty":
        return mutated(doc, lambda d: d["thermal"].__setitem__("cdus", []))
    if kind == "declared-not-installed":
        return mutated(doc, lambda d: d["thermal"].__setitem__("cdus", NOT_INSTALLED_BANK))
    return mutated(doc, lambda d: d["thermal"].__setitem__("cdus", INSTALLED_BANK))


@pytest.fixture(scope="module")
def results(intake: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases: dict[str, Any] = {}
    for kind in KINDS:
        cases[f"{kind}|compare"] = ("compare", {"intake": _hall(kind)})
        cases[f"{kind}|screen"] = ("screen", {"intake": _hall(kind)})
    got = answered_many(cases)
    return {k: {r: scenarios(got[f"{k}|{r}"]) for r in ("compare", "screen")} for k in KINDS}


@pytest.fixture(scope="module")
def declared_not_installed(intake: dict[str, Any]) -> dict[str, dict[str, Any]]:
    doc = _hall("declared-not-installed")
    return run_many(
        {"compare": ("compare", {"intake": doc}), "screen": ("screen", {"intake": doc})}
    )


def _first_rack_weeks_ok(s: dict[str, Any]) -> bool:
    tp = s["time_to_power"]
    return tp["status"] != "dated" or tp["first_rack_weeks"] != 0.0


@pytest.mark.parametrize("kind", KINDS)
def test_a_dated_week_zero_point_requires_installed_cdus(results, kind):
    installed = kind == "declared-installed"
    for sid in LIQUID_SCENARIOS:
        s = results[kind]["compare"][sid]
        if installed:
            continue
        assert _first_rack_weeks_ok(s), (
            f"{sid}: no CDU schedule was declared (thermal.cdus {kind}) yet {s['as_found']['racks']} racks are dated at "
            f"first_rack_weeks=0.0 ({s['time_to_power']['date_label']!r}); they rest on CDUs the engine assumes (installed=False) "
            "and elsewhere gives a 26-week lead time"
        )


def test_a_bank_declared_not_installed_is_refused_or_not_dated_at_week_zero(declared_not_installed):
    """`installed: false` on a CDU bank is either honoured (no week-0 point) or refused by name; it is not silently read as installed."""
    for route in ("compare", "screen"):
        row = declared_not_installed[route]
        if row["status"] == "clean":
            assert "installed" in row["message"], (
                f"/v1/{route} refused without naming the key: {row['message']!r}"
            )
            continue
        assert row["status"] == "ok", row
        for sid, s in scenarios(row["result"]).items():
            if sid not in LIQUID_SCENARIOS:
                continue
            if route == "compare":
                assert _first_rack_weeks_ok(s), (
                    f"{sid}: a CDU bank declared installed=false is dated at first_rack_weeks=0.0 with {s['as_found']['racks']} racks"
                )
            else:
                zero = [
                    p for p in s["time_to_power"]["curve"] if p["weeks"] == 0.0 and p["racks"] > 0
                ]
                assert not zero, (
                    f"{sid}: a bank declared installed=false opens the curve at week 0 with {zero[0]['racks']} racks"
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
        assert not (kit_eur and week_zero), (
            f"{sid}: the product charges EUR {kit_eur:,.0f} for an initial CDU kit "
            f"({s['economics']['capex_components'].get('initial_cdu_kit_scope')}) and still has "
            f"{week_zero[0]['racks']} racks energised at week 0"
        )


@pytest.mark.parametrize("kind", ["absent", "empty"])
def test_withdrawing_the_week_zero_claim_keeps_the_capacity_and_dates_it_after_the_cdu_lead_time(
    results, kind
):
    declared = results["declared-installed"]["compare"]
    withheld = results[kind]["compare"]
    cdu_lead = min(
        lt["lead_time_weeks"]["value"]
        for s in declared.values()
        for lt in s["time_to_power"]["lead_times"]
        if lt["constraint"] == "cdu_capacity"
    )
    hosting = 0
    for sid in LIQUID_SCENARIOS:
        got, ref = withheld[sid], declared[sid]
        assert got["after_ladder"]["racks"] >= ref["after_ladder"]["racks"], (
            f"{sid}: with the CDU schedule withheld the scenario ends at {got['after_ladder']['racks']} racks after the ladder, "
            f"the declared-installed hall at {ref['after_ladder']['racks']}: the week-0 claim was withdrawn by removing capacity"
        )
        hosting += got["after_ladder"]["racks"] > 0
        tp, ref_tp = got["time_to_power"], ref["time_to_power"]
        if tp["status"] != "dated":
            assert tp["weeks"] is None and tp["first_rack_weeks"] is None, (sid, tp)
            continue
        assert ref_tp["status"] == "dated", (sid, ref_tp)
        assert tp["weeks"] >= ref_tp["weeks"], (sid, tp["weeks"], ref_tp["weeks"])
        assert tp["first_rack_weeks"] is not None and tp["first_rack_weeks"] >= cdu_lead, (
            f"{sid}: first rack at {tp['first_rack_weeks']} weeks, before the {cdu_lead}-week lead time of the CDU relief "
            "the assumed kit would have needed"
        )
    assert hosting, (
        "no liquid scenario hosts any rack after the ladder with the CDU schedule withheld"
    )


def test_control_declared_installed_cdus_keep_a_week_zero_point_and_no_kit_charge(results):
    """The path the invariants above are written around is real: installed CDUs may carry racks at week 0, and are not priced as bought."""
    hybrid = results["declared-installed"]["compare"]["hybrid_dlc"]
    assert hybrid["as_found"]["racks"] > 0
    assert hybrid["time_to_power"]["status"] == "dated"
    assert hybrid["time_to_power"]["first_rack_weeks"] == 0.0
    screen = results["declared-installed"]["screen"]["hybrid_dlc"]
    assert not screen["economics"]["capex_components"].get("initial_cdu_kit_eur")
    curve = screen["time_to_power"]["curve"]
    assert curve, "the screen's energisation curve is empty for a hall with installed CDUs"
    assert curve[0]["weeks"] == 0.0 and curve[0]["racks"] == screen["as_found"]["racks"], curve[:2]
