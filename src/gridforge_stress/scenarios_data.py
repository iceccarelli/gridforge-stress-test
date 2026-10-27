"""GF-006 / GF-007 / GF-008: modeled scenarios, tariffs, weather context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from .findings import Finding

MODELED_LABEL = "modeled scenario output; not an observed fact or a guaranteed future price"
TARIFF_LABEL = "generic published tariff; does not prove a site's contracted rate or eligibility"
WEATHER_LABEL = "weather context; not a measured site cooling load"


def validate_cambium_record(rec: Mapping[str, Any]) -> list[Finding]:
    f: list[Finding] = []
    for k in ("scenario_id", "model_release", "region", "resolution", "units", "kind"):
        if not rec.get(k):
            f.append(Finding("missing_field", f"{k} missing"))
    if rec.get("kind") != "modeled":
        f.append(Finding("mislabeled", "scenario data must carry kind='modeled'"))
    units = rec.get("units") or {}
    if units.get("energy_cost") not in ("$/MWh", "USD/MWh"):
        f.append(Finding("units", "energy_cost unit must be $/MWh"))
    if units.get("emissions") not in ("kg/MWh", "kgCO2e/MWh"):
        f.append(Finding("units", "emissions unit must be kg/MWh"))
    return f


def rank_stability(costs: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    """costs[scenario][site] -> $/MWh. Reports whether the cheapest site is the same in every scenario."""
    winners = {sc: min(v, key=lambda s: v[s]) for sc, v in costs.items()}
    return {
        "winner_by_scenario": winners,
        "stable": len(set(winners.values())) == 1,
        "label": MODELED_LABEL,
    }


def validate_tariff(t: Mapping[str, Any]) -> list[Finding]:
    f: list[Finding] = []
    try:
        start = date.fromisoformat(str(t.get("effective_start")))
    except ValueError:
        f.append(Finding("bad_date", "effective_start invalid"))
        start = None
    end_raw = t.get("effective_end")
    if end_raw:
        try:
            end = date.fromisoformat(str(end_raw))
            if start and end < start:
                f.append(Finding("date_order", "effective_end before effective_start"))
        except ValueError:
            f.append(Finding("bad_date", "effective_end invalid"))
    if not t.get("customer_class"):
        f.append(Finding("missing_field", "customer_class missing"))
    rates = t.get("energy_rates_usd_per_kwh")
    if (
        not isinstance(rates, Sequence)
        or not rates
        or any(not isinstance(x, int | float) or x < 0 for x in rates)
    ):
        f.append(Finding("energy_rates", "energy rates must be non-negative numbers"))
        rates = []
    d = t.get("demand_charge_usd_per_kw")
    if d is not None and (not isinstance(d, int | float) or d < 0):
        f.append(Finding("demand_charge", "demand charge must be non-negative"))
    for name in ("tou_weekday", "tou_weekend"):
        m = t.get(name)
        if m is None:
            continue
        if not (
            isinstance(m, list)
            and len(m) == 12
            and all(isinstance(r, list) and len(r) == 24 for r in m)
        ):
            f.append(Finding("tou_shape", f"{name} must be 12 months x 24 hours"))
        elif any(not isinstance(p, int) or p < 0 or p >= max(len(rates), 1) for r in m for p in r):
            f.append(Finding("tou_period", f"{name} references an undefined rate period"))
    return f


def validate_weather(
    rec: Mapping[str, Any], *, as_of: datetime, max_age: timedelta
) -> list[Finding]:
    f: list[Finding] = []
    kind = rec.get("kind")
    if kind not in ("observation", "forecast"):
        f.append(Finding("kind", "kind must be observation or forecast"))
    try:
        ts = datetime.fromisoformat(str(rec["timestamp"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            f.append(Finding("tz", "timestamp must be timezone-aware"))
        elif kind == "observation" and ts > as_of:
            f.append(Finding("future_observation", "observation lies in the future"))
        elif kind == "observation" and as_of - ts > max_age:
            f.append(Finding("stale", "observation older than allowed age"))
    except (KeyError, ValueError):
        f.append(Finding("bad_timestamp", "timestamp missing or invalid"))
    if rec.get("temperature_c") is None:
        f.append(Finding("missing_value", "temperature missing", "warning"))
    return f
