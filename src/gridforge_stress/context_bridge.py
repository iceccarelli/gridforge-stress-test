"""Context bridge: set GridForge's own grid-wait assumption beside observed interconnection-queue history.

The product assumes a grid-reinforcement lead time (evidence class E0). This module reads THAT number from the real
`/v1/screen` payload (never a copy), and compares it with an attributed, source-derived LBNL statistic. The result is
CONTEXT ONLY: a US generation-queue statistic cannot change the product's evidence class, is not evidence about a
specific site's network operator, and is not a load-connection time. Every output says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence import EvidenceItem, assess
from .paths import fixtures_dir

SCHEMA = "gridforge-stress/context-bridge/1"
WEEKS_PER_YEAR = 365.25 / 7
CONTEXT_FILE = "lbnl_ir_to_cod_context.json"
MUST_NOT_CLAIM = (
    "that the observed queue duration is a connection time for a load or a data centre",
    "that the product's grid assumption is validated, confirmed or raised in evidence class by this comparison",
    "that this says anything about the site's own network operator, tariff, study status or equipment delivery",
    "that requests which did not reach commercial operation take this long (the statistic excludes them)",
)


class BridgeError(ValueError):
    """The bridge cannot answer without guessing; the message says what is missing."""


def load_queue_context(path: Path | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((path or fixtures_dir() / CONTEXT_FILE).read_text())
    if doc.get("schema") != "gridforge-stress/queue-context/1":
        raise BridgeError("unrecognised queue-context schema")
    for name, c in {**doc["contexts"], **doc.get("by_region_cod_2018_2025", {})}.items():
        if not (c["n"] > 0 and c["p25"] < c["median"] < c["p75"]):
            raise BridgeError(f"context {name!r} is not a valid quartile summary")
    return doc


def product_grid_assumption(screen_result: dict[str, Any]) -> dict[str, Any]:
    """The product's own grid-reinforcement lead time, read from a real /v1/screen (or /v1/study) payload."""
    for sc in screen_result.get("scenarios", []):
        for con in sc.get("constraints", []) or []:
            if con.get("id") != "grid_firm_capacity":
                continue
            lt = (con.get("relief") or {}).get("lead_time_weeks")
            if not lt:
                continue
            for k in ("value", "low", "high", "evidence"):
                if lt.get(k) is None:
                    raise BridgeError(f"product lead time lacks {k!r}; refusing to fill it in")
            return {
                "weeks": float(lt["value"]),
                "band_low_weeks": float(lt["low"]),
                "band_high_weeks": float(lt["high"]),
                "evidence_class": str(lt["evidence"]),
                "label": lt.get("label"),
                "years": round(float(lt["value"]) / WEEKS_PER_YEAR, 3),
                "product_risk_note": (con.get("relief") or {}).get("risk"),
                "read_from": f"scenario {sc.get('id')} constraint grid_firm_capacity relief.lead_time_weeks",
            }
    raise BridgeError(
        "the product payload carries no grid-reinforcement lead time (the public tier omits constraints): "
        "request the client tier; the bridge never substitutes a default"
    )


def site_country(intake: dict[str, Any]) -> str | None:
    country = (intake.get("site") or {}).get("country")
    return str(country).upper() if country else None


def _position(a: float, p25: float, p50: float, p75: float) -> str:
    if a < p25:
        return "below_p25"
    if a < p50:
        return "between_p25_and_median"
    if a < p75:
        return "between_median_and_p75"
    return "at_or_above_p75"


def bridge(
    screen_result: dict[str, Any],
    intake: dict[str, Any],
    *,
    context_key: str = "cod_2018_2025",
    region: str | None = None,
    context_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = context_doc or load_queue_context()
    if region is not None:
        regions = doc["by_region_cod_2018_2025"]
        if region not in regions:
            raise BridgeError(f"unknown region {region!r}; known: {sorted(regions)}")
        ctx, ctx_name = regions[region], f"region {region}, COD 2018-2025"
    else:
        if context_key not in doc["contexts"]:
            raise BridgeError(f"unknown context {context_key!r}; known: {sorted(doc['contexts'])}")
        labels = {"all_cod_years": "all COD years", "cod_2018_2025": "COD years 2018-2025"}
        ctx, ctx_name = doc["contexts"][context_key], labels.get(context_key, context_key)
    assumed = product_grid_assumption(screen_result)
    to_w = WEEKS_PER_YEAR
    p25, p50, p75 = ctx["p25"] * to_w, ctx["median"] * to_w, ctx["p75"] * to_w
    a = assumed["weeks"]
    country = site_country(intake)
    same_country = country == doc["geography"]
    level = "same_country_context" if same_country else "scale_reference_only"
    comparison = {
        "assumed_weeks": a,
        "observed_p25_weeks": round(p25, 1),
        "observed_median_weeks": round(p50, 1),
        "observed_p75_weeks": round(p75, 1),
        "position_of_assumption": _position(a, p25, p50, p75),
        "assumed_over_observed_median": round(a / p50, 3),
        "assumed_band_covers_observed_median": assumed["band_low_weeks"]
        <= p50
        <= assumed["band_high_weeks"],
        "assumed_band_covers_observed_iqr": assumed["band_low_weeks"] <= p25
        and p75 <= assumed["band_high_weeks"],
    }
    pos_text = comparison["position_of_assumption"].replace("_", " ")
    where = f"{country or 'unknown country'} is " + (
        "the same country as the observed data" if same_country else f"not {doc['geography']}"
    )
    statements = [
        f"The product assumes a grid-reinforcement wait of {a:.0f} weeks ({assumed['years']} years; band "
        f"{assumed['band_low_weeks']:.0f}-{assumed['band_high_weeks']:.0f} weeks; evidence class "
        f"{assumed['evidence_class']}).",
        f"In LBNL's {doc['geography']} generation-queue data ({ctx_name}; n={ctx['n']}), requests that reached "
        f"commercial operation took a median {ctx['median']} years from request to in-service "
        f"(p25 {ctx['p25']}, p75 {ctx['p75']}).",
        f"The product's assumption is {pos_text} of that observed distribution "
        f"({comparison['assumed_over_observed_median']}x the observed median).",
        f"Applicability: {where}; "
        + (
            "this is same-country context."
            if same_country
            else "this is a scale reference only, not evidence about this site's network operator."
        ),
        "The product's evidence class for this assumption is unchanged by this comparison.",
    ]
    return {
        "schema": SCHEMA,
        "product_assumption": assumed,
        "observed_context": {
            "name": ctx_name,
            "n": ctx["n"],
            "p25_years": ctx["p25"],
            "median_years": ctx["median"],
            "p75_years": ctx["p75"],
            "population": doc["population"],
            "geography": doc["geography"],
            "definition": doc["definition"],
            "provenance": doc["provenance"],
            "limits": doc["limits"],
        },
        "comparison": comparison,
        "applicability": {
            "site_country": country,
            "context_geography": doc["geography"],
            "level": level,
        },
        "evidence": assess([EvidenceItem("lbnl-queued-up", doc["evidence_kind"], "fresh")]),
        "product_evidence_class_unchanged": assumed["evidence_class"],
        "statements": statements,
        "must_not_claim": list(MUST_NOT_CLAIM),
    }
