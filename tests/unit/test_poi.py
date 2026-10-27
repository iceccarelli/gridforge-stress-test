from __future__ import annotations

import pytest

from gridforge_stress.poi import classify_poi

pytestmark = pytest.mark.unit


def test_listing_is_never_an_allocation():
    r = classify_poi({"listed_as": "open"})
    assert r["state"] == "candidate" and not r["is_allocation"]


@pytest.mark.parametrize(
    "rec",
    [
        {"listed_as": "restricted"},
        {"listed_as": "open_complex_upgrades"},
        {"listed_as": "open", "rating_ka": 63, "required_ka": 70},
        {"listed_as": "open", "mitigation_cost_is_placeholder": True},
        {"listed_as": "open", "sponsor_requirement_clear": False},
    ],
)
def test_constraints_demote_to_constrained(rec):
    assert classify_poi(rec)["state"] == "constrained_option"


def test_verified_requires_study_and_allocation_evidence_and_no_constraints():
    full = {"listed_as": "open", "study_complete": True, "allocation_evidence": "doc-1"}
    assert classify_poi(full)["state"] == "verified_availability"
    for drop in ("study_complete", "allocation_evidence"):
        d = {k: v for k, v in full.items() if k != drop}
        assert classify_poi(d)["state"] != "verified_availability"
    assert classify_poi({**full, "listed_as": "restricted"})["state"] == "constrained_option"


def test_unlisted_is_unknown():
    assert classify_poi({})["state"] == "unknown"
