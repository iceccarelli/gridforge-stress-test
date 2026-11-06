"""Withholding input must never improve the answer: an undeclared equipment bank is unassessed, not "not limiting".

Opt in with `pytest -m product`. Fixture: the SYNTHETIC reference hall (data/fixtures/product). The expectation is an
invariant, not a copy of the product's arithmetic: removing the transformer or UPS schedule cannot make a hall's
time-to-power date earlier or its "complete" capex lower, because the reliefs those banks would have needed are then
neither priced nor timed. A product may satisfy it either way:

  * withhold the date (and say why in `missing_inputs`), or
  * date the scenario no earlier than the declared hall does, naming the bank as missing.

Found 2026-11-06 against GridForge-AI e850311f: with `transformers` and `ups` omitted, `hybrid_dlc` was `dated` at
0.0 weeks where the declared hall was `dated` at 160 weeks, with no mention of the missing banks. Regression: this file
must FAIL on that product commit and pass on a commit that fixes it. It is not xfail'd, because the whole point of an
exact-SHA acceptance run is that a defective commit does not get a green result.
"""

from __future__ import annotations

import copy
import json

import pytest

from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import ProductUnavailable, call, product_dir, product_required

pytestmark = [pytest.mark.integration, pytest.mark.product]

BANKS = {"transformers": "transformers", "ups": "UPS"}


@pytest.fixture(scope="module")
def intake():
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))
    return json.loads((fixtures_dir() / "product" / "reference_hall_intake.json").read_text())


def scenarios(doc):
    out = call("compare", {"intake": doc})
    assert out["ok"], out
    return {s["id"]: s for s in out["result"]["scenarios"]}


def without(intake, *sections):
    doc = copy.deepcopy(intake)
    for s in sections:
        doc.pop(s)
    return doc


def test_the_declared_fixture_gives_dated_scenarios_to_compare_against(intake):
    """Guard against a vacuous pass: the invariant below only bites if the declared hall is dated somewhere."""
    declared = scenarios(intake)
    assert any(s["time_to_power"]["status"] == "dated" for s in declared.values()), {
        k: v["time_to_power"]["status"] for k, v in declared.items()
    }


@pytest.mark.parametrize("omitted", [("transformers",), ("ups",), ("transformers", "ups")])
def test_withholding_a_bank_never_brings_the_date_forward(intake, omitted):
    declared = scenarios(intake)
    sparse = scenarios(without(intake, *omitted))
    named = [BANKS[o] for o in omitted]
    for sid, base in declared.items():
        got = sparse[sid]
        tp, base_tp = got["time_to_power"], base["time_to_power"]
        if tp["status"] != "dated":
            assert tp["weeks"] is None, (sid, tp)  # a withheld date must carry no number at all
            continue
        # dated: no earlier than the declared hall, and the missing bank must be named
        if base_tp["status"] == "dated":
            assert tp["weeks"] >= base_tp["weeks"], (
                f"{sid}: omitting {omitted} brought the date forward from {base_tp['weeks']} to {tp['weeks']} weeks"
            )
        for n in named:
            assert any(n in m for m in got["missing_inputs"]), (
                f"{sid}: dated at {tp['weeks']} weeks with {n} undeclared and not named in missing_inputs: "
                f"{got['missing_inputs']}"
            )


@pytest.mark.parametrize("omitted", [("transformers",), ("ups",), ("transformers", "ups")])
def test_withholding_a_bank_never_lowers_a_capex_stated_as_complete(intake, omitted):
    declared = scenarios(intake)
    sparse = scenarios(without(intake, *omitted))
    for sid, base in declared.items():
        got, base_econ = sparse[sid]["economics"], base["economics"]
        if got["capex_total_eur"] is None or base_econ["capex_total_eur"] is None:
            continue
        if got["capex_complete"]:
            assert got["capex_total_eur"] >= base_econ["capex_total_eur"], (
                f"{sid}: omitting {omitted} lowered a capex presented as complete, "
                f"{base_econ['capex_total_eur']} -> {got['capex_total_eur']}"
            )


def test_a_sparse_screen_is_not_graded_above_the_declared_one(intake):
    order = {"undated": 0, "indicative": 1, "screening": 2}
    declared = scenarios(intake)
    sparse = scenarios(without(intake, "transformers", "ups"))
    for sid, base in declared.items():
        assert order[sparse[sid]["grade"]] <= order[base["grade"]], (
            sid,
            base["grade"],
            sparse[sid]["grade"],
        )


# --- the same invariant on the Density Screen (/v1/screen) and on a diff (/v1/diff) ----------------------------------------
# Found by this harness's independent review: on GridForge-AI e850311f the screen kept `schedule_complete: true` and showed
# 32-56 weeks for a hall with no transformer schedule, and the diff of the declared hall against it headlined "-116 weeks to
# full capacity ... capex moved -3.1 M EUR". Giving a product less data must not read as an improvement.


def screen(doc):
    out = call("screen", {"intake": doc})
    assert out["ok"], out
    return {s["id"]: s for s in out["result"]["scenarios"]}


@pytest.mark.parametrize("omitted", [("transformers",), ("ups",), ("transformers", "ups")])
def test_the_screen_never_brings_a_date_forward_when_a_bank_is_withheld(intake, omitted):
    declared = screen(intake)
    sparse = screen(without(intake, *omitted))
    for sid, base in declared.items():
        got_tp, base_tp = sparse[sid]["time_to_power"], base["time_to_power"]
        got_w, base_w = got_tp["weeks_to_full"], base_tp["weeks_to_full"]
        if got_w is None or base_w is None:
            continue
        assert got_w >= base_w, (
            f"{sid}: weeks_to_full fell from {base_w} to {got_w} with {omitted} withheld"
        )
        # a number that is no worse than declared is fine; one the product itself calls incomplete must carry no weeks
        assert got_tp["schedule_complete"] is not False, (sid, got_tp)


@pytest.mark.parametrize("omitted", [("transformers",), ("ups",), ("transformers", "ups")])
def test_a_complete_critical_path_is_never_shorter_when_a_bank_is_withheld(intake, omitted):
    declared = screen(intake)
    sparse = screen(without(intake, *omitted))
    for sid, base in declared.items():
        got, base_e = sparse[sid]["economics"], base["economics"]
        if (
            got["critical_path_complete"]
            and got["critical_path_weeks"] is not None
            and base_e["critical_path_weeks"]
        ):
            assert got["critical_path_weeks"] >= base_e["critical_path_weeks"], (
                f"{sid}: a path presented as complete fell from {base_e['critical_path_weeks']} to "
                f"{got['critical_path_weeks']} weeks with {omitted} withheld"
            )


@pytest.mark.parametrize("omitted", [("transformers",), ("ups",), ("transformers", "ups")])
def test_a_diff_to_a_sparser_hall_reports_no_earlier_date_and_no_unqualified_saving(
    intake, omitted
):
    out = call("diff", {"before": intake, "after": without(intake, *omitted)})
    assert out["ok"], out
    d = out["result"]
    assert d["weeks_delta"] is None or d["weeks_delta"] >= 0, (
        f"diff reports {d['weeks_delta']} weeks to full capacity for a hall that was only given less data: {d['headline']}"
    )
    delta = d.get("capex_delta_eur")
    if delta is not None and delta < 0:
        assert "lower bound" in d["headline"] or "not a saving" in d["headline"], (
            f"diff presents a capex fall of {delta} from withheld data without qualification: {d['headline']}"
        )
