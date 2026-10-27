"""GF-001 / GF-002: interconnection-queue rows and cohort outcomes.

The upstream LBNL workbook layout is UNVERIFIED in this repository, so nothing here assumes real column
names: callers pass an explicit `ColumnMap`. Original values are preserved in `raw`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from statistics import median
from typing import Any

from .findings import Finding

QUEUE_STAT_LABEL = (
    "interconnection-queue statistic for generation/storage requests; not a data-centre "
    "load-connection probability and not a guaranteed time-to-power"
)


@dataclass(frozen=True)
class ColumnMap:
    project_id: str
    status: str
    queue_date: str
    capacity_mw: str
    technology: str
    withdrawn_date: str | None = None
    in_service_date: str | None = None
    status_map: Mapping[str, str] = field(
        default_factory=dict
    )  # source status -> active|withdrawn|operational


@dataclass(frozen=True)
class QueueRecord:
    project_id: str
    status: str
    queue_date: date
    capacity_mw: float
    technology: str
    withdrawn_date: date | None
    in_service_date: date | None
    raw: Mapping[str, Any]


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def parse_rows(
    rows: Iterable[Mapping[str, Any]], cmap: ColumnMap
) -> tuple[list[QueueRecord], list[Finding]]:
    out: list[QueueRecord] = []
    findings: list[Finding] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        where = f"row {i}"
        pid = str(row.get(cmap.project_id) or "").strip()
        if not pid:
            findings.append(Finding("missing_id", "project id empty", where=where))
            continue
        if pid in seen:
            findings.append(Finding("duplicate_id", f"duplicate project id {pid}", where=where))
            continue
        seen.add(pid)
        raw_status = str(row.get(cmap.status) or "").strip()
        status = cmap.status_map.get(raw_status)
        if status is None:
            findings.append(
                Finding("unknown_status", f"unmapped status {raw_status!r}", where=where)
            )
            continue
        try:
            qd = _date(row.get(cmap.queue_date))
            wd = _date(row.get(cmap.withdrawn_date)) if cmap.withdrawn_date else None
            isd = _date(row.get(cmap.in_service_date)) if cmap.in_service_date else None
        except ValueError:
            findings.append(Finding("bad_date", "unparseable date", where=where))
            continue
        if qd is None:
            findings.append(Finding("missing_queue_date", "queue date missing", where=where))
            continue
        try:
            mw = float(row.get(cmap.capacity_mw))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            findings.append(Finding("bad_capacity", "capacity not numeric", where=where))
            continue
        if not math.isfinite(mw) or mw <= 0:
            findings.append(
                Finding("bad_capacity", f"capacity {mw} not finite and positive", where=where)
            )
            continue
        problems = _lifecycle(status, qd, wd, isd)
        if problems:
            findings.extend(Finding("lifecycle_contradiction", p, where=where) for p in problems)
            continue
        out.append(
            QueueRecord(
                pid, status, qd, mw, str(row.get(cmap.technology) or "").strip(), wd, isd, dict(row)
            )
        )
    return out, findings


def _lifecycle(status: str, qd: date, wd: date | None, isd: date | None) -> list[str]:
    p: list[str] = []
    if wd and wd < qd:
        p.append("withdrawn before it entered the queue")
    if isd and isd < qd:
        p.append("in service before it entered the queue")
    if status == "withdrawn" and (wd is None or isd):
        p.append("withdrawn status needs a withdrawal date and no in-service date")
    if status == "operational" and (isd is None or wd):
        p.append("operational status needs an in-service date and no withdrawal date")
    if status == "active" and (wd or isd):
        p.append("active status must not carry a withdrawal or in-service date")
    return p


def cohort_outcomes(
    records: Iterable[QueueRecord], *, entry_year: int, as_of: date, min_maturity_years: int = 5
) -> dict[str, Any]:
    """Outcomes of one explicit entry-year cohort, with censoring and denominators spelled out."""
    cohort = [r for r in records if r.queue_date.year == entry_year]
    n = len(cohort)
    op = [r for r in cohort if r.status == "operational"]
    wd = [r for r in cohort if r.status == "withdrawn"]
    active = [r for r in cohort if r.status == "active"]
    resolved = len(op) + len(wd)
    age = as_of.year - entry_year
    warnings = []
    if age < min_maturity_years:
        warnings.append(
            f"immature cohort: {age}y old < {min_maturity_years}y; outcome shares are provisional"
        )
    if active:
        warnings.append(f"{len(active)} still-active projects are right-censored, not failures")
    durations = [(r.in_service_date - r.queue_date).days / 365.25 for r in op if r.in_service_date]
    return {
        "label": QUEUE_STAT_LABEL,
        "cohort_definition": f"requests with queue date in {entry_year}, as of {as_of.isoformat()}",
        "entered": n,
        "operational": len(op),
        "withdrawn": len(wd),
        "active_censored": len(active),
        "share_operational_of_entered": (len(op) / n) if n else None,
        "share_operational_of_resolved": (len(op) / resolved) if resolved else None,
        "median_years_queue_to_service_among_operational": median(durations) if durations else None,
        "duration_caveat": "durations exist only for projects that reached service (survivorship); "
        "censored projects are excluded",
        "warnings": warnings,
        "mature": age >= min_maturity_years,
    }
