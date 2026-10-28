"""Vertical slice against the REAL GridForge-AI /v1/compare handler. Opt in with `pytest -m product`.

Input: SYNTHETIC reference-hall intake (GridForge's own example, verbatim copy with manifest).
Independent expectation: the elapsed time of a scenario is the longest lead time among the reliefs it needs, and
every lead time declared in an intake is an assumption (E0) in the product's loader.
"""

from __future__ import annotations

import copy
import json

import pytest

from gridforge_stress.evidence import EvidenceItem, assess
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import (
    GRADE_ORDER,
    ProductUnavailable,
    claim_violations,
    compare,
    product_dir,
    product_required,
)

pytestmark = [pytest.mark.integration, pytest.mark.product]

INTAKE = fixtures_dir() / "product" / "reference_hall_intake.json"


@pytest.fixture(scope="module")
def intake():
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))
    return json.loads(INTAKE.read_text())


@pytest.fixture(scope="module")
def baseline(intake):
    out = compare(intake)
    assert out["ok"], out
    return out["result"]


def scenario(out, sid):
    return next(s for s in out["scenarios"] if s["id"] == sid)


def test_fixture_is_labelled_synthetic():
    man = json.loads((INTAKE.with_name(INTAKE.name + ".manifest.json")).read_text())
    assert man["origin"] == "synthetic_fixture" and "SYNTHETIC" in " ".join(man["limitations"])


def test_independent_expected_date_matches_product(intake, baseline):
    # Independent derivation from the INTAKE: the grid-limited BTM scenario waits for the transformer
    # (declared 160 weeks) which is longer than the 30 and 44 week BTM options and every other relief.
    declared = intake["transformers"][0]["replacement_lead_time_weeks"]
    others = [o["lead_time_weeks"] for o in intake["btm_options"]]
    assert declared > max(others)
    ttp = scenario(baseline, "full_dlc_btm")["time_to_power"]
    assert ttp["status"] == "dated" and ttp["weeks"] == declared
    # and for every dated scenario the date is the longest lead time the product itself lists for it
    for s in baseline["scenarios"]:
        t = s["time_to_power"]
        if t["status"] == "dated":
            lead = [lt["lead_time_weeks"]["value"] for lt in t["lead_times"] if not lt["missing"]]
            assert t["weeks"] == max(lead)


def test_product_never_upgrades_assumptions(baseline):
    assert baseline["grade"] in ("indicative", "undated")
    for s in baseline["scenarios"]:
        t = s["time_to_power"]
        if t["status"] == "dated":
            assert t["evidence"] == "E0" and t["date_basis"] == "assumed"
            assert "not a date" in t["date_label"]
    assert any("Grid reinforcement is not dated" in c for c in baseline["caveats"])


def test_output_within_what_context_evidence_supports(baseline):
    ev = assess(
        [
            EvidenceItem("eia-930", "balancing_authority_observation", "synthetic"),
            EvidenceItem("openei-urdb", "generic_tariff", "synthetic"),
            EvidenceItem("lbnl-queued-up", "generation_queue_statistic", "synthetic"),
        ]
    )
    assert ev["level"] == "context_only"
    assert claim_violations(baseline, ev["level"]) == []


def test_claim_gate_catches_overclaim():
    fake = {
        "grade": "screening",
        "scenarios": [
            {
                "id": "x",
                "time_to_power": {
                    "status": "dated",
                    "weeks": 10,
                    "date_basis": "supported",
                    "date_label": "x",
                },
            }
        ],
    }
    v = claim_violations(fake, "context_only")
    assert len(v) == 2


def test_deterministic_and_digest_stable(intake, baseline):
    again = compare(intake)["result"]
    assert again == baseline and again["request_digest"] == baseline["request_digest"]


def test_removing_a_lead_time_removes_the_date_and_cannot_raise_grade(intake, baseline):
    mutated = copy.deepcopy(intake)
    for o in mutated["btm_options"]:
        o.pop("lead_time_weeks")
    out = compare(mutated)
    assert out["ok"], out
    r = out["result"]
    btm = scenario(r, "full_dlc_btm")["time_to_power"]
    assert btm["weeks"] is None and btm["status"] != "dated", (
        "missing lead time must not become a small number"
    )
    assert GRADE_ORDER.index(r["grade"]) <= GRADE_ORDER.index(baseline["grade"])


def test_longer_lead_time_never_shortens_the_date(intake, baseline):
    mutated = copy.deepcopy(intake)
    mutated["transformers"][0]["replacement_lead_time_weeks"] = 200
    r = compare(mutated)["result"]
    before = scenario(baseline, "full_dlc_btm")["time_to_power"]["weeks"]
    after = scenario(r, "full_dlc_btm")["time_to_power"]["weeks"]
    assert after == 200 and after >= before


def test_hostile_intake_is_refused_not_defaulted(intake):
    bad = copy.deepcopy(intake)
    bad["btm_options"][0]["lead_time_weeks"] = -4
    out = compare(bad)
    assert out["ok"] is False, (
        "negative lead time must be refused by the product, not silently accepted"
    )
    bad2 = copy.deepcopy(intake)
    bad2["btm_options"][0]["capacity_MW"] = "lots"
    assert compare(bad2)["ok"] is False
