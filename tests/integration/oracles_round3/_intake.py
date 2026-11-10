"""Helpers for the oracles that edit the reference intake document."""

from __future__ import annotations

import copy
from typing import Any

from gridforge_stress.product_adapter import call_batch

ROUTES = {
    "screen": "/v1/screen",
    "compare": "/v1/compare",
    "study": "/v1/study",
    "diff": "/v1/diff",
}


def set_path(doc: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A deep copy of `doc` with the dotted `path` set to `value` (a `...DELETE` sentinel is not used: see `without`)."""
    out = copy.deepcopy(doc)
    node = out
    keys = path.split(".")
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = value
    return out


def without(doc: dict[str, Any], path: str) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    node = out
    keys = path.split(".")
    for k in keys[:-1]:
        node = node[k]
    node.pop(keys[-1], None)
    return out


def annotate(
    value: Any, evidence: Any = None, note: str | None = "synthetic note"
) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value}
    if evidence is not None:
        out["evidence"] = evidence
    if note is not None:
        out["note"] = note
    return out


def rows_for(cases: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Batch rows (with results) for {id: (route name, body)}, as a client-tier caller."""
    return call_batch(
        [
            {"id": k, "route": ROUTES[route], "tier": "client", "body": body, "want_result": True}
            for k, (route, body) in cases.items()
        ]
    )
