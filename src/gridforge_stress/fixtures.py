"""Fixture validation: manifest schema, checksum, honest labelling, and content validity."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .findings import Finding, errors
from .hourly import validate_hourly
from .manifest import sha256_hex
from .poi import classify_poi
from .queue import LBNL_2026_MAP, ColumnMap, parse_rows
from .registry import RegistryError, Source, default_sources, load_json, validate_against
from .scenarios_data import validate_tariff

QUEUE_MAP = ColumnMap(
    project_id="proj",
    status="stat",
    queue_date="entered",
    capacity_mw="mw",
    technology="tech",
    withdrawn_date="withdrawn",
    in_service_date="in_service",
    status_map={"Active": "active", "Withdrawn": "withdrawn", "Operational": "operational"},
)


def _content_check(path: Path) -> list[Finding]:
    name = path.name
    if name == "queue_synthetic.csv":
        with path.open(newline="", encoding="utf-8") as fh:
            _, f = parse_rows(csv.DictReader(fh), QUEUE_MAP)
        return f
    if name == "eia930_synthetic.json":
        _, f = validate_hourly(
            load_json(path)["series"], as_of=datetime(2026, 3, 8, 10, tzinfo=UTC)
        )
        return f
    if name == "lbnl_observed_sample.json":
        rows = load_json(path)["rows"]
        recs, found = parse_rows(rows, LBNL_2026_MAP)
        if len(recs) != len(rows):
            found.append(
                Finding("rows_rejected", f"{len(rows) - len(recs)} observed rows rejected")
            )
        return found
    if name == "lbnl_ir_to_cod_context.json":
        from .context_bridge import BridgeError, load_queue_context

        try:
            load_queue_context(path)
        except (BridgeError, KeyError, ValueError) as exc:
            return [Finding("bad_queue_context", str(exc))]
        return []
    if name == "eia_retail_price_context.json":
        from .price import PriceContextError, load_price_context

        try:
            load_price_context(path)
        except (PriceContextError, KeyError, ValueError) as exc:
            return [Finding("bad_price_context", str(exc))]
        return []
    if name == "site_llms_pricing.json":
        from .site import SitePricingError, load_pricing_record

        try:
            load_pricing_record(path)
        except (SitePricingError, KeyError, ValueError) as exc:
            return [Finding("bad_site_pricing", str(exc))]
        return []
    if name == "poi_synthetic.json":
        bad = [p for p in load_json(path)["pois"] if classify_poi(p)["is_allocation"]]
        return [Finding("poi_promoted", f"{p['name']} classified as allocation") for p in bad]
    if name == "tariff_synthetic.json":
        return validate_tariff(load_json(path))
    if name.endswith("_intake.json"):
        doc = load_json(path)
        missing = [k for k in ("project", "grid", "compute") if k not in doc]
        return (
            [Finding("bad_intake_fixture", f"missing top-level keys {missing}")] if missing else []
        )
    return [Finding("no_validator", f"no content validator registered for {name}")]


def _source_derived_findings(
    doc: dict[str, Any], sources: dict[str, Source], path: Path
) -> list[Finding]:
    """A source-derived fixture must carry what lets a reader trace and re-check it; a missing piece is an error, not a default."""
    out: list[Finding] = []
    name = path.name
    if not doc["attribution"].strip():
        out.append(
            Finding("no_attribution", "source-derived fixture needs attribution", where=name)
        )
    for field in ("retrieved_at", "upstream_release"):
        if not (doc.get(field) or "").strip():
            out.append(
                Finding(
                    "incomplete_provenance", f"source-derived fixture needs {field}", where=name
                )
            )
    for field in ("units", "transformations"):
        if not doc.get(field):
            out.append(
                Finding(
                    "incomplete_provenance", f"source-derived fixture needs {field}", where=name
                )
            )
    src = sources.get(doc["source_id"])
    if src is not None and src.raw["verification"]["status"] != "publisher_page_read":
        out.append(
            Finding(
                "source_not_verified",
                f"derived from {doc['source_id']}, whose registry verification is "
                f"{src.raw['verification']['status']!r}: the fixture does not make the source verified",
                severity="warning",
                where=name,
            )
        )
    if name == "eia_retail_price_context.json":
        try:
            sha = load_json(path)["provenance"]["page_sha256"]
        except (KeyError, TypeError, ValueError):
            sha = ""
        if not sha or sha not in (doc.get("upstream_release") or ""):
            out.append(
                Finding(
                    "provenance_mismatch",
                    "page_sha256 in the fixture is not the one named in the manifest's upstream_release",
                    where=name,
                )
            )
    return out


def validate_fixtures(root: Path) -> list[Finding]:
    out: list[Finding] = []
    sources = default_sources()
    data_files = sorted(
        p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".manifest.json")
    )
    if not data_files:
        return [Finding("no_fixtures", f"no fixtures in {root}")]
    for p in data_files:
        mp = p.with_name(p.name + ".manifest.json")
        if not mp.is_file():
            out.append(Finding("no_manifest", "fixture has no manifest", where=p.name))
            continue
        doc = load_json(mp)
        try:
            validate_against("manifest", doc)
        except RegistryError as exc:
            out.append(Finding("manifest_schema", str(exc), where=p.name))
            continue
        data = p.read_bytes()
        if doc["sha256"] != sha256_hex(data) or doc["byte_count"] != len(data):
            out.append(Finding("checksum", "manifest does not match file", where=p.name))
        if doc["source_id"] not in sources:
            out.append(Finding("unknown_source", doc["source_id"], where=p.name))
        if doc["origin"] == "synthetic_fixture" and "SYNTHETIC" not in " ".join(doc["limitations"]):
            out.append(
                Finding("unlabelled_synthetic", "limitations must say SYNTHETIC", where=p.name)
            )
        if doc["origin"] == "source_derived_fixture":
            out.extend(_source_derived_findings(doc, sources, p))
        if doc["origin"] == "live_fetch":
            out.append(
                Finding(
                    "raw_download_in_fixtures",
                    "live downloads do not belong in fixtures",
                    where=p.name,
                )
            )
        out.extend(
            Finding(f.code, f.message, f.severity, f"{p.name} {f.where}".strip())
            for f in _content_check(p)
            if f.code not in EXPECTED.get(p.name, set())
        )
    return out


#: Findings the fixtures are DESIGNED to contain (negative cases); anything else is a defect.
EXPECTED: dict[str, set[str]] = {}


def ok(findings: list[Finding]) -> bool:
    return not errors(findings)
