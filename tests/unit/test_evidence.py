from __future__ import annotations

import random

import pytest

from gridforge_stress.evidence import CONTEXT_KINDS, MODELED_KINDS, SITE_KINDS, EvidenceItem, assess

pytestmark = pytest.mark.unit
ALL_KINDS = sorted(SITE_KINDS | CONTEXT_KINDS | MODELED_KINDS)
RETR = ["fresh", "stale", "failed", "synthetic"]


def rand_items(rng):
    return [
        EvidenceItem(f"s{i}", rng.choice(ALL_KINDS), rng.choice(RETR))
        for i in range(rng.randint(0, 6))
    ]


def test_region_observation_cannot_prove_site_capacity():
    r = assess([EvidenceItem("eia-930", "balancing_authority_observation", "fresh")])
    assert r["level"] == "context_only" and not r["allows_site_specific_claim"]


def test_candidate_poi_is_not_site_supported():
    assert (
        assess([EvidenceItem("caiso", "candidate_poi_listing", "fresh")])["level"] == "context_only"
    )


def test_modeled_never_raises_confidence():
    assert assess([EvidenceItem("c", "modeled_scenario", "fresh")])["level"] == "context_only"


def test_failed_retrieval_is_not_fresh_evidence():
    r = assess([EvidenceItem("u", "utility_load_letter", "failed")])
    assert r["level"] == "none" and any("failed" in x for x in r["reasons"])


def test_staleness_lowers_site_evidence():
    fresh = assess([EvidenceItem("u", "utility_load_letter", "fresh")])
    stale = assess([EvidenceItem("u", "utility_load_letter", "stale")])
    assert fresh["level_index"] > stale["level_index"] and not stale["allows_site_specific_claim"]


def test_unknown_kind_excluded():
    assert assess([EvidenceItem("x", "vibes", "fresh")])["level"] == "none"


def test_property_removing_evidence_never_increases_level():
    rng = random.Random(20261027)
    for _ in range(500):
        items = rand_items(rng)
        base = assess(items)["level_index"]
        for i in range(len(items)):
            assert assess(items[:i] + items[i + 1 :])["level_index"] <= base


def test_property_degrading_freshness_never_increases_level():
    rng = random.Random(7)
    for _ in range(500):
        items = rand_items(rng)
        base = assess(items)["level_index"]
        for i, it in enumerate(items):
            for worse in ("stale", "failed"):
                degraded = items[:i] + [EvidenceItem(it.source_id, it.kind, worse)] + items[i + 1 :]
                if it.retrieval in ("fresh", "synthetic") or worse == "failed":
                    assert assess(degraded)["level_index"] <= base


def test_property_site_claim_requires_fresh_site_item():
    rng = random.Random(99)
    for _ in range(500):
        items = rand_items(rng)
        if assess(items)["allows_site_specific_claim"]:
            assert any(
                i.kind in SITE_KINDS and i.retrieval in ("fresh", "synthetic") for i in items
            )
