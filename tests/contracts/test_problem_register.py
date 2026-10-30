from __future__ import annotations

import pytest

from gridforge_stress.registry import load_problems

pytestmark = pytest.mark.contract


def test_register_validates_and_ids_are_unique():
    assert len(load_problems()) >= 8


def test_no_unsupported_quantification():
    for p in load_problems():
        assert p["consequence_quantified"] is None, p["id"]


def test_unvalidated_unless_customer_validated():
    """Nothing here may claim customer validation without evidence of it (none has been gathered)."""
    for p in load_problems():
        assert p["validation_status"] == "hypothesis_unvalidated_with_customers", p["id"]


def test_strong_evidence_needs_an_observed_fact():
    for p in load_problems():
        kinds = {s["kind"] for s in p["evidence"]["statements"]}
        if p["evidence"]["strength"] == "strong":
            assert "observed_fact" in kinds, p["id"]
        if kinds <= {"search_snippet_unverified", "hypothesis"}:
            assert p["evidence"]["strength"] == "weak", p["id"]
