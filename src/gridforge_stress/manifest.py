"""Dataset manifests and atomic, non-clobbering snapshot storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .findings import Finding, errors
from .registry import RegistryError, validate_against

MANIFEST_VERSION = "dataset-manifest/1"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Manifest:
    source_id: str
    source_url: str
    origin: str
    retrieval_status: str
    attribution: str
    sha256: str = "0" * 64
    byte_count: int = 0
    retrieved_at: str | None = None
    response_status: int | None = None
    record_count: int | None = None
    time_coverage: dict[str, Any] | None = None
    upstream_release: str | None = None
    units: dict[str, str] = field(default_factory=dict)
    transformations: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        doc = {"schema_version": MANIFEST_VERSION, **self.__dict__}
        validate_against("manifest", doc)
        return doc


def manifest_for_bytes(
    data: bytes,
    *,
    source_id: str,
    source_url: str,
    origin: str,
    attribution: str,
    record_count: int | None = None,
    **extra: Any,
) -> Manifest:
    """Manifest for content we hold. `origin` must say whether it is live, source-derived or synthetic."""
    status = (
        "synthetic"
        if origin == "synthetic_fixture"
        else extra.pop("retrieval_status", "not_attempted")
    )
    return Manifest(
        source_id=source_id,
        source_url=source_url,
        origin=origin,
        retrieval_status=status,
        attribution=attribution,
        sha256=sha256_hex(data),
        byte_count=len(data),
        record_count=record_count,
        **extra,
    )


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def commit_snapshot(root: Path, name: str, data: bytes, manifest: Manifest) -> Path:
    """Write data+manifest only for a validated fetch; atomic; never overwrites a valid snapshot on failure."""
    if manifest.retrieval_status != "fetched_validated":
        raise RegistryError(f"refusing to store snapshot with status {manifest.retrieval_status}")
    if sha256_hex(data) != manifest.sha256 or len(data) != manifest.byte_count:
        raise RegistryError("manifest checksum/byte count does not match data")
    doc = manifest.to_dict()
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    for suffix, payload in ((".bin", data), (".manifest.json", json.dumps(doc, indent=2).encode())):
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, str(target) + suffix)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return target


def promote(manifest: Manifest, findings: list[Finding]) -> Manifest:
    """Only content validation can turn a transport-level success into `fetched_validated`."""
    if manifest.retrieval_status != "fetched_unparsed":
        raise RegistryError(f"cannot promote status {manifest.retrieval_status}")
    status = "fetched_failed_validation" if errors(findings) else "fetched_validated"
    return replace(manifest, retrieval_status=status)
