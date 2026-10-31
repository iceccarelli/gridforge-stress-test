"""Shared plumbing for product tests that need results, not just fault classification."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from gridforge_stress.fuzz import reference_intake
from gridforge_stress.product_adapter import (
    ProductUnavailable,
    call_batch,
    product_dir,
    product_required,
)


def require_product() -> None:
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))


def run(
    route: str, body: dict[str, Any], *, report_date: str = "2026-10-27", tier: str = "client"
) -> dict[str, Any]:
    """One real handler call; returns its result, or fails the test with the product's refusal/fault verbatim."""
    out = call_batch(
        [{"id": "x", "route": route, "tier": tier, "body": body, "want_result": True}],
        report_date=report_date,
    )["x"]
    assert out["status"] == "ok", (
        f"{route}: {out['status']} {out.get('error')}: {out.get('message')}"
    )
    result: dict[str, Any] = out["result"]
    return result


def mutated(edit: Any) -> dict[str, Any]:
    d = copy.deepcopy(reference_intake())
    edit(d)
    return d
