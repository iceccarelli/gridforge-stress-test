"""Source registry and scenario catalogue: schema-validated, read-only views."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .paths import configs_dir, schemas_dir


class RegistryError(ValueError):
    """The registry or catalogue is malformed or internally inconsistent."""


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate_against(schema_name: str, document: Any) -> None:
    schema = load_json(schemas_dir() / f"{schema_name}.schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5]
        )
        raise RegistryError(f"{schema_name} schema violation: {detail}")


@dataclass(frozen=True)
class Source:
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return tuple(self.raw["allowed_hosts"])

    @property
    def credential_env(self) -> tuple[str, ...]:
        return tuple(self.raw["credential_env"])

    @property
    def live_fetch(self) -> str:
        return str(self.raw["live_fetch"])

    @property
    def access_status(self) -> str:
        return str(self.raw["access_status"])

    @property
    def locator(self) -> str:
        return str(self.raw["locator"])

    @property
    def probe_urls(self) -> tuple[str, ...]:
        return tuple(self.raw.get("probe_urls", ()))

    @property
    def data_urls(self) -> tuple[str, ...]:
        return tuple(self.raw.get("data_urls", ()))

    @property
    def max_bytes(self) -> int:
        return int(self.raw.get("max_bytes", 5_000_000))

    @property
    def allowed_content_types(self) -> tuple[str, ...]:
        return tuple(self.raw.get("allowed_content_types", ()))


def load_sources(path: Path | None = None) -> dict[str, Source]:
    doc = load_json(path or configs_dir() / "sources.json")
    validate_against("source", doc)
    out: dict[str, Source] = {}
    for rec in doc["sources"]:
        if rec["id"] in out:
            raise RegistryError(f"duplicate source id {rec['id']}")
        host = rec["locator"].split("/")[2].split(":")[0].lower()
        if host not in rec["allowed_hosts"]:
            raise RegistryError(f"{rec['id']}: locator host {host} not in allowed_hosts")
        for extra in [*rec.get("probe_urls", []), *rec.get("data_urls", [])]:
            if extra.split("/")[2].split(":")[0].lower() not in rec["allowed_hosts"]:
                raise RegistryError(
                    f"{rec['id']}: probe/data url host not in allowed_hosts: {extra}"
                )
        pages = {rec["locator"], *rec.get("probe_urls", [])}
        if pages & set(rec.get("data_urls", [])):
            raise RegistryError(f"{rec['id']}: a URL cannot be both a page and a dataset download")
        if (
            rec["access_status"] in {"registration_required", "restricted"}
            and rec["live_fetch"] == "enabled"
        ):
            raise RegistryError(f"{rec['id']}: gated source cannot enable live fetch")
        if (
            rec["verification"]["status"] != "publisher_page_read"
            and rec["live_fetch"] == "enabled"
        ):
            raise RegistryError(f"{rec['id']}: live fetch enabled without verified publisher page")
        out[rec["id"]] = Source(rec)
    return out


def load_scenarios(path: Path | None = None) -> list[dict[str, Any]]:
    doc = load_json(path or configs_dir() / "scenarios.json")
    validate_against("scenario", doc)
    known = set(load_sources())
    seen: set[str] = set()
    for sc in doc["scenarios"]:
        if sc["id"] in seen:
            raise RegistryError(f"duplicate scenario id {sc['id']}")
        seen.add(sc["id"])
        missing = set(sc["source_ids"]) - known
        if missing:
            raise RegistryError(f"{sc['id']}: unknown source ids {sorted(missing)}")
    return list(doc["scenarios"])


@lru_cache(maxsize=1)
def default_sources() -> dict[str, Source]:
    return load_sources()
