"""GF-003: CAISO POI availability classification. Never promotes a listing to an allocation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STATES = ("verified_availability", "candidate", "constrained_option", "option", "unknown")


def classify_poi(rec: Mapping[str, Any]) -> dict[str, Any]:
    """rec keys (all optional): listed_as in {restricted, open_complex_upgrades, open},
    rating_ka, required_ka, mitigation_cost_is_placeholder, sponsor_requirement_clear,
    study_complete, allocation_evidence (document id or None)."""
    reasons: list[str] = []
    listed = rec.get("listed_as")
    rating, need = rec.get("rating_ka"), rec.get("required_ka")
    constrained = listed in ("restricted", "open_complex_upgrades")
    if constrained:
        reasons.append(f"listed as {listed}")
    if rating is not None and need is not None and need > rating:
        constrained = True
        reasons.append("required short-circuit duty exceeds equipment rating")
    if rec.get("mitigation_cost_is_placeholder"):
        constrained = True
        reasons.append("mitigation cost is a placeholder")
    if rec.get("sponsor_requirement_clear") is False:
        constrained = True
        reasons.append("sponsor requirement unclear")
    if listed is None and not rec.get("allocation_evidence"):
        return _out("unknown", ["not listed in the supplied material"])
    if rec.get("allocation_evidence") and rec.get("study_complete") and not constrained:
        return _out("verified_availability", ["study complete and allocation document cited"])
    if constrained:
        return _out("constrained_option", reasons)
    if listed == "open":
        if not rec.get("study_complete"):
            reasons.append("study incomplete or unavailable")
        return _out("candidate", reasons or ["listed open; no allocation evidence"])
    return _out("unknown", reasons or ["insufficient evidence"])


def _out(state: str, reasons: list[str]) -> dict[str, Any]:
    return {"state": state, "reasons": reasons, "is_allocation": state == "verified_availability"}
