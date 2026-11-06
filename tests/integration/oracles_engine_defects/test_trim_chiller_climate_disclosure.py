"""P1-1: the thermal capex and the PUE must each respond to climate, or the answer must say they do not. Opt in with `pytest -m product`.

DISCLOSURE oracle. The owner has reserved the numeric change, so this file asserts no number: it is satisfied, quantity by quantity, by
either a monotone response or an honest caveat, and it is RED on the base product because the product does neither.

Base behaviour (verified on GridForge-AI b649b3e7): `full_dlc` on the reference hall with `site.design_drybulb_C` 20 / 34 / 45 / 60 gives
the SAME 28 racks after the ladder, the SAME PUE 1.2 [1.15-1.28] (stated in the grid_firm_capacity constraint basis) and the SAME single
"Adiabatic assist plus trim chiller" step of EUR 350,000 / 32 weeks (cost key trim_chiller.adiabatic). Over 0 -> 15 C that step is absent
(the dry coolers can supply the water), so the cost does respond between 15 and 20 C and then not again. The only climate-aware text is the
achievable-supply sentence of the tcs_supply_achievable constraint ("dry coolers at design dry-bulb is estimated to supply 30 ... degC" at
20 C, 70 at 60 C), which does not touch cost, PUE or racks. The step's basis note says the lump "is not scaled with hall size"; it says
nothing about climate, and nothing at all about PUE.

Invariant, over a 40 K design dry-bulb sweep (20 -> 60 C), for EACH of the two quantities separately:
  * thermal capex (the capex of every thermal-domain relief taken on the ladder, per deployable rack, so that a lump replaced by a different
    kind of chiller counts and a per-rack relief does not move with the rack count) with its lead time; and
  * the PUE the product states;
either it responds monotonically (hotter never gives a cheaper or faster thermal plan, or a lower PUE), or some string in the answer says
that quantity does not scale / vary with climate (it must name the trim chiller for the first and the PUE for the second). Racks may never
rise with heat, but a racks response alone satisfies neither quantity. Wording is matched by notions (subject + climate + "does not scale"
in the same string), not by an exact phrase.

Control arm (satisfied on the base product): over 0 -> 20 C the thermal capex responds (the trim step appears as it gets hot). The verdict
function is itself checked on fabricated sweeps, including wrong-direction responses and a response in only one of the two quantities.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from ._common import answered, leaves, mutated, scenarios, strings

pytestmark = [pytest.mark.integration, pytest.mark.product]

SWEEP_C = (
    20,
    27,
    34,
    40,
    45,
    52,
    60,
)  # the 20/34/45/60 sweep plus intermediate points, so that equal-rack pairs exist
CONTROL_SWEEP_C = (0, 5, 10, 15, 20)

CLIMATE = r"climat|dry.?bulb|ambient|outdoor|weather|temperature"
NOT_SCALING = (
    r"not (be )?scal|not (be )?adjust|not a function of|does not (scale|vary|change|respond|depend|adjust|account|move)"
    r"|do not (scale|vary|change|respond|depend|adjust|account|move)|independent of|regardless of|ignores?\b|is fixed|are fixed"
    r"|held constant|\bconstant\b|same at every|unchanged|not vary|not respond"
)

Signature = tuple[
    int, float | None, float, float
]  # racks, PUE, thermal capex EUR per rack, thermal lead time weeks


def _pue(screen_scenario: dict[str, Any]) -> float | None:
    for path, v in leaves(screen_scenario):
        if isinstance(v, str) and (m := re.search(r"\bPUE\b\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)", v)):
            return float(m.group(1))
        if (
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and path.lower().endswith(".pue")
        ):
            return float(v)
    return None


def _signature(screen_scenario: dict[str, Any]) -> Signature:
    racks = screen_scenario["after_ladder"]["racks"]
    thermal = [s for s in screen_scenario["ladder"] if s["taken"] and s["domain"] == "thermal"]
    cost = sum(s["capex_eur"]["value"] for s in thermal if s["capex_eur"])
    weeks = max((s["lead_time_weeks"]["value"] for s in thermal if s["lead_time_weeks"]), default=0)
    return racks, _pue(screen_scenario), float(cost), float(weeks)


def _disclosed(texts: list[str], subject: str) -> bool:
    return any(
        re.search(subject, t, re.IGNORECASE)
        and re.search(CLIMATE, t, re.IGNORECASE)
        and re.search(NOT_SCALING, t, re.IGNORECASE)
        for t in texts
    )


def _direction(name: str, series: list[float], hotter_is_larger: bool) -> list[str]:
    ordered = sorted(series, reverse=not hotter_is_larger)
    if series == ordered:
        return []
    return [
        f"{name} moves the wrong way with heat: {series} ({'rises' if hotter_is_larger else 'falls'} expected)"
    ]


def verdict(sigs: list[Signature], texts: list[str]) -> dict[str, list[str]]:
    """Problems per quantity for a sweep ordered from cool to hot. All lists empty means the invariant holds.

    The thermal capex is the raw sum over every thermal relief taken, so a different kind of chiller at 60 C is counted. Because a
    different rack count changes per-rack items on its own, cost and lead time are compared only between neighbouring points that host the
    same number of racks (when no two neighbours do, per rack instead)."""
    racks = [s[0] for s in sigs]
    pue = [s[1] for s in sigs]
    cost = [s[2] for s in sigs]
    weeks = [s[3] for s in sigs]
    out: dict[str, list[str]] = {"racks": _direction("racks", racks, False), "cost": [], "pue": []}
    pairs = [(i, i + 1) for i in range(len(sigs) - 1) if racks[i] == racks[i + 1]]
    if pairs:
        c_series = [(cost[a], cost[b], weeks[a], weeks[b]) for a, b in pairs]
    else:
        per_rack = [c / max(r, 1) for c, r in zip(cost, racks, strict=True)]
        c_series = [
            (per_rack[i], per_rack[i + 1], weeks[i], weeks[i + 1]) for i in range(len(sigs) - 1)
        ]
    responds = False
    for c0, c1, w0, w1 in c_series:
        if c1 < c0:
            out["cost"].append(
                f"thermal capex falls with heat ({c0:,.0f} -> {c1:,.0f} EUR between neighbouring points)"
            )
        if w1 < w0:
            out["cost"].append(
                f"thermal lead time falls with heat ({w0:g} -> {w1:g} weeks between neighbouring points)"
            )
        responds = responds or c1 != c0 or w1 != w0
    if all(p is not None for p in pue):
        out["pue"] += _direction("PUE", [p for p in pue if p is not None], True)
    if not out["cost"] and not responds and not _disclosed(texts, r"trim|chiller"):
        out["cost"].append(
            f"the thermal capex ({cost[0]:,.0f} EUR) and lead time ({weeks[0]:g} weeks) do not respond across the sweep (at equal racks) "
            "and no string says the trim chiller cost / lead time do not scale with climate"
        )
    if not out["pue"] and len(set(pue)) == 1 and not _disclosed(texts, r"\bPUE\b"):
        out["pue"].append(
            f"the PUE ({pue[0]}) is identical across the sweep and no string says the PUE does not scale with climate"
        )
    return out


def _sweep(intake: dict[str, Any], temps: tuple[int, ...]) -> tuple[list[Signature], list[str]]:
    sigs: list[Signature] = []
    texts: list[str] = []
    for t in temps:
        doc = mutated(intake, lambda d, t=t: d["site"].__setitem__("design_drybulb_C", t))
        screen = answered("screen", doc)
        sigs.append(_signature(scenarios(screen)["full_dlc"]))
        texts += strings(screen) + strings(answered("compare", doc))
    return sigs, texts


def test_full_dlc_thermal_capex_and_pue_each_respond_to_a_40k_sweep_or_say_they_do_not(intake):
    sigs, texts = _sweep(intake, SWEEP_C)
    v = verdict(sigs, texts)
    problems = [p for k in ("racks", "cost", "pue") for p in v[k]]
    assert not problems, (
        f"design dry-bulb {SWEEP_C} C gives (racks, PUE, thermal EUR, thermal weeks) = {sigs}: "
        + "; ".join(problems)
    )


def test_control_the_thermal_capex_responds_to_heat_between_0_and_20_c(intake):
    sigs, texts = _sweep(intake, CONTROL_SWEEP_C)
    assert len({s[2] for s in sigs}) > 1, (
        f"thermal capex does not respond at all over {CONTROL_SWEEP_C} C: {sigs}"
    )
    assert verdict(sigs, texts)["cost"] == []
    assert verdict(sigs, texts)["racks"] == []


FLAT = [(28, 1.2, 12_500.0, 32.0)] * 4
HOT_DEARER = [(28, 1.2, 0.0, 0.0), (28, 1.2, 12_500.0, 32.0), (28, 1.2, 12_500.0, 32.0)]
SAYS_TRIM = "The trim chiller cost does not scale with climate or load."
SAYS_PUE = "PUE is a library figure that does not vary with design dry-bulb."


@pytest.mark.parametrize(
    ("sigs", "texts", "bad_keys"),
    [
        (
            [(28, 1.2, 0.0, 0.0), (28, 1.3, 12_500.0, 32.0)],
            [],
            [],
        ),  # both respond, hotter is dearer: fine
        (HOT_DEARER, [], ["pue"]),  # capex responds only: the PUE needs its own caveat
        (HOT_DEARER, [SAYS_PUE], []),  # ... and a PUE caveat is enough
        ([(28, 1.2, 12_500.0, 32.0), (28, 1.3, 12_500.0, 32.0)], [], ["cost"]),  # PUE responds only
        (FLAT, [], ["cost", "pue"]),  # flat and silent: the defect
        (
            FLAT,
            ["trim chiller lump is not scaled with hall size"],
            ["cost", "pue"],
        ),  # silent on climate
        (FLAT, [SAYS_TRIM], ["pue"]),  # PUE not covered
        (FLAT, [SAYS_PUE], ["cost"]),  # trim not covered
        (FLAT, [SAYS_TRIM, SAYS_PUE], []),
        (
            FLAT,
            [
                "The trim chiller step ignores climate.",
                "PUE is not adjusted for ambient temperature.",
            ],
            [],
        ),
        (
            FLAT,
            [
                "The trim chiller step is a constant, whatever the dry-bulb.",
                "The PUE is constant across climates.",
            ],
            [],
        ),
        (
            [(28, 1.2, 12_500.0, 32.0), (28, 1.2, 0.0, 0.0)],
            [SAYS_PUE],
            ["cost"],
        ),  # hotter yet cheaper
        (
            [(28, 1.2, 12_500.0, 32.0), (28, 1.1, 12_500.0, 32.0)],
            [SAYS_TRIM],
            ["pue"],
        ),  # hotter yet lower PUE
        (
            [(28, 1.2, 0.0, 0.0), (30, 1.2, 0.0, 0.0)],
            [SAYS_TRIM, SAYS_PUE],
            ["racks"],
        ),  # hotter yet more racks
        (
            [(28, None, 0.0, 0.0)] * 2,
            [SAYS_TRIM],
            ["pue"],
        ),  # no PUE to read: treated as flat, needs its caveat
        # a PUE that moves the rack count moves per-rack capex on its own: that is not a response of the trim chiller
        (
            [
                (28, 1.2, 100.0, 32.0),
                (27, 1.23, 96.0, 32.0),
                (27, 1.26, 96.0, 32.0),
                (26, 1.3, 92.0, 32.0),
            ],
            [SAYS_TRIM],
            [],
        ),
        (
            [
                (28, 1.2, 100.0, 32.0),
                (27, 1.23, 96.0, 32.0),
                (27, 1.26, 96.0, 32.0),
                (26, 1.3, 92.0, 32.0),
            ],
            [],
            ["cost"],
        ),
        (
            [(28, 1.2, 100.0, 32.0), (27, 1.3, 130.0, 40.0)],
            [SAYS_PUE],
            [],
        ),  # no equal-rack pair: per rack 3.57 -> 4.81 rises
        (
            [(28, 1.2, 100.0, 32.0), (27, 1.3, 90.0, 32.0)],
            [SAYS_PUE],
            ["cost"],
        ),  # no equal-rack pair: per rack 3.57 -> 3.33 falls
        (
            [(28, 1.2, 100.0, 32.0), (28, 1.3, 90.0, 32.0)],
            [SAYS_PUE],
            ["cost"],
        ),  # equal racks: hotter yet cheaper
    ],
)
def test_the_verdict_function_is_not_vacuous(sigs, texts, bad_keys):
    v = verdict(sigs, texts)
    assert sorted(k for k, ps in v.items() if ps) == sorted(bad_keys), v
