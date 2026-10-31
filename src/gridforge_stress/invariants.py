"""Whole-response invariants over the product's quantities.

Every number the product prints for a modelled quantity is an object {value, low, high, unit, evidence, ...}. Whatever the model does, a quantity
whose best estimate lies outside its own stated range is incoherent: the range is the product's statement of how uncertain the value is.

EVALUATES. For every such object anywhere in a response: all three are finite numbers and low <= value <= high.
DOES NOT EVALUATE. Whether the value or the range is right, or whether a band is wide enough. A range that is wrong but contains its value passes.
INDEPENDENCE. Pure structure checking; no product code and no product constant.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

KEYS = ("value", "low", "high")


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def quantities(node: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    """Every {value, low, high} object in a JSON-like tree, with its path."""
    if isinstance(node, dict):
        if set(KEYS) <= set(node) and all(_is_number(node[k]) for k in KEYS):
            yield path, node
        for k, v in node.items():
            yield from quantities(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from quantities(v, f"{path}[{i}]")


def range_violations(response: Any) -> list[str]:
    out: list[str] = []
    for path, q in quantities(response):
        v, lo, hi = q["value"], q["low"], q["high"]
        label = q.get("label", "")
        if not all(math.isfinite(x) for x in (v, lo, hi)):
            out.append(f"{path} ({label}): non-finite value/low/high {v!r}/{lo!r}/{hi!r}")
        elif not lo <= v <= hi:
            out.append(f"{path} ({label}): value {v:g} outside its own range [{lo:g}, {hi:g}]")
    return out
