"""Rack-position counts: plain and annotated agree, and an empty hall is not better evidence than an occupied one. Opt in with `pytest -m product`.

TWO ORACLES ON THE POSITION COUNTS OF /v1/screen AND /v1/compare. Semantics pinned from the product's own text, not guessed: `hall.rack_positions`
is the total installed positions; `hall.positions_available` is the FREE ones (schema: "Rack positions available", "Floor plan plus the tenancy
schedule"; loader: "free positions cannot exceed the positions that exist"); the floor-space constraint permits `min(total, free)` racks and
states "N of M positions available"; the busway constraint states that "existing load and occupied positions are not subtracted".

A. PARITY (plain vs annotated). For the free count, the total, and both, each given as a bare number and as `{"value": N, "evidence": E, "note":
   ...}` with E in E0, E3, E5 and no class, over free counts 180 / 300 / 401 / 650 / 5000 and with the total omitted or given: every capacity number
   and label is identical, and a hall the bare numbers make the product REFUSE (free above total) is refused when annotated, never answered.
   Regression of the product fix bd750c8: a free count above the library's default total of 400, annotated, was once read as "not declared" and
   refused where the same bare number was answered.
B. EMPTY vs OCCUPIED. A hall with rack_positions equal to positions_available (zero occupied) against the same hall with k more total positions
   (k occupied), k in 1, 40, 380, 1820, bare and annotated: because the FREE positions are the same and occupied load is documented as not subtracted,
   (1) every capacity number and label is identical (apart from the restated position counts); (2) no stated evidence class is higher for the
   empty hall; (3) every 'not subtracted' disclosure and every warning not about occupied positions that the occupied hall carries, the empty
   hall carries; (4) the intake completeness is equal. "Zero occupied" must not buy a better-looking answer than "some occupied".

OPEN QUESTION FOR THE OWNER (not asserted here, because the product text does not settle it):
  * Is `rack_positions == positions_available` a CLAIM ("nothing is occupied") or a COPY (a form author wrote one number twice, or the total was
    left to its default: the loader sets the default total to max(400, free), so an intake that omits the total reads as zero occupied whenever free
    >= 400)? The engine treats it as plain arithmetic: the busway "occupied positions would draw N kW" warning cannot fire at zero occupied, and
    nothing says "no occupied positions were declared; confirm". The oracle above only forbids it from looking better EVIDENCED; whether it should
    additionally carry a prompt is a product decision.
  * Conversely, with the total OMITTED the engine invents an occupied count from the library default ("the 220 occupied positions ... would draw
    1,760 kW" for free 180, total defaulted to 400): a warning built from a number nobody gave. Observed on the product at 8f944ac on /v1/screen and
    /v1/compare; whether a warning may rest on a default is the owner's call.
Judges: `annotated_intake.parity_violations`, `zero_occupied_violations`; their unit proofs are in tests/unit/test_annotated_intake.py. Not xfail'd.
"""

from __future__ import annotations

from typing import Any

import pytest

from gridforge_stress.annotated_intake import parity_violations, zero_occupied_violations

from ..oracles_engine_defects._common import reference_doc
from ._intake import annotate, rows_for, set_path, without

pytestmark = [pytest.mark.integration, pytest.mark.product]

ROUTES = ("screen", "compare")
FREE = [180, 300, 401, 650, 5000]
CLASSES: list[str | None] = ["E0", "E3", "E5", None]
OCCUPIED = [1, 40, 380, 1820]


def hall(
    free: int, total: int | None, *, free_as: Any = None, total_as: Any = None
) -> dict[str, Any]:
    """The reference hall with the given counts; `free_as`/`total_as` wrap the number in the annotated form with that class (None: no class)."""
    doc = reference_doc()
    doc = set_path(doc, "hall.positions_available", free)
    doc = (
        without(doc, "hall.rack_positions")
        if total is None
        else set_path(doc, "hall.rack_positions", total)
    )
    if free_as is not None:
        doc = set_path(
            doc, "hall.positions_available", annotate(free, None if free_as == "none" else free_as)
        )
    if total_as is not None and total is not None:
        doc = set_path(
            doc, "hall.rack_positions", annotate(total, None if total_as == "none" else total_as)
        )
    return doc


def wrapped(c: str | None) -> str:
    return "none" if c is None else c


def parity_cases() -> dict[str, tuple[str, dict[str, Any]]]:
    cases: dict[str, tuple[str, dict[str, Any]]] = {}
    for route in ROUTES:
        for free in FREE:
            for total in (None, free + 380):
                tag = f"{route}|free={free}|total={total}"
                cases[f"{tag}|plain"] = (route, {"intake": hall(free, total)})
                for c in CLASSES:
                    cases[f"{tag}|free={wrapped(c)}"] = (
                        route,
                        {"intake": hall(free, total, free_as=wrapped(c))},
                    )
                    if total is not None:
                        cases[f"{tag}|total={wrapped(c)}"] = (
                            route,
                            {"intake": hall(free, total, total_as=wrapped(c))},
                        )
                        cases[f"{tag}|both={wrapped(c)}"] = (
                            route,
                            {"intake": hall(free, total, free_as=wrapped(c), total_as=wrapped(c))},
                        )
        # a free count above the total: refused when bare, so refused when annotated
        for c in CLASSES:
            tag = f"{route}|over"
            cases[f"{tag}|plain"] = (route, {"intake": hall(200, 150)})
            cases[f"{tag}|free={wrapped(c)}"] = (
                route,
                {"intake": hall(200, 150, free_as=wrapped(c))},
            )
            cases[f"{tag}|total={wrapped(c)}"] = (
                route,
                {"intake": hall(200, 150, total_as=wrapped(c))},
            )
    return cases


@pytest.fixture(scope="module")
def parity():
    return rows_for(parity_cases())


@pytest.mark.parametrize("route", ROUTES)
def test_annotated_position_counts_give_the_capacity_of_the_bare_counts(parity, route):
    problems = []
    for key, row in parity.items():
        tag, which = key.rsplit("|", 1)
        route_ = key.split("|")[0]
        if route_ != route or which == "plain":
            continue
        field = "hall.rack_positions" if which.startswith("total") else "hall.positions_available"
        if which.startswith("both"):
            field = "hall."  # both are annotated: any line the annotation adds names one of them
        problems += parity_violations(key, parity[f"{tag}|plain"], row, field)
    assert not problems, f"{len(problems)} problems: " + "; ".join(problems[:6])


def test_the_parity_catalogue_has_answered_and_refused_halls(parity):
    """Control arm: the plain halls include answers and refusals, so neither 'always answer' nor 'always refuse' can pass."""
    statuses = {row["status"] for k, row in parity.items() if k.endswith("|plain")}
    assert statuses == {"ok", "clean"}, statuses


def empty_vs_occupied() -> dict[str, tuple[str, dict[str, Any]]]:
    cases: dict[str, tuple[str, dict[str, Any]]] = {}
    for route in ROUTES:
        for free in (180, 650):
            cases[f"{route}|{free}|zero"] = (route, {"intake": hall(free, free)})
            cases[f"{route}|{free}|zero-annotated"] = (
                route,
                {"intake": hall(free, free, free_as="E3", total_as="E3")},
            )
            for k in OCCUPIED:
                cases[f"{route}|{free}|occupied+{k}"] = (route, {"intake": hall(free, free + k)})
                cases[f"{route}|{free}|occupied+{k}-annotated"] = (
                    route,
                    {"intake": hall(free, free + k, free_as="E3", total_as="E3")},
                )
    return cases


@pytest.fixture(scope="module")
def empty():
    return rows_for(empty_vs_occupied())


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("free", [180, 650])
def test_a_hall_with_nothing_occupied_is_not_better_evidenced_than_an_occupied_one(
    empty, route, free
):
    problems = []
    for suffix in ("", "-annotated"):
        zero = empty[f"{route}|{free}|zero{suffix}"]
        assert zero["status"] == "ok", zero
        for k in OCCUPIED:
            occ = empty[f"{route}|{free}|occupied+{k}{suffix}"]
            assert occ["status"] == "ok", occ
            problems += [
                f"occupied+{k}{suffix}: {p}"
                for p in zero_occupied_violations(zero["result"], occ["result"])
            ]
    assert not problems, "; ".join(problems[:6])
