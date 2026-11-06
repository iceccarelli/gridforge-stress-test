"""P2-4: a hall inside the documented ceilings is answered, or refused by field; never "the ladder did not converge". Opt in with `-m product`.

Defect (verified on GridForge-AI b649b3e7): take the reference hall and scale `grid.firm_capacity_MVA`, `grid.contracted_MW` (x 12/14) and the
transformer unit rating (half the supply each, 3 units N+1) to 150, 170, 200 or 300 MVA with `hall.rack_positions` 20,000. Every number is
inside the product's own plausibility ceilings (loader.PLAUSIBLE_MAX: 5,000 MVA / MW of supply, 1,000 MVA per transformer unit, 50,000 rack
positions, checked below), so the intake loader accepts the hall. /v1/compare and /v1/screen then fail inside the engine with
`LadderDidNotConverge: the headroom ladder was still finding reliefs after 200 steps` (the same hall at 130 MVA is answered: 592 / 641 / 676
racks). That message names no field and no ceiling and tells the caller nothing they can change; "steps" and "converge" are the engine's
internals. Because LadderDidNotConverge subclasses ValueError it is also counted as a clean refusal by this harness's fuzz classification
(`_fuzz_runner.py`, `clean_types` includes ValueError, and `product_adapter._BATCH_RUNNER` does the same), so `gridforge-stress fuzz` cannot
see it: the last test below pins that gap at the oracle level without touching fuzz.py.

Invariant: for a hall inside the documented ceilings the product either ANSWERS, or REFUSES with a message that names an intake field (or the
ceiling it exceeded) and does not talk about the engine's step budget. Satisfied on the base product at 130 MVA (the answered arm). Not xfail'd.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from gridforge_stress.product_adapter import call, call_batch

from ._common import leaves, mutated, product_modules_json

pytestmark = [pytest.mark.integration, pytest.mark.product]

SIZES_MVA = [130, 150, 170, 200, 300]
INTERNAL = re.compile(r"converge|\bsteps?\b|ladder|iteration|budget", re.IGNORECASE)


def scaled_hall(intake: dict[str, Any], mva: float) -> dict[str, Any]:
    def edit(d: dict[str, Any]) -> None:
        d["grid"]["firm_capacity_MVA"] = mva
        d["grid"]["contracted_MW"] = round(mva * 12 / 14, 1)
        d["transformers"][0]["unit_rating_MVA"] = mva / 2
        d["hall"]["rack_positions"] = 20000
        d["hall"]["positions_available"] = 18000

    return mutated(intake, edit)


def intake_field_names(doc: dict[str, Any]) -> set[str]:
    """Every key the caller wrote anywhere in the intake: a refusal that quotes one names a field they can change."""
    names: set[str] = set()
    for path, _ in leaves(doc):
        names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", path))
    # generic words ("grid", "id", "note") are not field names; the product's are snake_case or carry a unit suffix (rack_positions, contracted_MW)
    return {n for n in names if "_" in n}


def names_a_field_or_ceiling(message: str, doc: dict[str, Any]) -> bool:
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", message))
    return bool(words & intake_field_names(doc)) or bool(
        re.search(r"ceiling|the most this tool will model", message, re.IGNORECASE)
    )


@pytest.mark.parametrize("mva", SIZES_MVA)
def test_the_scaled_halls_are_inside_the_documented_ceilings(intake, mva):
    """Precondition, read from the product itself: no scaled figure exceeds loader.PLAUSIBLE_MAX, so the refusal route is not the expected one."""
    ceilings = product_modules_json(
        "from gridforge.intake import loader; print(json.dumps(loader.PLAUSIBLE_MAX))"
    )
    doc = scaled_hall(intake, mva)
    assert doc["grid"]["firm_capacity_MVA"] <= ceilings["grid.firm_capacity_MVA"]
    assert doc["grid"]["contracted_MW"] <= ceilings["grid.contracted_MW"]
    assert doc["transformers"][0]["unit_rating_MVA"] <= ceilings["transformers.unit_rating_MVA"]
    assert doc["hall"]["rack_positions"] <= ceilings["hall.rack_positions"]
    assert doc["hall"]["positions_available"] <= ceilings["hall.positions_available"]


@pytest.mark.parametrize("route", ["compare", "screen"])
@pytest.mark.parametrize("mva", SIZES_MVA)
def test_a_hall_inside_the_ceilings_is_answered_or_refused_naming_a_field(intake, route, mva):
    doc = scaled_hall(intake, mva)
    out = call(route, {"intake": doc})
    if out["ok"]:
        assert out["result"]["scenarios"], out
        return
    message = out["message"]
    assert not INTERNAL.search(message), (
        f"/v1/{route} at {mva} MVA (inside every ceiling) failed with the engine's internal message "
        f"{out['error']}: {message!r}"
    )
    assert names_a_field_or_ceiling(message, doc), (
        f"/v1/{route} at {mva} MVA refused without naming an intake field or ceiling: {out['error']}: {message!r}"
    )


@pytest.mark.parametrize("mva", SIZES_MVA)
def test_the_fuzz_clean_class_is_not_a_refusal_for_an_engine_internal_failure(intake, mva):
    """`call_batch` classifies outcomes exactly as `gridforge-stress fuzz` does. A 'clean' row must be a caller-facing refusal (IntakeError /
    ApiError); a ValueError subclass raised by the engine's own solver is not, even though the fuzz would count it as one."""
    row = call_batch(
        [
            {
                "id": "x",
                "route": "/v1/compare",
                "tier": "client",
                "body": {"intake": scaled_hall(intake, mva)},
            }
        ]
    )["x"]
    assert row["status"] in {"ok", "clean"}, row
    if row["status"] == "clean":
        assert row["error"] in {"IntakeError", "ApiError"}, (
            f"{mva} MVA: counted as a clean refusal by the fuzz classification, but it is {row['error']}: {row['message']!r}"
        )
