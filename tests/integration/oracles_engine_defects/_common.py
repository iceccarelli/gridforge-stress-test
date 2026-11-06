"""Shared plumbing for the engine-defect oracles in this package.

Everything here reaches the product only through `product_adapter.call` (its real `/v1/compare` and `/v1/screen` handlers) or, for the
one unit-level oracle, a read-only import of two product modules in a clean interpreter. Nothing re-implements the product's arithmetic.
Input is the SYNTHETIC reference hall (data/fixtures/product/reference_hall_intake.json).
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import ProductUnavailable, call, product_dir, product_required

LIQUID_SCENARIOS = ("hybrid_dlc", "full_dlc", "full_dlc_btm")


def require_product() -> None:
    """A named checkout that is missing fails; an unnamed, absent one skips (the harness-wide convention)."""
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))


def reference_doc() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(
        (fixtures_dir() / "product" / "reference_hall_intake.json").read_text()
    )
    return doc


def mutated(doc: dict[str, Any], edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    edit(out)
    return out


def answered(route: str, doc: dict[str, Any]) -> dict[str, Any]:
    """The product's result for `doc`, or a test failure quoting its refusal verbatim."""
    out = call(route, {"intake": doc})
    assert out["ok"], f"{route} refused a hall the test needs answered: {out}"
    result: dict[str, Any] = out["result"]
    return result


def scenarios(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in result["scenarios"]}


def leaves(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from leaves(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from leaves(v, f"{path}[{i}]")
    else:
        yield path, node


def strings(node: Any) -> list[str]:
    """Every text value anywhere in a result (captions, bases, caveats, limitations), for disclosure checks."""
    return [v for _, v in leaves(node) if isinstance(v, str)]


def ample_hall(busway_A: int, tapoff_A: int) -> dict[str, Any]:
    """The reference hall with grid, transformer and UPS made ample, a 230 V single-phase feed (a GB300 rack then needs ~600 A) and 40 kW
    of residual-air removal, so that only the LV chain (busway ampacity and tap-off rating) decides how many racks fit.

    Only `lv.busway_ampacity_A` and `lv.tapoff_max_A` vary between the members of a family built from this."""
    doc = reference_doc()
    doc["grid"].update(firm_capacity_MVA=60, contracted_MW=50, current_it_load_MW=1.0)
    doc["grid"]["current_site_peak_MW"] = 3.0
    doc["transformers"][0]["unit_rating_MVA"] = 30
    doc["ups"][0]["unit_rating_kW"] = 10000
    doc["hall"].update(rack_positions=2000, positions_available=1500)
    doc["lv"].update(voltage_V=230, phases=1, busway_ampacity_A=busway_A, tapoff_max_A=tapoff_A)
    doc["thermal"]["residual_air_capacity_kW_per_rack"] = {"value": 40, "evidence": "E3"}
    return doc


def constraint(scenario: dict[str, Any], cid: str) -> dict[str, Any]:
    """One constraint row of a /v1/screen scenario."""
    rows = [c for c in scenario["constraints"] if c["id"] == cid]
    assert rows, (
        f"{scenario['id']}: no {cid} constraint in {[c['id'] for c in scenario['constraints']]}"
    )
    row: dict[str, Any] = rows[0]
    return row


def product_modules_json(code: str) -> Any:
    """Run `code` (which prints one JSON document) in a clean interpreter with the product on sys.path. Read-only: it imports library
    modules and prints constants, it calls no handler and writes nothing."""
    root = str(product_dir())
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", f"import sys, json; sys.path.insert(0, {root!r})\n{code}"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if proc.returncode != 0:
        raise ProductUnavailable(f"product import failed: {proc.stderr[-400:]}")
    return json.loads(proc.stdout)


def mentions(text: str, *patterns: str) -> bool:
    """True when `text` matches every regular expression (case-insensitive)."""
    return all(re.search(p, text, re.IGNORECASE) for p in patterns)
