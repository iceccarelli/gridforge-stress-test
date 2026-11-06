"""P2-2: a tap-off loaded to 100% of its rating needs its continuous-load assumption stated. Opt in with `pytest -m product`.

DISCLOSURE oracle. The standard and the factor are the owner's to confirm, so this file asserts no factor (not 1.25, not 0.8): it asserts only
that an assumption is STATED wherever the product accepts a tap-off at its full nameplate rating.

Finding (verified on GridForge-AI b649b3e7): the platform needs 205 A per rack (400 V three-phase). A 204 A tap-off permits 0 racks; a 205 A
tap-off passes (zero margin, 100% of rating). The same product derates the busway to 0.8 and says so ("8 run(s) at 400 A derated to 0.8").
Nothing in /v1/compare or /v1/screen says whether the tap-off is rated for continuous duty, whether a continuous-load factor was applied or
deliberately not applied, or which standard it follows. A rack that draws its rated current for hours, on a tap-off sized to exactly that
current, is the case where the factor decides the answer.

Invariant: when a hall's tap-off rating is exactly the rack's current (the rack is accepted at 100% of the rating), some string in the answer
mentions the tap-off together with a continuous-load / duty-factor assumption. The need is read from the product's own constraint basis
("platform needs N A"), not typed here. Control arm (satisfied on the base product): 1 A below the need the same product refuses the tap-off,
so the boundary the disclosure is about is real. Not xfail'd.
"""

from __future__ import annotations

import math
import re
from typing import Any

import pytest

from ._common import answered, constraint, mutated, scenarios, strings

pytestmark = [pytest.mark.integration, pytest.mark.product]

CONTINUOUS = re.compile(r"continuous|duty factor|125 ?%|1\.25|80 ?%|sustained", re.IGNORECASE)


def states_a_tapoff_continuous_load_assumption(texts: list[str]) -> bool:
    return any(re.search(r"tap-?off", t, re.IGNORECASE) and CONTINUOUS.search(t) for t in texts)


def _rack_need_A(intake: dict[str, Any]) -> int:
    screen = answered("screen", intake)
    basis = constraint(scenarios(screen)["full_dlc"], "rack_feed_tapoff")["basis"]
    m = re.search(r"needs (\d+(?:\.\d+)?)", basis)
    assert m, f"cannot read the rack's current from the tap-off basis: {basis!r}"
    return math.ceil(float(m.group(1)))


def _with_tapoff(intake: dict[str, Any], amps: int) -> dict[str, Any]:
    return mutated(intake, lambda d: d["lv"].__setitem__("tapoff_max_A", amps))


def test_a_tapoff_accepted_at_100_percent_of_its_rating_states_its_continuous_load_assumption(
    intake,
):
    need = _rack_need_A(intake)
    doc = _with_tapoff(intake, need)
    screen, compare = answered("screen", doc), answered("compare", doc)
    full_dlc = scenarios(screen)["full_dlc"]
    assert constraint(full_dlc, "rack_feed_tapoff")["racks_permitted"] is None, (
        "precondition: the tap-off is not what limits, i.e. it accepts the rack at 100% of its rating"
    )
    assert states_a_tapoff_continuous_load_assumption(strings(screen) + strings(compare)), (
        f"a {need} A tap-off is accepted for a rack that needs {need} A (100% of rating), and no string in /v1/screen or /v1/compare "
        "states the tap-off's continuous-load assumption (duty factor / standard); the busway, by contrast, says it is derated to 0.8"
    )


def test_control_one_amp_below_the_need_the_tapoff_is_refused(intake):
    """The boundary the disclosure is about is real (satisfied on the base product): a tap-off 1 A short permits no rack."""
    need = _rack_need_A(intake)
    screen = answered("screen", _with_tapoff(intake, need - 1))
    assert constraint(scenarios(screen)["full_dlc"], "rack_feed_tapoff")["racks_permitted"] == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tap-off units are rated for continuous duty at 80% of nameplate", True),
        ("rack feed: the tap-off is derated by a 1.25 continuous-load factor", True),
        ("installed tap-offs rated 205 A (E5)", False),
        ("8 run(s) at 400 A derated to 0.8", False),  # the busway, not the tap-off
        ("Higher-rated tap-off units (and busway if ampacity does not allow)", False),
    ],
)
def test_the_detector_is_not_vacuous(text, expected):
    assert states_a_tapoff_continuous_load_assumption([text]) is expected
