"""Shared plumbing for the round-3 oracles. Reaches the product only through `product_adapter` (batch handler calls, or its real HTTP
handler on a private loopback port). Inputs are SYNTHETIC: the qualifier's seven-figure hall below, and the harness's reference hall."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any

from gridforge_stress.assumed_fields import FIELD_ORDER
from gridforge_stress.fuzz import default_plans, reference_intake
from gridforge_stress.product_adapter import call_batch

#: A hall the free qualifier answers (all seven figures). Synthetic.
HALL: dict[str, Any] = {
    "contracted_MW": 12,
    "current_site_peak_MW": 7.4,
    "current_it_load_MW": 4.9,
    "busway_ampacity_A": 400,
    "tapoff_max_A": 63,
    "plant_supply_C": 6,
    "positions_available": 180,
}


def qualify_bases() -> dict[str, dict[str, Any]]:
    fuzz_base = next(p for p in default_plans(reference_intake()) if p["name"] == "qualify")["base"]
    return {
        "seven-figure hall": dict(HALL),
        "fuzz plan base": dict(fuzz_base),
        "five figures sent": {
            k: v for k, v in HALL.items() if k not in ("plant_supply_C", "positions_available")
        },
        "positions above the default total": {**HALL, "positions_available": 650},
    }


def provided(body: dict[str, Any]) -> list[str]:
    return [f for f in FIELD_ORDER if body.get(f) is not None]


def subsets(names: list[str]) -> Iterator[tuple[str, ...]]:
    for r in range(len(names) + 1):
        yield from itertools.combinations(names, r)


def qualify_rows(bodies: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{label: batch row with the result} for each body, on the free route as a public caller."""
    return call_batch(
        [
            {"id": k, "route": "/v1/qualify", "tier": "public", "body": b, "want_result": True}
            for k, b in bodies.items()
        ]
    )


def declare(body: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {**body, "assumed_fields": list(names)} if names else dict(body)
