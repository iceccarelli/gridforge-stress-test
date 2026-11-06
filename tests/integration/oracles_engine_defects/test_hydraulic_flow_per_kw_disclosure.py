"""Hydraulics: the per-kW flow the hydraulic limit uses must be stated as a number, and the platform and library figures must agree or be
reconciled. DISCLOSURE oracle plus a unit-level oracle. Opt in with `pytest -m product`.

Base behaviour (verified on GridForge-AI b649b3e7): the hydraulic_flow constraint divides the available pumped flow by the platform's
`flow_l_per_min_per_rack`, 130 l/min for the GB300 NVL72's 135 kW rack = 0.963 l/min per kW (1.070 per kW of the 90% liquid-captured heat).
The product's own thermal library holds `FLOW_L_PER_MIN_PER_KW` = 1.45 l/min/kW "at 10 K delta-T" (band 1.4-1.5), which no calculation
reads, and plain physics for water at 10 K is ~1.43 l/min per kW (a flow of 130 l/min for 135 kW implies a delta-T of ~14.9 K, or ~13.4 K on
the liquid share: physically plausible, but not the 10 K the library constant is stated at). Using 1.45 instead of 0.963 would cut the
reference hall's hydraulic limit from 69 racks to ~45. The constraint's basis states "130 [120-140] l/min (E0) per rack" and never the per-kW
figure, the delta-T it implies, or that it differs from the library constant.

Invariants:
  1. The hydraulic_flow basis states the per-kW flow as a NUMBER next to its unit ("0.96 l/min per kW", "0.96 l/min/kW"), and the number is the
     one the platform implies (flow / rack kW, or flow / liquid-captured kW) to the precision it prints (within one unit of its last digit):
     a wrong number, or a number about something else ("EUR 40 per kW"), does not count.
  2. Unit level, on the product's own library points (never their bands, which a widened band would satisfy): the platform's implied
     l/min/kW under either reading lies inside the library constant's band, OR the answer discloses that they differ. Independently of
     the library, the implied delta-T (60 / (rho x cp x l/min per kW), rho 0.997 kg/l, cp 4.19 kJ/kg/K) must be a physical 5-20 K, or the
     answer must disclose it. A product that removes the library constant has nothing to disagree with and passes the first half.
Both are RED on the base product. Not xfail'd.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from ._common import answered, constraint, product_modules_json, scenarios, strings

pytestmark = [pytest.mark.integration, pytest.mark.product]

RHO_KG_PER_L = 0.997
CP_KJ_PER_KG_K = 4.19
PER_KW_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*l/min(?:/| per )\s*kW", re.IGNORECASE)
RECONCILES = re.compile(
    r"differ|discrepan|inconsisten|reconcil|disagree|does not match|delta-?T of \d|implies an? \d+(?:\.\d+)? ?K\b",
    re.IGNORECASE,
)


def per_kw_numbers(text: str) -> list[str]:
    return PER_KW_NUMBER.findall(text)


def matches_to_printed_precision(printed: str, value: float) -> bool:
    """`printed` rounds `value` to the decimals it shows, give or take one unit of its last digit."""
    decimals = len(printed.split(".")[1]) if "." in printed else 0
    return abs(float(printed) - value) <= 10**-decimals


def implied_delta_t_K(l_per_min_per_kw: float) -> float:
    return 60.0 / (RHO_KG_PER_L * CP_KJ_PER_KG_K * l_per_min_per_kw)


def _platform_and_library() -> dict[str, Any]:
    out: dict[str, Any] = product_modules_json(
        "from gridforge.compute.library import GB300_NVL72 as p\n"
        "try:\n"
        "    from gridforge.thermal.library import FLOW_L_PER_MIN_PER_KW as k\n"
        "    lib = [k.value, *k.band]\n"
        "except Exception:\n"
        "    lib = None\n"
        "print(json.dumps({'flow': p.flow_l_per_min_per_rack.value, 'kw': p.rack_kW.value,"
        " 'liquid_fraction': p.liquid_fraction.value, 'library': lib}))"
    )
    return out


def _readings(p: dict[str, Any]) -> dict[str, float]:
    return {
        "per kW of rack power": p["flow"] / p["kw"],
        "per kW of liquid-captured heat": p["flow"] / (p["kw"] * p["liquid_fraction"]),
    }


def test_the_hydraulic_flow_basis_states_the_per_kw_flow_it_uses_as_a_number(intake):
    basis = constraint(scenarios(answered("screen", intake))["full_dlc"], "hydraulic_flow")["basis"]
    stated = per_kw_numbers(basis)
    assert stated, (
        "the hydraulic_flow basis gives the flow per RACK only; it never states a number of l/min per kW (130 l/min for 135 kW is "
        f"0.963 l/min/kW, against 1.45 in the library and ~1.43 from physics at 10 K): {basis!r}"
    )
    readings = _readings(_platform_and_library())
    assert any(matches_to_printed_precision(s, v) for s in stated for v in readings.values()), (
        f"the basis states {stated} l/min per kW, but the platform's flow implies {readings}: {basis!r}"
    )


def test_platform_and_library_flow_per_kw_agree_and_are_physical_or_the_answer_discloses_the_difference(
    intake,
):
    p = _platform_and_library()
    readings = _readings(p)
    problems = []
    lib = p["library"]
    if lib is not None and not any(lib[1] <= v <= lib[2] for v in readings.values()):
        problems.append(
            f"the platform implies {', '.join(f'{v:.3f} l/min/kW {k}' for k, v in readings.items())}; the library constant is "
            f"{lib[0]:g} [{lib[1]:g}-{lib[2]:g}] l/min/kW and neither reading is inside its band"
        )
    if not any(5.0 <= implied_delta_t_K(v) <= 20.0 for v in readings.values()):
        problems.append(
            "the platform's flow implies a delta-T of "
            + ", ".join(f"{implied_delta_t_K(v):.1f} K" for v in readings.values())
            + ", outside a physical 5-20 K"
        )
    if not problems:
        return
    texts = strings(answered("screen", intake)) + strings(answered("compare", intake))
    disclosed = any(
        PER_KW_NUMBER.search(t) and RECONCILES.search(t) and re.search(r"flow", t, re.IGNORECASE)
        for t in texts
    )
    assert disclosed, "; ".join(problems) + "; nothing in /v1/screen or /v1/compare says so"


@pytest.mark.parametrize(
    ("basis", "stated"),
    [
        (
            "9,000 l/min available against 130 l/min per rack = 0.96 l/min/kW at a 14 K delta-T",
            ["0.96"],
        ),
        ("flow 1.07 l/min per kW of liquid heat", ["1.07"]),
        ("(pipework cost EUR 40 per kW)", []),
        ("9,000 l/min (E3) available against 130 [120-140] l/min (E0) per rack", []),
    ],
)
def test_the_per_kw_reader_is_not_vacuous(basis, stated):
    assert per_kw_numbers(basis) == stated


@pytest.mark.parametrize(
    ("printed", "value", "ok"),
    [
        ("0.96", 0.963, True),
        ("0.963", 0.963, True),
        ("1.07", 1.0699, True),
        ("1.45", 0.963, False),
        ("1", 0.963, True),
        ("2", 0.963, False),
    ],
)
def test_the_precision_match_is_not_vacuous(printed, value, ok):
    assert matches_to_printed_precision(printed, value) is ok


@pytest.mark.parametrize(
    ("q", "plausible"), [(0.963, True), (1.45, True), (0.2, False), (4.0, False)]
)
def test_the_delta_t_check_is_not_vacuous(q, plausible):
    assert (5.0 <= implied_delta_t_K(q) <= 20.0) is plausible
