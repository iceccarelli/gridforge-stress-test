"""P1-4: busway racks must respond to occupancy, or the answer must say the existing load is not subtracted. Opt in with `-m product`.

DISCLOSURE oracle.

Defect (verified on GridForge-AI b649b3e7): the busway ampacity limit is "the INSTALLED busway total as declared"; existing load and occupied
positions are not subtracted. On the reference hall the busway constraint permits 12 racks whether `hall.positions_available` is 0, 180 or
540 (560 positions, 560 / 380 / 20 of them occupied). On /v1/screen that is disclosed in the busway constraint's basis ("existing load and
occupied positions are not subtracted, so the rack count is overstated if the figure is not the hall's spare capacity"). On /v1/compare it is
disclosed ONLY by a caveat that fires when the occupied load would exceed the busway rating: on a hall whose busway binds
(`ample_hall(1000, 630)`: 10 racks as found, binding busway_ampacity) with 1,990 of 2,000 positions free, compare reports the same 10 racks as
for 500 free positions and carries no busway caveat at all, although 10 occupied racks at 8 kW would use 80 kW of a 1,428 kW busway and a
subtracted figure would be 9.

Invariant: across occupancy values, busway-bound racks either change with occupancy, or the same response says existing load is not subtracted.
Checked on both routes. The /v1/screen arm is the one the base product already satisfies (it proves the detector recognises the product's own
disclosure and is not "fail always"); the /v1/compare arm is RED on the base product at 1,990 free positions. Not xfail'd.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from ._common import ample_hall, answered, constraint, mutated, reference_doc, scenarios, strings

pytestmark = [pytest.mark.integration, pytest.mark.product]

FREE_POSITIONS = (500, 1500, 1990)  # of 2,000, on the ample hall: 1,500 / 500 / 10 occupied
FIXTURE_FREE_POSITIONS = (
    0,
    180,
    540,
)  # of 560, on the reference hall (the values in the defect report)

NOT_SUBTRACTED = re.compile(
    r"(existing|occupied)[^.]{0,80}\bnot (been )?(subtracted|deducted|netted|taken off|counted)"
    r"|(not|never) (subtract|deduct|net)[^.]{0,60}(existing|occupied)"
    r"|(ignores|does not subtract|doesn't subtract)[^.]{0,40}(existing|occupied)",
    re.IGNORECASE,
)


def says_existing_load_is_not_subtracted(texts: list[str]) -> bool:
    return any(re.search("busway", t, re.IGNORECASE) and NOT_SUBTRACTED.search(t) for t in texts)


def _with_free(doc: dict[str, Any], free: int) -> dict[str, Any]:
    return mutated(doc, lambda d: d["hall"].__setitem__("positions_available", free))


@pytest.fixture(scope="module")
def compare_runs(intake) -> dict[int, dict[str, Any]]:
    return {n: answered("compare", _with_free(ample_hall(1000, 630), n)) for n in FREE_POSITIONS}


@pytest.fixture(scope="module")
def screen_runs(intake) -> dict[int, dict[str, Any]]:
    return {n: answered("screen", _with_free(reference_doc(), n)) for n in FIXTURE_FREE_POSITIONS}


def test_compare_busway_bound_racks_respond_to_occupancy_or_the_answer_says_why_not(compare_runs):
    racks = {n: scenarios(r)["hybrid_dlc"]["as_found"] for n, r in compare_runs.items()}
    assert all(a["binding"] == "busway_ampacity" for a in racks.values()), (
        racks
    )  # the busway is what decides: the test is not vacuous
    if len({a["racks"] for a in racks.values()}) > 1:
        return  # racks respond to occupancy: no disclosure needed
    for n, run in compare_runs.items():
        assert says_existing_load_is_not_subtracted(strings(run)), (
            f"/v1/compare: {racks[n]['racks']} busway-bound racks at {n} free positions are identical to every other occupancy "
            f"{sorted(racks)}, and the response never says the existing load is not subtracted from the busway "
            f"(busway caveats: {[c for c in run['caveats'] if 'usway' in c]})"
        )


def test_screen_busway_racks_respond_to_occupancy_or_the_basis_says_why_not(screen_runs):
    """Satisfied on the base product: the busway constraint's basis carries the disclosure at every occupancy."""
    permitted = {
        n: constraint(scenarios(r)["hybrid_dlc"], "busway_ampacity")["racks_permitted"]
        for n, r in screen_runs.items()
    }
    if len(set(permitted.values())) > 1:
        return
    for n, run in screen_runs.items():
        basis = constraint(scenarios(run)["hybrid_dlc"], "busway_ampacity")["basis"]
        assert says_existing_load_is_not_subtracted([basis]), (
            f"/v1/screen: busway permits {permitted[n]} racks at {n} free positions whatever the occupancy "
            f"{permitted}, and its basis does not say existing load is not subtracted: {basis!r}"
        )


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
