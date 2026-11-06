"""P1-1: the trim chiller and the PUE must respond to climate, or the answer must say they do not. Opt in with `pytest -m product`.

DISCLOSURE oracle. The owner has reserved the numeric change, so this file asserts no number: it is satisfied by either a monotone response
or an honest caveat, and it is RED on the base product because the product does neither.

Defect (verified on GridForge-AI b649b3e7): `full_dlc` on the reference hall with `site.design_drybulb_C` 20 / 34 / 45 / 60 gives the SAME 28
racks after the ladder, the SAME PUE 1.2 [1.15-1.28] (constraint basis of grid_firm_capacity) and the SAME single "Adiabatic assist plus trim
chiller" step of EUR 350,000 / 32 weeks. The only climate-aware text is the achievable-supply sentence of the tcs_supply_achievable constraint
("dry coolers at design dry-bulb is estimated to supply 30 ... degC" at 20 C, 70 at 60 C), which does not touch cost, PUE or racks. The step's
own basis note says the lump "is not scaled with hall size"; it says nothing about climate, and nothing at all about PUE.

Invariant: across a 40 K design dry-bulb sweep (20 -> 60 C), EITHER racks, PUE, trim-chiller cost or trim-chiller lead time respond
monotonically (hotter never gives more racks, a lower PUE, a cheaper or a faster trim chiller), OR the answer carries a caveat / limitation
string saying that the trim-chiller cost, and the PUE, do not scale with climate. Whichever of cost/lead time and PUE is flat must be named.

Control arm (satisfied on the base product): over 0 -> 20 C the trim-chiller step appears (none at <= 15 C, EUR 350,000 from 20 C), a monotone
response, so the verdict function passes a product that responds; and the verdict function is itself checked on fabricated sweeps.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from ._common import answered, mutated, scenarios, strings

pytestmark = [pytest.mark.integration, pytest.mark.product]

SWEEP_C = (20, 34, 45, 60)
CONTROL_SWEEP_C = (0, 5, 10, 15, 20)

CLIMATE = r"climat|dry.?bulb|ambient|outdoor|weather|temperature"
NOT_SCALING = (
    r"not (be )?scal|does not (scale|vary|change|respond|depend)|do not (scale|vary|change|respond|depend)"
    r"|independent of|regardless of|is fixed|are fixed|held constant|same at every"
)

Signature = tuple[int, float, float, float]  # racks, PUE, trim-chiller EUR, trim-chiller weeks


def _signature(screen_scenario: dict[str, Any]) -> Signature:
    racks = screen_scenario["after_ladder"]["racks"]
    pues = [
        float(m.group(1))
        for c in screen_scenario["constraints"]
        if (m := re.search(r"PUE (\d+(?:\.\d+)?)", c["basis"]))
    ]
    assert pues, "no PUE stated in any constraint basis of the screen"
    trim = [
        s
        for s in screen_scenario["ladder"]
        if s["cost_key"] == "trim_chiller.adiabatic" and s["taken"]
    ]
    cost = sum(s["capex_eur"]["value"] for s in trim)
    weeks = max((s["lead_time_weeks"]["value"] for s in trim), default=0)
    return racks, pues[0], float(cost), float(weeks)


def _disclosed(texts: list[str], subject: str) -> bool:
    return any(
        re.search(subject, t, re.IGNORECASE)
        and re.search(CLIMATE, t, re.IGNORECASE)
        and re.search(NOT_SCALING, t, re.IGNORECASE)
        for t in texts
    )


def verdict(sigs: list[Signature], texts: list[str]) -> list[str]:
    """Problems with a sweep ordered from cool to hot. Empty means the invariant holds."""
    problems: list[str] = []
    racks, pue, cost, weeks = ([s[i] for s in sigs] for i in range(4))
    for name, series, hotter_is_larger in (
        ("racks", racks, False),
        ("PUE", pue, True),
        ("trim-chiller cost", cost, True),
        ("trim-chiller lead time", weeks, True),
    ):
        ordered = sorted(series, reverse=not hotter_is_larger)
        if series != ordered:
            problems.append(
                f"{name} moves the wrong way with heat: {series} ({'rises' if hotter_is_larger else 'falls'} expected)"
            )
    if problems:
        return problems
    responds = any(len(set(series)) > 1 for series in (racks, pue, cost, weeks))
    if responds:
        return []
    if not _disclosed(texts, r"trim"):
        problems.append(
            "racks, PUE and the trim-chiller cost and lead time are identical across the sweep, and no string says the trim-chiller "
            "cost / lead time do not scale with climate"
        )
    if not _disclosed(texts, r"\bPUE\b"):
        problems.append(
            "the PUE is identical across the sweep, and no string says the PUE does not scale with climate"
        )
    return problems


def _sweep(intake: dict[str, Any], temps: tuple[int, ...]) -> tuple[list[Signature], list[str]]:
    sigs: list[Signature] = []
    texts: list[str] = []
    for t in temps:
        doc = mutated(intake, lambda d, t=t: d["site"].__setitem__("design_drybulb_C", t))
        screen = answered("screen", doc)
        sigs.append(_signature(scenarios(screen)["full_dlc"]))
        texts += strings(screen) + strings(answered("compare", doc))
    return sigs, texts


def test_full_dlc_either_responds_to_a_40k_dry_bulb_sweep_or_says_it_does_not(intake):
    sigs, texts = _sweep(intake, SWEEP_C)
    problems = verdict(sigs, texts)
    assert not problems, (
        f"design dry-bulb {SWEEP_C} C gives (racks, PUE, trim EUR, trim weeks) = {sigs}: "
        + "; ".join(problems)
    )


def test_control_the_product_responds_between_0_and_20_c_and_the_verdict_accepts_it(intake):
    sigs, texts = _sweep(intake, CONTROL_SWEEP_C)
    assert sigs[0][2] == 0.0 and sigs[-1][2] > 0.0, (
        sigs
    )  # the trim-chiller step appears as it gets hot
    assert verdict(sigs, texts) == []


@pytest.mark.parametrize(
    ("sigs", "texts", "ok"),
    [
        (
            [(28, 1.2, 0.0, 0.0), (28, 1.2, 350e3, 32.0)],
            [],
            True,
        ),  # cost and lead time respond, hotter is dearer: fine
        (
            [(30, 1.2, 0.0, 0.0), (28, 1.25, 0.0, 0.0)],
            [],
            True,
        ),  # racks fall and PUE rises with heat: fine
        ([(28, 1.2, 350e3, 32.0)] * 4, [], False),  # flat and silent: the defect
        (
            [(28, 1.2, 350e3, 32.0)] * 4,
            ["trim chiller lump is not scaled with hall size"],
            False,
        ),  # silent on climate
        (
            [(28, 1.2, 350e3, 32.0)] * 4,
            [
                "The trim chiller cost does not scale with climate or load.",
                "PUE is a library figure that does not vary with design dry-bulb.",
            ],
            True,
        ),
        (
            [(28, 1.2, 350e3, 32.0)] * 4,
            ["The trim chiller cost does not scale with climate."],
            False,
        ),  # PUE not covered
        ([(28, 1.2, 350e3, 32.0), (28, 1.2, 0.0, 0.0)], [], False),  # hotter yet cheaper
        ([(28, 1.2, 0.0, 0.0), (30, 1.2, 0.0, 0.0)], [], False),  # hotter yet more racks
    ],
)
def test_the_verdict_function_is_not_vacuous(sigs, texts, ok):
    assert (verdict(sigs, texts) == []) is ok
