"""P2-2: a tap-off loaded to 100% of its rating needs its continuous-load assumption stated. Opt in with `pytest -m product`.

DISCLOSURE oracle. The standard and the factor are the owner's to confirm, so this file asserts no factor (not 1.25, not 0.8): it asserts
that an assumption is STATED wherever the product accepts a tap-off at its full nameplate rating, and that if the product does apply a
factor it states it and applies exactly that.

Base behaviour (verified on GridForge-AI b649b3e7): the platform needs 205 A per rack (400 V three-phase). A 204 A tap-off permits 0 racks;
a 205 A tap-off passes (zero margin, 100% of rating). The same product derates the busway to 0.8 and says so ("8 run(s) at 400 A derated to
0.8"). Nothing in /v1/compare or /v1/screen says whether the tap-off is rated for continuous duty, whether a continuous-load factor was
applied or deliberately not applied, or which standard it follows. A rack that draws its rated current for hours, on a tap-off sized to
exactly that current, is the case where the factor decides the answer.

Invariant, at a tap-off rated at the rack's current (read from the product's own constraint basis, "platform needs N A", not typed here):
  * if the tap-off is accepted (racks_permitted is None), some string in the answer mentions the tap-off together with a continuous-load /
    duty / derating notion AND a factor, a percentage or "no derating"; "Tap-off: sustained." is not a statement;
  * if it is refused, the basis must state the factor it applied (a percentage or a decimal factor), and a tap-off rated at the rack's
    current times that stated factor must be accepted while one amp less is refused: the product does what it says.
Control arm (satisfied on the base product): one amp below the need the same product refuses the tap-off. Not xfail'd.
"""

from __future__ import annotations

import math
import re
from typing import Any

import pytest

from ._common import answered, constraint, mutated, scenarios, strings

pytestmark = [pytest.mark.integration, pytest.mark.product]

CONTINUOUS = re.compile(r"continuous|duty|derat|load factor|125 ?%|1\.25|80 ?%", re.IGNORECASE)
FACTOR_OR_NONE = re.compile(
    r"\bfactor\b|\d+(?:\.\d+)? ?%|\b[01]\.\d+\b|no derating|not derated|without derating|nameplate|full rating",
    re.IGNORECASE,
)


def states_a_tapoff_continuous_load_assumption(texts: list[str]) -> bool:
    return any(
        re.search(r"tap-?off", t, re.IGNORECASE)
        and CONTINUOUS.search(t)
        and FACTOR_OR_NONE.search(t)
        for t in texts
    )


def stated_factor(basis: str) -> float | None:
    """The continuous-load factor a basis states: 125% or 1.25 -> 1.25; 80% (a derating of the rating) -> 1.25 as well."""
    if m := re.search(r"(\d+(?:\.\d+)?) ?%", basis):
        pct = float(m.group(1))
        return pct / 100 if pct > 100 else (100 / pct if 0 < pct < 100 else None)
    if m := re.search(r"\b(1\.\d+)\b(?! ?A\b)", basis.split("rated", 1)[-1]):
        return float(m.group(1))
    return None


def _screen_tapoff(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    screen = answered("screen", doc)
    return screen, constraint(scenarios(screen)["full_dlc"], "rack_feed_tapoff")


def _rack_need_A(intake: dict[str, Any]) -> float:
    _, row = _screen_tapoff(intake)
    m = re.search(r"needs (\d+(?:\.\d+)?)", row["basis"])
    assert m, f"cannot read the rack's current from the tap-off basis: {row['basis']!r}"
    return float(m.group(1))


def _with_tapoff(intake: dict[str, Any], amps: int) -> dict[str, Any]:
    return mutated(intake, lambda d: d["lv"].__setitem__("tapoff_max_A", amps))


def test_a_tapoff_at_the_rack_current_states_its_continuous_load_assumption_or_the_factor_it_applies(
    intake,
):
    need = math.ceil(_rack_need_A(intake))
    doc = _with_tapoff(intake, need)
    screen, row = _screen_tapoff(doc)
    if row["racks_permitted"] is None:
        texts = strings(screen) + strings(answered("compare", doc))
        assert states_a_tapoff_continuous_load_assumption(texts), (
            f"a {need} A tap-off is accepted for a rack that needs {need} A (100% of rating), and no string in /v1/screen or "
            "/v1/compare states the tap-off's continuous-load assumption (a continuous / duty / derating notion with a factor or "
            "'no derating'); the busway, by contrast, says it is derated to 0.8"
        )
        return
    factor = stated_factor(row["basis"])
    assert factor, (
        f"a {need} A tap-off is REFUSED for a rack that needs {need} A, but the basis states no continuous-load factor: {row['basis']!r}"
    )
    accepted_at = math.ceil(_rack_need_A(intake) * factor)
    assert _screen_tapoff(_with_tapoff(intake, accepted_at))[1]["racks_permitted"] is None, (
        f"the basis states a factor of {factor:g}, so a {accepted_at} A tap-off should carry the rack, but it is refused"
    )
    assert _screen_tapoff(_with_tapoff(intake, accepted_at - 1))[1]["racks_permitted"] == 0, (
        f"a {accepted_at - 1} A tap-off is accepted although the stated factor of {factor:g} needs {accepted_at} A"
    )


def test_control_one_amp_below_the_need_the_tapoff_is_refused(intake):
    """The boundary the disclosure is about is real (satisfied on the base product): a tap-off 1 A short permits no rack."""
    need = math.ceil(_rack_need_A(intake))
    assert _screen_tapoff(_with_tapoff(intake, need - 1))[1]["racks_permitted"] == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tap-off units are rated for continuous duty at 80% of nameplate", True),
        ("rack feed: the tap-off is derated by a 1.25 continuous-load factor", True),
        (
            "Tap-off units are assumed rated for continuous duty at 100% of nameplate; no continuous-load derating is applied.",
            True,
        ),
        ("Tap-off: sustained.", False),
        ("Tap-off: continuous.", False),  # a notion, but no factor and no "no derating"
        ("installed tap-offs rated 205 A (E5)", False),
        ("8 run(s) at 400 A derated to 0.8", False),  # the busway, not the tap-off
        ("Higher-rated tap-off units (and busway if ampacity does not allow)", False),
    ],
)
def test_the_detector_is_not_vacuous(text, expected):
    assert states_a_tapoff_continuous_load_assumption([text]) is expected


@pytest.mark.parametrize(
    ("basis", "factor"),
    [
        (
            "platform needs 205 A per rack; installed tap-offs rated 205 A (E5); a 125% continuous-load factor is applied",
            1.25,
        ),
        ("installed tap-offs rated 205 A; continuous-load factor 1.25", 1.25),
        ("installed tap-offs rated 250 A (E5), derated to 80% of nameplate", 1.25),
        ("platform needs 205 A per rack; installed tap-offs rated 205 A (E5)", None),
    ],
)
def test_the_factor_reader_is_not_vacuous(basis, factor):
    assert stated_factor(basis) == factor
