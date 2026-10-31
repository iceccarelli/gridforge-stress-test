"""EIA Electric Power Monthly Table 5.6.A: average retail electricity price by state and sector. Context, never a tariff.

The page publishes one month of average prices (cents per kilowatt-hour) to ultimate customers, by US state and end-use sector, from the
EIA-861M survey. It is a statistic over everyone in a sector in a state, not the price any particular load pays: a hall's price is its
contracted tariff. The parser reads the table by its column headings, refuses a page whose layout is not the one it knows, and keeps
unpublished cells ('--', 'NM', blank) as absent rather than as zero.
"""

from __future__ import annotations

import html
import re
from typing import Any

SCHEMA = "gridforge-stress/price-context/1"
SOURCE_ID = "eia-epm"
SECTORS = ("residential", "commercial", "industrial", "transportation", "all_sectors")
#: Sectors a data-centre load can be billed under; the others are carried by the source table but not reported as context.
REPORTED = ("commercial", "industrial")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_RELEASE = re.compile(r"Release Date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
_PERIOD = re.compile(r"^([A-Z][a-z]+) (\d{4})$")
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
NOT_A_NUMBER = frozenset({"", "--", "NM", "W", "NA", "*", "(S)"})


class PriceTableError(ValueError):
    """The page is not the table this parser understands; nothing is guessed."""


def _cells(row: str) -> list[str]:
    return [" ".join(html.unescape(_TAG.sub("", c)).split()) for c in _CELL.findall(row)]


def _number(text: str) -> float | None:
    if text in NOT_A_NUMBER:
        return None
    try:
        v = float(text.replace(",", ""))
    except ValueError as exc:
        raise PriceTableError(f"unreadable price cell {text!r}") from exc
    if not 0 < v < 1000:
        raise PriceTableError(f"price {v} cents/kWh is outside any plausible range")
    return v


def parse_table_5_6_a(body: str) -> dict[str, Any]:
    """Parse the page into {period, prior_period, release_date, rows: {name: {sector: {current, prior}}}}."""
    if "Table 5.6.A" not in body:
        raise PriceTableError("not Table 5.6.A (Average Price of Electricity by State)")
    rows = [_cells(r) for r in _ROW.findall(body)]
    group = next((r for r in rows if len(r) == 6 and r[1] == "Residential"), None)
    if group is None or tuple(s.lower().replace(" ", "_") for s in group[1:]) != SECTORS:
        raise PriceTableError("sector header row not recognised")
    head = next((r for r in rows if r and r[0].startswith("Census Division")), None)
    if head is None or len(head) != 1 + 2 * len(SECTORS):
        raise PriceTableError("period header row not recognised")
    parsed = []
    for p in head[1:]:
        m = _PERIOD.match(p)
        if not m or m.group(1) not in MONTHS:
            raise PriceTableError(f"unreadable period heading {p!r}")
        parsed.append((int(m.group(2)), MONTHS.index(m.group(1)) + 1))
    cur, prior = parsed[0], parsed[1]
    if any(parsed[i] != cur for i in range(0, len(parsed), 2)) or any(
        parsed[i] != prior for i in range(1, len(parsed), 2)
    ):
        raise PriceTableError("columns do not alternate current/prior year")
    if not (prior[1] == cur[1] and prior[0] == cur[0] - 1):
        raise PriceTableError("prior period is not the same month a year earlier")
    out: dict[str, Any] = {}
    for r in rows:
        if len(r) != len(head) or r is head or not r[0]:
            continue
        out[r[0]] = {
            s: {"current": _number(r[1 + 2 * i]), "prior": _number(r[2 + 2 * i])}
            for i, s in enumerate(SECTORS)
        }
    if "U.S. Total" not in out or len(out) < 50:
        raise PriceTableError(f"expected U.S. Total and the states, found {len(out)} rows")
    rel = _RELEASE.search(_TAG.sub(" ", body))
    return {
        "period": f"{cur[0]}-{cur[1]:02d}",
        "prior_period": f"{prior[0]}-{prior[1]:02d}",
        "release_date": rel.group(1) if rel else None,
        "rows": out,
    }


def build_context(parsed: dict[str, Any], *, provenance: dict[str, Any]) -> dict[str, Any]:
    """The fixture document: US total, divisions and states; commercial and industrial only, cents per kWh as published."""
    rows = {
        name: {s: sec[s] for s in REPORTED}
        for name, sec in parsed["rows"].items()
        if any(sec[s]["current"] is not None for s in REPORTED)
    }
    return {
        "schema": SCHEMA,
        "source_id": SOURCE_ID,
        "evidence_kind": "retail_price_statistic",
        "geography": "US states",
        "unit": "US cents per kWh",
        "period": parsed["period"],
        "prior_period": parsed["prior_period"],
        "release_date": parsed["release_date"],
        "definition": (
            "Average price to ultimate customers in the sector for one month (EIA-861M): revenue divided by sales, so it includes delivery "
            "and every customer in the sector. NOT the tariff of any load, NOT a data-centre price, NOT a forecast."
        ),
        "provenance": provenance,
        "rows": rows,
    }


class PriceContextError(ValueError):
    """The price-context fixture is not usable; the message says why."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise PriceContextError(f"duplicate key {k!r}: the last value would silently win")
        out[k] = v
    return out


def _months_before(period: str, prior: str) -> int:
    (y1, m1), (y0, m0) = (tuple(int(x) for x in p.split("-")) for p in (period, prior))
    return (y1 * 12 + m1) - (y0 * 12 + m0)


def load_price_context(path: Any) -> dict[str, Any]:
    """Read and validate the fixture. Refuses, with a reason: wrong schema/source/unit, an unmistakable period whose prior period is exactly
    twelve months earlier, missing sectors or values, booleans/NaN/implausible prices, a US total outside the range of the states it
    summarises, duplicate keys, and provenance without a content hash."""
    import json
    import math
    from pathlib import Path

    try:
        doc = json.loads(
            Path(path).read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys
        )
    except json.JSONDecodeError as exc:
        raise PriceContextError(f"not valid JSON: {exc.msg}") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise PriceContextError("unrecognised price-context schema")
    if doc.get("source_id") != "eia-epm":
        raise PriceContextError(f"source_id {doc.get('source_id')!r} is not eia-epm")
    if doc.get("unit") != "US cents per kWh":
        raise PriceContextError(f"unit {doc.get('unit')!r}: prices are US cents per kWh")
    if doc.get("evidence_kind") != "retail_price_statistic":
        raise PriceContextError("evidence_kind must stay retail_price_statistic (not a tariff)")
    period, prior = str(doc.get("period", "")), str(doc.get("prior_period", ""))
    pattern = r"\d{4}-(0[1-9]|1[0-2])"
    if not re.fullmatch(pattern, period):
        raise PriceContextError("period must be YYYY-MM")
    if not re.fullmatch(pattern, prior) or _months_before(period, prior) != 12:
        raise PriceContextError("prior_period must be exactly twelve months before period")
    prov = doc.get("provenance")
    if (
        not isinstance(prov, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(prov.get("page_sha256", "")))
        or not isinstance(prov.get("page_bytes"), int)
        or isinstance(prov.get("page_bytes"), bool)
        or prov["page_bytes"] <= 0
    ):
        raise PriceContextError("provenance needs page_sha256 (64 hex) and positive page_bytes")
    rows = doc.get("rows")
    if not isinstance(rows, dict) or "U.S. Total" not in rows or len(rows) < 50:
        raise PriceContextError("needs the US total and the states")
    for name, sec in rows.items():
        for s in REPORTED:
            for k in ("current", "prior"):
                try:
                    v = sec[s][k]
                except (KeyError, TypeError):
                    raise PriceContextError(f"{name}: missing {s}.{k}") from None
                if v is None:
                    continue
                ok = isinstance(v, (int, float)) and not isinstance(v, bool)
                if not (ok and math.isfinite(v) and 0 < v < 1000):
                    raise PriceContextError(f"{name} {s} {k}: {v!r} is not a plausible price")
    for s in REPORTED:
        for k in ("current", "prior"):
            vals = [r[s][k] for n, r in rows.items() if n != "U.S. Total" and r[s][k] is not None]
            us = rows["U.S. Total"][s][k]
            if us is not None and vals and not (min(vals) <= us <= max(vals)):
                raise PriceContextError(
                    f"U.S. Total {s} {k} {us} lies outside the range of the rows it summarises"
                )
    return doc
