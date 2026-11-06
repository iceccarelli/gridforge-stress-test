"""Hydraulics: the per-kW flow the hydraulic limit uses must be stated, and the platform and library figures must agree or be reconciled.

DISCLOSURE oracle (plus one unit-level oracle). Opt in with `pytest -m product`.

Finding (verified on GridForge-AI b649b3e7): the hydraulic_flow constraint divides the available pumped flow by the platform's
`flow_l_per_min_per_rack`, 130 l/min for the GB300 NVL72's 135 kW rack = 0.963 l/min per kW. The product's own thermal library holds
`FLOW_L_PER_MIN_PER_KW` = 1.45 l/min/kW "at 10 K delta-T" (band 1.4-1.5), which no calculation reads, and plain physics for water at 10 K is
~1.43 l/min per kW. Using 1.45 instead of 0.963 would cut the reference hall's hydraulic limit from 69 racks to ~45. The constraint's basis
states "130 [120-140] l/min (E0) per rack" and never the per-kW figure, the delta-T it implies (~15 K on the total heat, ~13 K on the 90%
liquid share), or that it differs from the library constant.

Invariants:
  1. The hydraulic_flow basis states the per-kW flow used ("l/min/kW" or "per kW").
  2. Unit level: the platform's implied flow per kW (flow / rack kW, and flow / liquid-captured kW) and the library's per-kW constant are
     mutually consistent, judged by overlap of the product's OWN declared bands (no tolerance is added here), OR the answer discloses the
     difference. Both are RED on the base product. Control (satisfied on the base product): the detector accepts a basis that does state it.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from ._common import answered, constraint, product_modules_json, scenarios, strings

pytestmark = [pytest.mark.integration, pytest.mark.product]

PER_KW = re.compile(r"l/min/kW|l/min per kW|litres? per minute per kW|per kW\b", re.IGNORECASE)
RECONCILES = re.compile(
    r"differ|discrepan|inconsisten|reconcil|disagree|does not match", re.IGNORECASE
)


def states_per_kw_flow(basis: str) -> bool:
    return bool(PER_KW.search(basis))


def _hydraulic_basis(intake: dict[str, Any]) -> str:
    return str(
        constraint(scenarios(answered("screen", intake))["full_dlc"], "hydraulic_flow")["basis"]
    )


def test_the_hydraulic_flow_basis_states_the_per_kw_flow_it_uses(intake):
    basis = _hydraulic_basis(intake)
    assert states_per_kw_flow(basis), (
        "the hydraulic_flow basis gives the flow per RACK only; it never states the per-kW flow used (130 l/min for 135 kW is "
        f"0.963 l/min/kW, against 1.45 in the library and ~1.43 from physics at 10 K): {basis!r}"
    )


def test_platform_and_library_flow_per_kw_agree_or_the_answer_discloses_the_difference(intake):
    lib = product_modules_json(
        "from gridforge.compute.library import GB300_NVL72 as p\n"
        "from gridforge.thermal.library import FLOW_L_PER_MIN_PER_KW as k\n"
        "print(json.dumps({'flow': [p.flow_l_per_min_per_rack.value, *p.flow_l_per_min_per_rack.band],"
        " 'kw': [p.rack_kW.value, *p.rack_kW.band], 'liquid_fraction': [p.liquid_fraction.value, *p.liquid_fraction.band],"
        " 'library': [k.value, *k.band]}))"
    )
    (flow_v, flow_lo, flow_hi), (kw_v, kw_lo, kw_hi) = lib["flow"], lib["kw"]
    (lf_v, lf_lo, lf_hi), (lib_v, lib_lo, lib_hi) = lib["liquid_fraction"], lib["library"]
    implied = {  # (point, low, high) of l/min per kW under each reading of "per kW"
        "per kW of rack power": (flow_v / kw_v, flow_lo / kw_hi, flow_hi / kw_lo),
        "per kW of liquid-captured heat": (
            flow_v / (kw_v * lf_v),
            flow_lo / (kw_hi * lf_hi),
            flow_hi / (kw_lo * lf_lo),
        ),
    }
    overlapping = {k: v for k, v in implied.items() if v[1] <= lib_hi and lib_lo <= v[2]}
    if overlapping:
        return  # the figures agree within the product's own declared bands
    texts = strings(answered("screen", intake)) + strings(answered("compare", intake))
    disclosed = any(
        PER_KW.search(t) and RECONCILES.search(t) and re.search(r"flow", t, re.IGNORECASE)
        for t in texts
    )
    assert disclosed, (
        f"the platform's flow ({flow_v:g} l/min for {kw_v:g} kW) implies "
        + ", ".join(f"{v[0]:.3f} [{v[1]:.3f}-{v[2]:.3f}] l/min/kW {k}" for k, v in implied.items())
        + f"; the library constant is {lib_v:g} [{lib_lo:g}-{lib_hi:g}] l/min/kW. The bands do not overlap under either reading, "
        "and nothing in /v1/screen or /v1/compare says the two differ"
    )


@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        (
            "9,000 l/min available against 130 l/min per rack = 0.96 l/min/kW at a 14 K delta-T",
            True,
        ),
        (
            "9,000 l/min (E3) available against 130 [120-140] l/min (E0) per rack (the platform library's secondary-loop flow)",
            False,
        ),
    ],
)
def test_the_per_kw_detector_is_not_vacuous(basis, expected):
    assert states_per_kw_flow(basis) is expected
