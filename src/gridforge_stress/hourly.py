"""GF-004: hourly grid series integrity. Region-level observations; never site headroom."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from .findings import Finding

#: EIA-930 (publisher page read 2026-10-27): hourly data use the hour-ENDING convention, in each BA's chosen local time.
EIA930_CONVENTION = "hour-ending"


def interval_start(stamp: datetime, convention: str) -> datetime:
    """Start of the hour a timestamp labels. Hour-ending stamps label the hour that ENDS at the stamp."""
    if convention == "hour-ending":
        return stamp - timedelta(hours=1)
    if convention == "hour-beginning":
        return stamp
    raise ValueError(f"unknown interval convention {convention!r}")


SCOPE_LABEL = "balancing-authority-level reading; not evidence of site-specific headroom"


def parse_utc(ts: str) -> datetime | None:
    """Accept only explicit UTC ('Z' or +00:00). Naive/local stamps are ambiguous across DST."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        return None
    return dt.astimezone(UTC)


def validate_hourly(
    rows: Iterable[Mapping[str, Any]], *, as_of: datetime, max_staleness_hours: int = 48
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """rows: {period_utc, value, published_at?}. Returns deduplicated series (latest publication wins)."""
    findings: list[Finding] = []
    best: dict[datetime, tuple[datetime, float]] = {}
    for i, r in enumerate(rows):
        dt = parse_utc(str(r.get("period_utc", "")))
        if dt is None:
            findings.append(
                Finding("bad_timestamp", "need explicit UTC timestamp", where=f"row {i}")
            )
            continue
        if dt != dt.replace(minute=0, second=0, microsecond=0):
            findings.append(
                Finding("not_hour_aligned", "timestamp not on the hour", where=f"row {i}")
            )
            continue
        try:
            val = float(r["value"])
        except (KeyError, TypeError, ValueError):
            findings.append(Finding("bad_value", "value missing or non-numeric", where=f"row {i}"))
            continue
        if val != val or val in (float("inf"), float("-inf")):
            findings.append(Finding("bad_value", "value not finite", where=f"row {i}"))
            continue
        pub = parse_utc(str(r.get("published_at", ""))) or datetime.min.replace(tzinfo=UTC)
        if dt in best:
            old_pub, old_val = best[dt]
            if old_val != val:
                findings.append(
                    Finding(
                        "revision",
                        f"{dt.isoformat()} revised {old_val}->{val}",
                        "warning",
                        f"row {i}",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "duplicate", f"duplicate interval {dt.isoformat()}", "warning", f"row {i}"
                    )
                )
            if pub < old_pub:
                continue
        best[dt] = (pub, val)
    series = [{"period_utc": k.isoformat(), "value": v[1]} for k, v in sorted(best.items())]
    keys = sorted(best)
    for a, b in pairwise(keys):
        if b - a != timedelta(hours=1):
            n = int((b - a) / timedelta(hours=1)) - 1
            findings.append(Finding("gap", f"{n} missing hour(s) after {a.isoformat()}"))
    if keys and as_of - keys[-1] > timedelta(hours=max_staleness_hours):
        findings.append(
            Finding(
                "stale",
                f"latest interval {keys[-1].isoformat()} older than "
                f"{max_staleness_hours}h at {as_of.isoformat()}",
            )
        )
    if not keys:
        findings.append(Finding("empty", "no valid intervals"))
    return series, findings


def forecast_error(forecast: list[dict[str, Any]], actual: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare only intervals present in both; reports coverage so missing data is visible."""
    f = {r["period_utc"]: r["value"] for r in forecast}
    a = {r["period_utc"]: r["value"] for r in actual}
    common = sorted(set(f) & set(a))
    errs = [f[k] - a[k] for k in common]
    return {
        "intervals_compared": len(common),
        "forecast_only": len(set(f) - set(a)),
        "actual_only": len(set(a) - set(f)),
        "mean_error": sum(errs) / len(errs) if errs else None,
        "mean_abs_error": sum(abs(e) for e in errs) / len(errs) if errs else None,
        "scope": SCOPE_LABEL,
    }
