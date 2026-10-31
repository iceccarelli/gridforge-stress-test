"""The company's own published pricing claims (https://timetopower.ai/llms.txt), parsed strictly into a checkable record.

WHAT THIS IS. A CLAIM, not evidence about the world: what the company says it meters and sells. It exists so that claim can be compared with what the
real engine does (tests/integration/test_product_pricing_weights.py). The source is registered as `timetopower-site-llms` and is labelled that way.

WHAT IT PARSES. The per-endpoint unit lines ("`gridforge_screen` - POST https://.../v1/screen - 1 unit", "free", "1 unit per hall") and the API plan
lines ("- API - Triage - EUR 900/month, 600 units"). Nothing else on the page is interpreted.

STRICT. Any line in those two shapes that does not parse cleanly, a duplicate endpoint or plan, a tool name that disagrees with its URL path, a
non-positive plan figure, or a page with no endpoints or no plans, is an error: a parser that guessed would turn a changed page into a silently wrong
record.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "gridforge-stress/site-pricing/1"
SOURCE_ID = "timetopower-site-llms"

_TOOL_LINE = re.compile(r"^- `gridforge_(?P<tool>[a-z_]+)`")
_TOOL = re.compile(
    r"^- `gridforge_(?P<tool>[a-z_]+)` — POST https://(?P<host>[A-Za-z0-9.\-]+)(?P<path>/v1/[a-z/]+) — "
    r"(?P<cost>free|(?P<units>\d+) units?(?P<per_hall> per hall)?)\s*$"
)
_PLAN_LINE = re.compile(r"^- API — ")
_PLAN = re.compile(
    r"^- API — (?P<name>[A-Za-z]+) — €(?P<eur>[\d,]+)/month, (?P<units>[\d,]+) units\s*$"
)


class SitePricingError(ValueError):
    """The page, or the record built from it, cannot be trusted; the message says why."""


def parse_llms_txt(text: str) -> dict[str, Any]:
    weights: dict[str, dict[str, Any]] = {}
    plans: dict[str, dict[str, int]] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if _TOOL_LINE.match(line):
            m = _TOOL.match(line)
            if not m:
                raise SitePricingError(f"line {n}: an endpoint line that does not parse: {line!r}")
            path = m["path"]
            if m["tool"] != path[len("/v1/") :].replace("/", "_"):
                raise SitePricingError(
                    f"line {n}: tool gridforge_{m['tool']} does not match its path {path}"
                )
            if path in weights:
                raise SitePricingError(f"line {n}: duplicate endpoint {path}")
            free = m["cost"] == "free"
            weights[path] = {
                "units": 0 if free else int(m["units"]),
                "per_hall": bool(m["per_hall"]),
                "free": free,
            }
        elif _PLAN_LINE.match(line):
            m = _PLAN.match(line)
            if not m:
                raise SitePricingError(f"line {n}: a plan line that does not parse: {line!r}")
            eur, units = int(m["eur"].replace(",", "")), int(m["units"].replace(",", ""))
            if eur <= 0 or units <= 0:
                raise SitePricingError(
                    f"line {n}: plan {m['name']} has a non-positive figure ({eur}, {units})"
                )
            if m["name"] in plans:
                raise SitePricingError(f"line {n}: duplicate plan {m['name']}")
            plans[m["name"]] = {"eur_per_month": eur, "units_per_month": units}
    if not weights:
        raise SitePricingError("no endpoint lines found: the page's shape has changed")
    if not plans:
        raise SitePricingError("no API plan lines found: the page's shape has changed")
    for path, w in weights.items():
        if not w["free"] and w["units"] <= 0:
            raise SitePricingError(f"{path}: a priced endpoint with {w['units']} units")
    return {"weights": weights, "plans": plans}


def build_record(raw: bytes, *, url: str, retrieved_at: str) -> dict[str, Any]:
    """The checkable record of one reading of the page, tied to the exact bytes by length and SHA-256."""
    parsed = parse_llms_txt(raw.decode("utf-8"))
    return {
        "schema": SCHEMA,
        "source_id": SOURCE_ID,
        "evidence_kind": "company_claim",
        "definition": "What the company says it meters per call and sells as API plans. A claim to be checked against the engine, not evidence about the world.",
        "provenance": {
            "url": url,
            "retrieved_at": retrieved_at,
            "http_status": 200,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "retrieval": "one plain HTTP GET outside the harness fetch path; not a probe workflow run",
        },
        **parsed,
    }


def load_pricing_record(path: Path | str) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise SitePricingError("unrecognised site-pricing schema")
    if doc.get("source_id") != SOURCE_ID or doc.get("evidence_kind") != "company_claim":
        raise SitePricingError(
            "source_id / evidence_kind must stay timetopower-site-llms / company_claim (a claim, not a measurement)"
        )
    prov = doc.get("provenance")
    if (
        not isinstance(prov, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(prov.get("sha256", "")))
        or not isinstance(prov.get("bytes"), int)
        or isinstance(prov.get("bytes"), bool)
        or prov["bytes"] <= 0
        or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(prov.get("retrieved_at", ""))
        )
    ):
        raise SitePricingError(
            "provenance needs sha256 (64 hex), positive bytes and a UTC retrieved_at"
        )
    weights, plans = doc.get("weights"), doc.get("plans")
    if not isinstance(weights, dict) or not weights or not isinstance(plans, dict) or not plans:
        raise SitePricingError("weights and plans must be non-empty objects")
    for path_, w in weights.items():
        ok = (
            isinstance(w, dict)
            and set(w) == {"units", "per_hall", "free"}
            and isinstance(w["units"], int)
            and not isinstance(w["units"], bool)
        )
        if (
            not ok
            or not str(path_).startswith("/v1/")
            or w["units"] < 0
            or (w["free"] != (w["units"] == 0))
        ):
            raise SitePricingError(f"{path_}: malformed weight {w!r}")
    previous = float("inf")
    for name, p in plans.items():
        if (
            not isinstance(p, dict)
            or set(p) != {"eur_per_month", "units_per_month"}
            or min(p.values()) <= 0
        ):
            raise SitePricingError(f"plan {name}: malformed {p!r}")
        per_unit = p["eur_per_month"] / p["units_per_month"]
        if per_unit >= previous:
            raise SitePricingError(
                f"plan {name}: a bigger plan must cost less per unit than the one before"
            )
        previous = per_unit
    return doc
