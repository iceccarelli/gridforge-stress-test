"""GF-001 / GF-002: interconnection-queue rows and cohort outcomes.

Callers pass an explicit `ColumnMap`; nothing is assumed about a file's columns. `LBNL_2026_MAP` is built ONLY from
what the 2026 workbook was observed to contain (see tests/contracts/test_lbnl_observed_layout.py for the pinned
evidence). Original values are preserved in `raw`.

Real-data lessons encoded here (observed in the workbook, probe run 40615136879):
* the unique key is (entity, q_id), not q_id alone, and `q_id` can be the literal "not assigned";
* dates are numeric serials in the data sheet;
* withdrawn/operational requests may lack a withdrawal/in-service date (the publisher notes withdrawal dates exist
  for only part of the withdrawn requests), so a missing date is a warning, never grounds to drop the row;
* the publisher's status vocabulary has FOUR values (active, withdrawn, suspended, operational).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any

from .findings import Finding

QUEUE_STAT_LABEL = (
    "interconnection-queue statistic for generation/storage requests; not a data-centre "
    "load-connection probability and not a guaranteed time-to-power"
)
EXCEL_EPOCH_1900 = date(
    1899, 12, 30
)  # serial 1 == 1900-01-01 under the (leap-bug-compatible) 1900 system
MAX_SERIAL = 80_000  # ~ year 2118: anything larger is not a plausible date serial


@dataclass(frozen=True)
class ColumnMap:
    project_id: str
    status: str
    queue_date: str
    capacity_mw: str
    technology: str
    withdrawn_date: str | None = None
    in_service_date: str | None = None
    entity: str | None = None
    extra_capacity: tuple[str, ...] = ()
    date_format: str = "iso"  # "iso" | "excel_serial_1900"
    unassigned_id_literal: str | None = None
    # source status -> active|suspended|withdrawn|operational
    status_map: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class QueueRecord:
    project_id: str
    entity: str | None
    status: str
    queue_date: date
    capacity_mw: float
    technology: str
    withdrawn_date: date | None
    in_service_date: date | None
    raw: Mapping[str, Any]


def _date(value: Any, fmt: str = "iso") -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("bool is not a date")
    if isinstance(value, date):
        return value
    if fmt == "excel_serial_1900":
        serial = float(str(value).strip())
        if not math.isfinite(serial) or serial < 1 or serial > MAX_SERIAL:
            raise ValueError(f"implausible date serial {value!r}")
        return EXCEL_EPOCH_1900 + timedelta(days=int(serial))
    return date.fromisoformat(str(value).strip())


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def parse_rows(
    rows: Iterable[Mapping[str, Any]], cmap: ColumnMap
) -> tuple[list[QueueRecord], list[Finding]]:
    out: list[QueueRecord] = []
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(rows):
        where = f"row {i}"
        pid = str(row.get(cmap.project_id) or "").strip()
        if not pid:
            findings.append(Finding("missing_id", "project id empty", where=where))
            continue
        entity = str(row.get(cmap.entity) or "").strip() if cmap.entity else None
        if cmap.unassigned_id_literal is not None and pid == cmap.unassigned_id_literal:
            findings.append(
                Finding(
                    "unassigned_id",
                    "request has no assigned queue id; cannot be de-duplicated or tracked by id",
                    "warning",
                    where,
                )
            )
        else:
            key = (entity or "", pid)
            if key in seen:
                findings.append(
                    Finding("duplicate_id", f"duplicate (entity, id) {key!r}", where=where)
                )
                continue
            seen.add(key)
        raw_status = str(row.get(cmap.status) or "").strip()
        status = cmap.status_map.get(raw_status)
        if status is None:
            findings.append(
                Finding("unknown_status", f"unmapped status {raw_status!r}", where=where)
            )
            continue
        try:
            qd = _date(row.get(cmap.queue_date), cmap.date_format)
            wd = (
                _date(row.get(cmap.withdrawn_date), cmap.date_format)
                if cmap.withdrawn_date
                else None
            )
            isd = (
                _date(row.get(cmap.in_service_date), cmap.date_format)
                if cmap.in_service_date
                else None
            )
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
        if any(not _blank(row.get(c)) for c in cmap.extra_capacity):
            findings.append(
                Finding(
                    "split_capacity",
                    f"{', '.join(cmap.extra_capacity)} also populated: capacity may be split across "
                    f"columns; their semantics are unverified, so only {cmap.capacity_mw} is used",
                    "warning",
                    where,
                )
            )
        errors, warns = _lifecycle(status, qd, wd, isd)
        if errors:
            findings.extend(Finding("lifecycle_contradiction", p, where=where) for p in errors)
            continue
        findings.extend(Finding(code, msg, "warning", where) for code, msg in warns)
        out.append(
            QueueRecord(
                pid,
                entity,
                status,
                qd,
                mw,
                str(row.get(cmap.technology) or "").strip(),
                wd,
                isd,
                dict(row),
            )
        )
    return out, findings


def _lifecycle(
    status: str, qd: date, wd: date | None, isd: date | None
) -> tuple[list[str], list[tuple[str, str]]]:
    """(errors, warnings). Errors are logical contradictions; a MISSING date is only a warning because the
    publisher's data is known to lack withdrawal dates for part of the withdrawn requests."""
    err: list[str] = []
    warn: list[tuple[str, str]] = []
    if wd and wd < qd:
        err.append("withdrawn before it entered the queue")
    if isd and isd < qd:
        err.append("in service before it entered the queue")
    if status == "withdrawn" and isd:
        err.append("withdrawn status must not carry an in-service date")
    if status == "operational" and wd:
        err.append("operational status must not carry a withdrawal date")
    if status in ("active", "suspended") and isd:
        err.append(f"{status} status must not carry an in-service date")
    if status == "active" and wd:
        err.append("active status must not carry a withdrawal date")
    if status == "withdrawn" and wd is None:
        warn.append(("missing_withdrawal_date", "withdrawn but no withdrawal date recorded"))
    if status == "operational" and isd is None:
        warn.append(("missing_in_service_date", "operational but no in-service date recorded"))
    return err, warn


def cohort_outcomes(
    records: Iterable[QueueRecord], *, entry_year: int, as_of: date, min_maturity_years: int = 5
) -> dict[str, Any]:
    """Outcomes of one explicit entry-year cohort, with censoring and denominators spelled out."""
    cohort = [r for r in records if r.queue_date.year == entry_year]
    n = len(cohort)
    op = [r for r in cohort if r.status == "operational"]
    wd = [r for r in cohort if r.status == "withdrawn"]
    active = [r for r in cohort if r.status == "active"]
    suspended = [r for r in cohort if r.status == "suspended"]
    resolved = len(op) + len(wd)
    age = as_of.year - entry_year
    warnings = []
    if age < min_maturity_years:
        warnings.append(
            f"immature cohort: {age}y old < {min_maturity_years}y; outcome shares are provisional"
        )
    if active:
        warnings.append(f"{len(active)} still-active projects are right-censored, not failures")
    if suspended:
        warnings.append(f"{len(suspended)} suspended projects are unresolved, not failures")
    no_date = [r for r in op if r.in_service_date is None]
    if no_date:
        warnings.append(
            f"{len(no_date)} operational projects have no in-service date and are excluded from durations"
        )
    durations = [(r.in_service_date - r.queue_date).days / 365.25 for r in op if r.in_service_date]
    return {
        "label": QUEUE_STAT_LABEL,
        "cohort_definition": f"requests with queue date in {entry_year}, as of {as_of.isoformat()}",
        "entered": n,
        "operational": len(op),
        "withdrawn": len(wd),
        "active_censored": len(active),
        "suspended_unresolved": len(suspended),
        "share_operational_of_entered": (len(op) / n) if n else None,
        "share_operational_of_resolved": (len(op) / resolved) if resolved else None,
        "median_years_queue_to_service_among_operational": median(durations) if durations else None,
        "duration_caveat": "durations exist only for projects that reached service (survivorship); "
        "censored projects are excluded",
        "warnings": warnings,
        "mature": age >= min_maturity_years,
    }


#: Built ONLY from the observed 2026 workbook: sheet '03. Complete Queue Data' header row and the codebook text
#: "q_status ... One of: active, withdrawn, suspended, or operational". `mw_1` pairs with `type_1` in every observed
#: row; the meaning of mw_2/mw_3 is not yet read, hence `extra_capacity` raises a warning instead of summing.
LBNL_2026_MAP = ColumnMap(
    project_id="q_id",
    entity="entity",
    status="q_status",
    queue_date="q_date",
    withdrawn_date="wd_date",
    in_service_date="on_date",
    capacity_mw="mw_1",
    extra_capacity=("mw_2", "mw_3"),
    technology="type_clean",
    date_format="excel_serial_1900",
    unassigned_id_literal="not assigned",
    status_map={
        "active": "active",
        "withdrawn": "withdrawn",
        "suspended": "suspended",
        "operational": "operational",
    },
)


#: What to profile in the real workbook (observed columns only): the vocabulary/quality questions the parser's rules
#: depend on. Used by `probe lbnl-queued-up --profile`.
LBNL_2026_PROFILE: dict[str, Any] = {
    "sheet_name": "03. Complete Queue Data",
    "header_marker": "q_id",
    "columns": (
        "q_status",
        "IA_phase_clean",
        "service",
        "project_type",
        "type_clean",
        "region",
        "q_date",
        "wd_date",
        "on_date",
        "q_year",
        "mw_1",
        "mw_2",
        "mw_3",
        "entity",
    ),
    "blank_checks": (
        ("q_status", "withdrawn", "wd_date"),
        ("q_status", "operational", "on_date"),
        ("q_status", "active", "wd_date"),
        ("q_status", "active", "on_date"),
        ("q_status", "suspended", "wd_date"),
    ),
    "order_checks": (("wd_date", "q_date"), ("on_date", "q_date"), ("on_date", "wd_date")),
    "dup_key": ("entity", "q_id"),
    "dup_exclude": {"q_id": "not assigned"},
}
