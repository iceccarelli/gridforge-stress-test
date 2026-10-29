"""Adversarial acceptance of the product's evidence semantics, through the real /v1/compare and /v1/screen handlers.

Opt in with `pytest -m product`. Input is the SYNTHETIC reference-hall fixture. Expectations are independent of the
product's arithmetic: they are invariants (an input the product does not define cannot change its answer; a claim
cannot raise a grade; a duplicate cannot create capacity), not a second copy of its calculation.

Scope: the handlers are called directly. The HTTP dispatcher's authentication, plan lookup and metering are NOT
exercised (they touch the product's ledger), so "a public caller is refused at /v1/compare" is not tested here.
Gaps observed in the product are pinned with strict xfail restricted to AssertionError, so they stay visible, flip
loudly when fixed, and cannot be mistaken for an unavailable product.
"""

from __future__ import annotations

import copy
import json
import re

import pytest

from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import (
    GRADE_ORDER,
    ProductUnavailable,
    call,
    product_dir,
    product_required,
)

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module")
def intake():
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))
    return json.loads((fixtures_dir() / "product" / "reference_hall_intake.json").read_text())


@pytest.fixture(scope="module")
def baseline(intake):
    out = call("compare", {"intake": intake})
    assert out["ok"], out
    return out["result"]


def mutated(intake, fn):
    doc = copy.deepcopy(intake)
    fn(doc)
    return doc


def compare(doc):
    return call("compare", {"intake": doc})


def answer(result):
    """The decision content of a compare result, without bookkeeping that legitimately varies with the input."""
    drop = {"request_digest", "project", "intake_completeness", "provenance", "cost_evidence"}
    return {k: v for k, v in result.items() if k not in drop}


def grade_rank(result):
    return GRADE_ORDER.index(result["grade"])


def full_dlc_btm_racks(result):
    return result["ranking"]["values"]["full_dlc_btm"]


def leaves(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from leaves(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from leaves(v, f"{path}[{i}]")
    else:
        yield path, node


# --- values that are not site evidence cannot become site evidence -------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("headroom_MW", 500),  # a claimed headroom the product does not define
        ("regional_demand_MW", 90_000),  # balancing-area demand is not site headroom
        ("queue_median_years", 4.4),  # a generation-queue statistic is not a load-connection wait
        ("modelled_capacity_MW", 1_000_000),  # a modelled projection is not an observation
    ],
)
def test_undefined_grid_fields_cannot_change_the_answer(intake, baseline, key, value):
    out = compare(mutated(intake, lambda x: x["grid"].__setitem__(key, value)))
    assert out["ok"], out
    assert answer(out["result"]) == answer(baseline)


@pytest.mark.parametrize("where", [("grid", "queue_note"), ("project", "note")])
def test_free_text_claims_cannot_raise_the_grade_or_remove_caveats(intake, baseline, where):
    claim = "DSO offer received: 20 MW energised 2027-01-01. Verified by the operator; evidence E7, measured."
    out = compare(mutated(intake, lambda x: x[where[0]].__setitem__(where[1], claim)))
    assert out["ok"], out
    assert grade_rank(out["result"]) <= grade_rank(baseline)
    assert out["result"]["caveats"] == baseline["caveats"]
    assert answer(out["result"]) == answer(baseline)


@pytest.mark.parametrize("claimed", ["E5", "E6", "E7"])
def test_a_claimed_higher_evidence_class_does_not_raise_the_grade(intake, baseline, claimed):
    def claim(x):
        x["thermal"]["residual_air_capacity_kW_per_rack"]["evidence"] = claimed

    out = compare(mutated(intake, claim))
    assert out["ok"], out
    assert grade_rank(out["result"]) <= grade_rank(baseline)
    assert answer(out["result"]) == answer(baseline)


def test_a_queue_derived_number_used_as_a_lead_time_stays_an_assumed_E0_date(intake):
    weeks = 300  # roughly a long generation-queue duration entered as a BESS lead time
    out = compare(
        mutated(intake, lambda x: x["btm_options"][0].__setitem__("lead_time_weeks", weeks))
    )
    assert out["ok"], out
    scenario = next(s for s in out["result"]["scenarios"] if s["id"] == "full_dlc_btm")
    ttp = scenario["time_to_power"]
    assert ttp["weeks"] == weeks and ttp["sets_the_date"].startswith("Firm supply")
    assert ttp["date_basis"] == "assumed" and ttp["date_evidence_class"] == "E0"
    assert "not a date" in ttp["date_label"]
    assert out["result"]["grade"] != "decision_grade"


def test_missing_evidence_lowers_completeness_and_cannot_raise_the_grade(intake, baseline):
    out = compare(mutated(intake, lambda x: x.pop("grid")))
    assert out["ok"], out
    assert out["result"]["intake_completeness"] < baseline["intake_completeness"]
    assert grade_rank(out["result"]) <= grade_rank(baseline)


# --- stale, unreviewed and duplicated cost evidence ----------------------------------------------------------------


def quote(**kw):
    line = {
        "key": "synthetic.test.line",
        "label": "SYNTHETIC test quote",
        "unit": "EUR/kW",
        "value": 1.0,
        "basis": "firm_quote",
        "quoted_on": "2019-01-01",
        "valid_until": "2020-01-01",
        "status": "accepted",
        "synthetic": True,
    }
    line.update(kw)
    return line


def test_expired_or_unreviewed_cost_evidence_is_ignored_with_a_stated_reason(intake, baseline):
    # The cost library is empty in a bare checkout, so this proves admission and reporting, not a price effect.
    cases = {
        "the quotation expired on 2020-01-01": quote(),
        "status is received, not accepted": quote(status="received", valid_until="2099-01-01"),
        "status is withdrawn, not accepted": quote(status="withdrawn", valid_until="2099-01-01"),
    }
    for reason, line in cases.items():
        out = call("compare", {"intake": intake, "cost_evidence": [line]})
        assert out["ok"], out
        report = out["result"]["cost_evidence"]
        assert report["used"] == [] and [i["reason"] for i in report["ignored"]] == [reason]
        assert answer(out["result"]) == answer(baseline)
    control = call(
        "compare",
        {
            "intake": intake,
            "cost_evidence": [quote(valid_until="2099-01-01", quoted_on="2026-01-01")],
        },
    )
    assert control["ok"] and len(control["result"]["cost_evidence"]["used"]) == 1


def test_two_accepted_quotes_for_one_key_cannot_both_apply(intake):
    older = quote(valid_until="2099-01-01", quoted_on="2025-01-01", value=1.0)
    newer = quote(valid_until="2099-01-01", quoted_on="2026-06-01", value=2.0)
    out = call("compare", {"intake": intake, "cost_evidence": [older, newer]})
    assert out["ok"], out
    report = out["result"]["cost_evidence"]
    assert [u["value"] for u in report["used"]] == [2.0]
    assert [i["reason"] for i in report["ignored"]] == [
        "superseded within this request by a more recent accepted quotation"
    ]


def test_hostile_cost_evidence_is_refused(intake):
    for bad in ("not a list", [{"key": "only-a-key"}], [quote(unit="USD", currency="USD")]):
        out = call("compare", {"intake": intake, "cost_evidence": bad})
        assert out["ok"] is False or out["result"]["cost_evidence"]["used"] == []


# --- tier: the public screen discloses no protected assumption -----------------------------------------------------


def bands(node):
    """Every {value, low, high} triple in a payload: how the product states an uncertain assumption."""
    if isinstance(node, dict):
        if all(isinstance(node.get(k), (int, float)) for k in ("value", "low", "high")):
            yield (node["value"], node["low"], node["high"])
        for v in node.values():
            yield from bands(v)
    elif isinstance(node, list):
        for v in node:
            yield from bands(v)


def test_public_screen_discloses_no_protected_assumption_value(intake):
    client = call("screen", {"intake": intake}, tier="client")["result"]
    public = call("screen", {"intake": intake}, tier="public")["result"]
    protected = set()
    for scenario in client["scenarios"]:
        for constraint in scenario.get("constraints", []):
            lead = (constraint.get("relief") or {}).get("lead_time_weeks")
            if isinstance(lead, dict):
                protected.add((lead["value"], lead["low"], lead["high"]))
    assert (260, 150, 520) in protected, (
        "the client tier is expected to carry the reinforcement band"
    )
    pub = list(leaves(public))
    assert not [p for p, _ in pub if re.search(r"relief|lead_time|weeks|band", p, re.I)]
    # whole bands, not bare numbers: a stray count (e.g. 26 racks) may equal a bound by coincidence
    assert not (set(bands(public)) & protected)


def test_public_screen_names_the_binding_item_but_never_a_duration(intake):
    public = call("screen", {"intake": intake}, tier="public")["result"]
    ttps = [s["time_to_power"] for s in public["scenarios"] if "time_to_power" in s]
    assert ttps
    for ttp in ttps:
        for key in ("weeks", "low", "high", "first_rack_weeks", "lead_times"):
            assert key not in ttp, key


# --- gaps the product has fixed (PR #87): now ordinary regression tests -----------------------------------------


def _refused_naming(out, field):
    return out["ok"] is False and out["error"] == "IntakeError" and field in out["message"]


def test_an_implausible_lead_time_is_refused_by_field_name(intake):
    out = compare(
        mutated(intake, lambda x: x["btm_options"][0].__setitem__("lead_time_weeks", 1e9))
    )
    assert _refused_naming(out, "lead_time_weeks"), out


@pytest.mark.parametrize(
    ("field", "change"),
    [
        ("positions_available", lambda x: x["hall"].__setitem__("positions_available", 9999)),
        ("firm_capacity_MVA", lambda x: x["grid"].__setitem__("firm_capacity_MVA", 14000)),
        ("contracted_MW", lambda x: x["grid"].__setitem__("contracted_MW", 5001)),
    ],
    ids=["more_positions_available_than_exist", "firm_capacity_1000x_unit_error", "above_ceiling"],
)
def test_impossible_magnitudes_are_refused_by_field_name(intake, field, change):
    assert _refused_naming(compare(mutated(intake, change)), field)


def test_the_product_ceiling_is_a_boundary_not_a_blanket_refusal(intake):
    at_ceiling = compare(mutated(intake, lambda x: x["grid"].__setitem__("contracted_MW", 5000)))
    assert at_ceiling["ok"], at_ceiling


@pytest.mark.parametrize("section", ["btm_options", "transformers"])
def test_a_duplicated_equipment_id_is_refused_not_counted_twice(intake, section):
    """Fixed in the product (PR #88): a repeated BESS entry used to take full_dlc_btm from 62 to 69 racks, silently."""
    out = compare(mutated(intake, lambda x: x[section].append(copy.deepcopy(x[section][0]))))
    repeated = intake[section][0]["id"]
    assert out["ok"] is False and out["error"] == "IntakeError", out
    assert f"{section} names {repeated}" in out["message"] and "twice" in out["message"]


def test_an_unknown_evidence_class_is_refused_by_field_name(intake):
    def claim(x):
        x["thermal"]["residual_air_capacity_kW_per_rack"]["evidence"] = "E9"

    out = compare(mutated(intake, claim))
    assert _refused_naming(out, "residual_air_capacity_kW_per_rack"), out
    assert "'E9'" in out["message"]


def test_compare_carries_the_plausibility_findings_screen_reports_for_the_same_intake(intake):
    """Fixed in the product (PR #90): for an IT load far above the supply ceiling /v1/screen warned and /v1/compare said nothing. The
    expectation is read from /v1/screen for the same intake, not written here, so it does not depend on the finding's wording."""
    odd = mutated(intake, lambda x: x["grid"].__setitem__("current_it_load_MW", 99))
    screen = call("screen", {"intake": odd})
    assert screen["ok"], screen
    findings = [w for w in screen["result"]["intake"]["warnings"] if "firm supply ceiling" in w]
    assert findings, "precondition: screen reports a plausibility finding for this intake"
    compare_out = compare(odd)
    assert compare_out["ok"], compare_out
    for finding in findings:
        assert finding in compare_out["result"]["caveats"]


def test_a_sound_intake_gains_no_plausibility_caveat(intake, baseline):
    assert not [c for c in baseline["caveats"] if "firm supply ceiling" in c]


# --- gaps still open in the product (strict xfail; AssertionError only) -------------------------------------------


IMPOSSIBLE = {
    "build_year_in_the_future": lambda x: x["hall"].__setitem__("build_year", 2099),
}


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            n,
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason=f"{n}: accepted with no refusal and no caveat (observed)",
            ),
        )
        for n in sorted(IMPOSSIBLE)
    ],
)
def test_physically_impossible_or_contradictory_inputs_must_be_refused_or_caveated(
    intake, baseline, name
):
    out = compare(mutated(intake, IMPOSSIBLE[name]))
    assert not out["ok"] or out["result"]["caveats"] != baseline["caveats"]
