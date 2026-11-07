"""P1-4: busway racks must respond to occupancy, in the right direction and by about the occupied load, or the answer must say existing load
is not subtracted. Opt in with `pytest -m product`.

DISCLOSURE oracle with a response arm.

Base behaviour (verified on GridForge-AI b649b3e7): the busway ampacity limit is "the INSTALLED busway total as declared"; existing load and
occupied positions are not subtracted. On the reference hall the busway constraint permits 12 racks whether `hall.positions_available` is
0, 180 or 540 (560 positions, 560 / 380 / 20 of them occupied). On /v1/screen that is disclosed in the busway constraint's basis ("existing
load and occupied positions are not subtracted, so the rack count is overstated"). On /v1/compare it is disclosed ONLY by a caveat that
fires when the occupied load would exceed the busway rating: on a hall whose busway binds (`ample_hall(1000, 630)`: 10 racks as found,
binding busway_ampacity, busway total 1,428 kW) with 1,990 of 2,000 positions free, compare reports the same 10 racks as for 500 free
positions and carries no busway caveat at all, although 10 occupied racks at 8 kW use 80 kW of the 1,428 kW. Both routes are exercised
below on that same hall, with the same judge, so the two arms cannot drift apart: /v1/screen is satisfied on the base product (the
disclosure is in the busway basis at every occupancy), /v1/compare is RED at 1,900, 1,950 and 1,990 free positions (occupied load under the busway rating, so no caveat fires).

Invariant, over free positions 500 / 1,500 / 1,900 / 1,950 / 1,990 (occupied 1,500 / 500 / 100 / 50 / 10):
  * if busway-bound racks are the same at every occupancy, the response must say, in a string that mentions the busway, that existing /
    occupied load is not subtracted; otherwise
  * racks must be NON-INCREASING as occupancy rises, and sit within one rack of what subtracting the occupied load gives
    ((busway kW - occupied x design density) / rack kW, floored at zero), computed here from the intake numbers: "responds" means
    responds by about the load, not merely differs.
Not xfail'd.
"""

from __future__ import annotations

import math
import re
from typing import Any

import pytest

from ._common import (
    ample_hall,
    answered_many,
    constraint,
    mutated,
    product_modules_json,
    scenarios,
    strings,
)

pytestmark = [pytest.mark.integration, pytest.mark.product]

POSITIONS = 2000
FREE_POSITIONS = (500, 1500, 1900, 1950, 1990)

NOT_SUBTRACTED = re.compile(
    r"(existing|occupied)[^.]{0,80}\bnot (been )?(subtracted|deducted|netted|taken off|counted)"
    r"|(not|never) (subtract|deduct|net)[^.]{0,60}(existing|occupied)"
    r"|(ignores|does not subtract|doesn't subtract)[^.]{0,40}(existing|occupied)",
    re.IGNORECASE,
)

Row = tuple[
    int, int, bool, float
]  # occupied positions, racks reported, disclosed, racks expected from subtracting the load


def says_existing_load_is_not_subtracted(texts: list[str]) -> bool:
    return any(re.search("busway", t, re.IGNORECASE) and NOT_SUBTRACTED.search(t) for t in texts)


def judge(rows: list[Row]) -> list[str]:
    """Problems with a busway-vs-occupancy series. Empty means the invariant holds."""
    rows = sorted(rows)
    if len({r[1] for r in rows}) == 1:
        return [
            f"{occ} occupied positions give {racks} busway-bound racks, the same as at every other occupancy, and the response does not "
            "say existing load is not subtracted from the busway"
            for occ, racks, disclosed, _ in rows
            if not disclosed
        ]
    problems = []
    racks = [r[1] for r in rows]
    if racks != sorted(racks, reverse=True):
        problems.append(f"racks {racks} do not fall as occupancy rises {[r[0] for r in rows]}")
    for occ, got, _, expected in rows:
        lo, hi = math.floor(max(expected, 0.0)) - 1, math.ceil(max(expected, 0.0))
        if not max(lo, 0) <= got <= hi:
            problems.append(
                f"{occ} occupied positions: {got} racks, but subtracting the occupied load leaves room for {max(expected, 0.0):.2f}"
            )
    return problems


def _expected_racks(doc: dict[str, Any], occupied: int, rack_kw: float) -> float:
    lv, grid = doc["lv"], doc["grid"]
    volts, amps = _num(lv["voltage_V"]), _num(lv["busway_ampacity_A"])
    phase = math.sqrt(3) if _num(lv["phases"]) == 3 else 1.0
    total_kw = (
        phase
        * volts
        * amps
        * _num(lv["busway_utilisation_limit"])
        * _num(grid["power_factor"])
        * _num(lv["busway_runs"])
        / 1000
    )
    density = _num(doc["hall"]["design_density_kW_per_rack"])
    return (total_kw - occupied * density) / rack_kw


def _num(v: Any) -> float:
    return float(v["value"] if isinstance(v, dict) else v)


@pytest.fixture(scope="module")
def runs(intake) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for n in FREE_POSITIONS:
        doc = mutated(
            ample_hall(1000, 630), lambda d, n=n: d["hall"].__setitem__("positions_available", n)
        )
        cases[f"compare|{n}"] = ("compare", {"intake": doc})
        cases[f"screen|{n}"] = ("screen", {"intake": doc})
    return answered_many(cases)


@pytest.fixture(scope="module")
def rack_kw(intake) -> float:
    lib = product_modules_json(
        "from gridforge.compute.library import GB300_NVL72 as p; print(json.dumps(p.rack_kW.value))"
    )
    return float(lib)


def _rows(runs: dict[str, Any], route: str, rack_kw: float) -> list[Row]:
    rows: list[Row] = []
    for n in FREE_POSITIONS:
        doc = ample_hall(1000, 630)
        run = runs[f"{route}|{n}"]
        hybrid = scenarios(run)["hybrid_dlc"]
        if route == "compare":
            assert hybrid["as_found"]["binding"] == "busway_ampacity", hybrid[
                "as_found"
            ]  # the busway is what decides: not vacuous
            racks = hybrid["as_found"]["racks"]
        else:
            racks = constraint(hybrid, "busway_ampacity")["racks_permitted"]
        occupied = POSITIONS - n
        rows.append(
            (
                occupied,
                racks,
                says_existing_load_is_not_subtracted(strings(run)),
                _expected_racks(doc, occupied, rack_kw),
            )
        )
    return rows


@pytest.mark.parametrize("route", ["compare", "screen"])
def test_busway_bound_racks_respond_to_occupancy_by_about_the_load_or_the_answer_says_why_not(
    runs, rack_kw, route
):
    problems = judge(_rows(runs, route, rack_kw))
    assert not problems, f"/v1/{route}: " + "; ".join(problems)


@pytest.mark.parametrize(
    ("rows", "ok"),
    [
        ([(10, 10, False, 9.98), (50, 10, False, 7.6)], False),  # flat and silent: the defect
        ([(10, 10, True, 9.98), (50, 10, True, 7.6)], True),  # flat but disclosed everywhere
        ([(10, 10, True, 9.98), (50, 10, False, 7.6)], False),  # disclosed at one occupancy only
        (
            [(10, 9, False, 9.98), (50, 7, False, 7.6), (100, 4, False, 4.35)],
            True,
        ),  # responds by about the load
        (
            [(10, 10, False, 9.98), (50, 9, False, 7.6), (100, 9, False, 4.35)],
            False,
        ),  # responds, but far too little
        ([(10, 10, False, 9.98), (50, 12, False, 7.6)], False),  # wrong way: more racks when fuller
        ([(10, 9, False, 9.98), (50, 0, False, 7.6)], False),  # far too much
        (
            [(1500, 0, False, -3.0), (500, 3, False, 3.2), (10, 9, False, 9.98)],
            True,
        ),  # saturates at zero when over-full
    ],
)
def test_the_judge_is_not_vacuous(rows, ok):
    assert (judge(rows) == []) is ok, judge(rows)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Modelling limit: the figure is the INSTALLED busway total as declared; existing load and occupied positions are not subtracted",
            True,
        ),
        (
            "Busway: busway_ampacity_A is read as the installed total and existing load is not subtracted, so check",
            True,
        ),
        ("Busway: the 380 occupied positions would draw more than the busway can carry", False),
        ("busway ampacity derated to 0.8", False),
        ("existing load is not subtracted from the grid headroom", False),  # not about the busway
    ],
)
def test_the_disclosure_detector_is_not_vacuous(text, expected):
    assert says_existing_load_is_not_subtracted([text]) is expected
