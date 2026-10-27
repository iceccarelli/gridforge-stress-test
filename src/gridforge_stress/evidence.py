"""Evidence-sufficiency rubric for a time-to-power claim about a specific site.

This is the harness's independent oracle for how much a claim may assert given the evidence on hand. It is
deliberately NOT a re-implementation of the product's calculation; it bounds what any product output may say.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

LEVELS = ("none", "context_only", "indicative", "site_supported")

#: Evidence kinds that are about the specific site/connection and so can support a site-level claim.
SITE_KINDS = frozenset({"utility_load_letter", "load_interconnection_study", "allocation_document"})
#: Kinds that describe a region/system or a generic tariff: context only.
CONTEXT_KINDS = frozenset(
    {
        "balancing_authority_observation",
        "generation_queue_statistic",
        "candidate_poi_listing",
        "generic_tariff",
        "weather",
    }
)
MODELED_KINDS = frozenset({"modeled_scenario"})


@dataclass(frozen=True)
class EvidenceItem:
    source_id: str
    kind: str
    retrieval: str  # fresh | stale | failed | synthetic
    note: str = ""


def assess(items: Iterable[EvidenceItem]) -> dict[str, Any]:
    reasons: list[str] = []
    used: list[EvidenceItem] = []
    for it in items:
        if it.retrieval == "failed":
            reasons.append(f"{it.source_id}: retrieval failed; excluded, not treated as fresh")
        elif it.kind not in SITE_KINDS | CONTEXT_KINDS | MODELED_KINDS:
            reasons.append(f"{it.source_id}: unknown evidence kind {it.kind!r}; excluded")
        else:
            used.append(it)
    fresh = [u for u in used if u.retrieval in ("fresh", "synthetic")]
    stale = [u for u in used if u.retrieval == "stale"]
    site_fresh = [u for u in fresh if u.kind in SITE_KINDS]
    site_stale = [u for u in stale if u.kind in SITE_KINDS]
    context = [u for u in used if u.kind in CONTEXT_KINDS | MODELED_KINDS]
    if site_fresh:
        level = 3
    elif site_stale:
        level = 2
        reasons.append("site-specific evidence is stale; capped at indicative")
    elif context:
        level = 1
        reasons.append(
            "only region/system/generic/modeled evidence; cannot support a site-level claim"
        )
    else:
        level = 0
    if level and any(u.kind in MODELED_KINDS for u in used):
        reasons.append("modeled scenario data is labelled modeled and never raises the level")
    if stale and level == 1:
        reasons.append("stale context evidence noted")
    return {
        "level": LEVELS[level],
        "level_index": level,
        "reasons": reasons,
        "allows_site_specific_claim": level == 3,
    }
