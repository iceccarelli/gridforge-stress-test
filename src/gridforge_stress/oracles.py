"""Independent electrical-headroom oracle: what the intake alone says each supply constraint can carry.

WHAT IT EVALUATES. Three constraints a Density Screen reports, recomputed from the intake document and nothing else:
  * transformer_capacity: effective units x unit MVA x power factor x derating, minus the current site peak
  * ups_capacity:         effective units x unit kW, minus the current IT load
  * grid_firm_capacity:   min(firm MVA x power factor, contracted MW), minus the current site peak
and the rack counts those headrooms imply under the rack power and PUE the product declares.

WHAT IT DELIBERATELY DOES NOT EVALUATE. Rack power and PUE (read from the product's own text and only sanity-banded: they are SHARED inputs, not
independent ones), cooling, busway, plant, floor loading, BTM relief, cost, dates, reliability or availability. Nameplate arithmetic is not an
availability claim. Intakes with more than one transformer or UPS entry are reported as UNSUPPORTED rather than guessed at.

DERIVATION. Plain power-engineering arithmetic: S[MVA] x pf = P[MW]; N+k leaves (units - k) carrying load; 2N leaves floor(units / 2) (a
conservative reading when units is odd, stated here because the product's text does not define it).

INDEPENDENCE. Nothing here imports or copies product code. Agreement is evidence that the product's arithmetic matches this derivation on the
tested inputs; it is not proof the product is right for inputs not tested.
"""

from __future__ import annotations

import math
import re
from typing import Any

REDUNDANCY = ("N", "N+1", "N+2", "2N")
#: Product text rounds headroom to 10 kW.
HEADROOM_TOLERANCE_KW = 10.0
#: A rack outside this band is not a believable AI rack; if the product declares one the arithmetic check is moot.
RACK_KW_BAND = (30.0, 400.0)
PUE_BAND = (1.0, 2.5)
CHECKED = ("transformer_capacity", "ups_capacity", "grid_firm_capacity")


class Unsupported(ValueError):
    """The intake has a shape this oracle does not model; the check is UNCHECKED, not passed."""


def _num(x: Any) -> float:
    """An intake number, bare or wrapped as {'value': n, 'note': ...}."""
    if isinstance(x, dict):
        x = x.get("value")
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise Unsupported(f"not a finite number: {x!r}")
    return float(x)


def effective_units(units: int, redundancy: str) -> int:
    if redundancy not in REDUNDANCY:
        raise Unsupported(f"unknown redundancy {redundancy!r}")
    if redundancy == "2N":
        return max(0, units // 2)
    return max(0, units - {"N": 0, "N+1": 1, "N+2": 2}[redundancy])


def _single(intake: dict[str, Any], key: str) -> dict[str, Any]:
    items = intake.get(key)
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise Unsupported(f"{key}: exactly one entry is modelled")
    return items[0]


def transformer_headroom_kw(intake: dict[str, Any]) -> float:
    t, g = _single(intake, "transformers"), intake["grid"]
    mw = (
        effective_units(int(t["units"]), t.get("redundancy", "N+1"))
        * _num(t["unit_rating_MVA"])
        * _num(g["power_factor"])
        * _num(t.get("derating_factor", 1.0))
    )
    return (mw - _num(g["current_site_peak_MW"])) * 1000.0


def ups_headroom_kw(intake: dict[str, Any]) -> float:
    u, g = _single(intake, "ups"), intake["grid"]
    kw = effective_units(int(u["units"]), u.get("redundancy", "N+1")) * _num(u["unit_rating_kW"])
    return kw - _num(g["current_it_load_MW"]) * 1000.0


def grid_headroom_kw(intake: dict[str, Any]) -> float:
    g = intake["grid"]
    ceiling = min(_num(g["firm_capacity_MVA"]) * _num(g["power_factor"]), _num(g["contracted_MW"]))
    return (ceiling - _num(g["current_site_peak_MW"])) * 1000.0


def expected_headroom_kw(intake: dict[str, Any]) -> dict[str, float]:
    return {
        "transformer_capacity": transformer_headroom_kw(intake),
        "ups_capacity": ups_headroom_kw(intake),
        "grid_firm_capacity": grid_headroom_kw(intake),
    }


def racks_for(headroom_kw: float, per_rack_kw: float) -> int:
    return 0 if headroom_kw <= 0 else math.floor(headroom_kw / per_rack_kw)


MINUS_SIGN = chr(0x2212)  # the product prints negative headroom with either this or a hyphen
_FIRST_NUMBER = re.compile(rf"^\s*([-{MINUS_SIGN}]?[\d,]+(?:\.\d+)?)")


def _leading_kw(basis: str) -> float | None:
    m = _FIRST_NUMBER.match(basis)
    return float(m.group(1).replace(",", "").replace(MINUS_SIGN, "-")) if m else None


def _declared(scenario: dict[str, Any]) -> tuple[float | None, float | None]:
    """(rack kW, PUE) the product declares in this scenario's own constraint text. Shared inputs."""
    rack = pue = None
    for c in scenario.get("constraints", []):
        if m := re.search(r"draws ([\d.]+)", str(c.get("basis", ""))):
            rack = float(m.group(1))
        if c.get("id") == "grid_firm_capacity" and (
            m := re.search(r"PUE ([\d.]+)", str(c.get("basis")))
        ):
            pue = float(m.group(1))
    return rack, pue


def check_screen(intake: dict[str, Any], screen: dict[str, Any]) -> list[str]:
    """Discrepancies between a /v1/screen result and the independent headrooms. Empty means consistent on the tested constraints.

    Raises Unsupported if the intake shape is outside the model: the caller must report UNCHECKED, never PASS.
    """
    expected = expected_headroom_kw(intake)
    problems: list[str] = []
    for s in screen.get("scenarios", []):
        sid = s.get("id", "?")
        rack_kw, pue = _declared(s)
        if rack_kw is None or pue is None:
            problems.append(f"{sid}: cannot read the rack kW / PUE the product declares")
            continue
        if (
            not RACK_KW_BAND[0] <= rack_kw <= RACK_KW_BAND[1]
            or not PUE_BAND[0] <= pue <= PUE_BAND[1]
        ):
            problems.append(
                f"{sid}: declared rack {rack_kw} kW / PUE {pue} outside any believable range"
            )
        by_id = {c.get("id"): c for c in s.get("constraints", [])}
        for cid in CHECKED:
            c = by_id.get(cid)
            if c is None:
                problems.append(f"{sid}.{cid}: constraint missing from the result")
                continue
            shown = _leading_kw(str(c.get("basis", "")))
            if shown is None:
                problems.append(f"{sid}.{cid}: no headroom in text {c.get('basis')!r}")
            elif abs(shown - expected[cid]) > HEADROOM_TOLERANCE_KW:
                problems.append(
                    f"{sid}.{cid}: product headroom {shown:g} kW, independent {expected[cid]:g} kW"
                )
            per_rack = (
                rack_kw if cid == "ups_capacity" else rack_kw * pue
            )  # UPS carries IT load; the others carry facility load
            want, got = racks_for(expected[cid], per_rack), c.get("racks_permitted")
            if got != want:
                problems.append(f"{sid}.{cid}: product permits {got} racks, independent {want}")
    return problems


def bess_bound_violations(
    *,
    grid_firm_mw: float,
    power_mw: float,
    energy_mwh: float,
    ride_through_h: float,
    available_mw: float,
) -> list[str]:
    """Physical bounds on 'grid + BESS' firm availability over a stated ride-through. Empty means no bound is violated.

    EVALUATES: only what no battery can beat. (1) it never reduces the grid; (2) it never adds more than its power rating; (3) it never
    delivers more energy than it holds: (available - grid) x hours <= nameplate MWh (usable energy is smaller, so this is the loosest true bound).
    DOES NOT EVALUATE: the exact usable-energy window, efficiency, degradation or auxiliary load (library assumptions of the product, not
    physics), nor whether the answer is exactly right: a product that is too conservative passes. Independence: derived from power and
    energy conservation; no product constant is used.
    """
    eps = 1e-6
    added = available_mw - grid_firm_mw
    out: list[str] = []
    if added < -eps:
        out.append(
            f"the battery reduced availability: {available_mw:g} MW < grid {grid_firm_mw:g} MW"
        )
    if added > power_mw + eps:
        out.append(f"added {added:g} MW exceeds the {power_mw:g} MW power rating")
    if ride_through_h > 0 and added * ride_through_h > energy_mwh + eps:
        out.append(
            f"added {added:g} MW for {ride_through_h:g} h needs {added * ride_through_h:g} MWh, more than the {energy_mwh:g} MWh stored"
        )
    return out
