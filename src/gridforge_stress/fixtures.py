"""Fixture validation: manifest schema, checksum, honest labelling, and content validity."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from .findings import Finding, errors
from .hourly import validate_hourly
from .manifest import sha256_hex
from .poi import classify_poi
from .queue import ColumnMap, parse_rows
from .registry import RegistryError, default_sources, load_json, validate_against
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
    if name == "poi_synthetic.json":
        bad = [p for p in load_json(path)["pois"] if classify_poi(p)["is_allocation"]]
        return [Finding("poi_promoted", f"{p['name']} classified as allocation") for p in bad]
    if name == "tariff_synthetic.json":
        return validate_tariff(load_json(path))
    return [Finding("no_validator", f"no content validator registered for {name}")]


def validate_fixtures(root: Path) -> list[Finding]:
    out: list[Finding] = []
    sources = default_sources()
    data_files = sorted(
        p for p in root.iterdir() if p.is_file() and not p.name.endswith(".manifest.json")
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
        if doc["origin"] == "source_derived_fixture" and not doc["attribution"].strip():
            out.append(
                Finding("no_attribution", "source-derived fixture needs attribution", where=p.name)
            )
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
