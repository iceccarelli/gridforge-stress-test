"""GF-001 / GF-002: interconnection-queue rows and cohort outcomes.

Callers pass an explicit `ColumnMap`; nothing is assumed about a file's columns. `LBNL_2026_MAP` is built ONLY from
what the 2026 workbook was observed to contain (tests/contracts/test_lbnl_observed_layout.py pins the evidence).
Original values are preserved in `raw`.

Principle, learned from measuring the real data (38,201 rows, probe run 40615868160): FLAG, never silently drop or
repair a publisher's rows. The data contains status/date conflicts (e.g. 158 `active` rows carry an in-service date),
reversed dates, repeated (entity, id) keys, a status the codebook does not list, and missing terminal dates for about
a third of withdrawn/operational rows. Each is reported as a warning and recorded in `QueueRecord.flags`; exclusion
from a statistic is an explicit analysis-time decision with the excluded count disclosed. Only a row that cannot be
placed at all (no id, unmapped status, unparseable/missing queue date, non-positive capacity) is rejected.
"""

from __future__ import annotations

import math
from collections import Counter
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
EXCEL_EPOCH_1900 = date(1899, 12, 30)  # serial 1 == 1900-01-01 under the 1900 date system
MAX_SERIAL = 80_000  # ~ year 2118: anything larger is not a plausible date serial

# Flags carried on a kept record. Analysis code decides what to exclude and must report the count.
FLAG_DATE_ORDER = "date_order_conflict"
FLAG_STATUS_DATE = "status_date_conflict"
FLAG_MISSING_WD = "missing_withdrawal_date"
FLAG_MISSING_ON = "missing_in_service_date"
FLAG_DUP_KEY = "duplicate_key"
FLAG_SPLIT_CAP = "split_capacity"
FLAG_UNASSIGNED = "unassigned_id"


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
    # source status -> active|suspended|withdrawn|operational|unknown
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
    flags: tuple[str, ...] = ()


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


def _lifecycle(
    status: str, qd: date, wd: date | None, isd: date | None
) -> list[tuple[str, str, str]]:
    """(flag, code, message) for every conflict. All are warnings: the publisher's own data contains each of them."""
    out: list[tuple[str, str, str]] = []
    if wd and wd < qd:
        out.append(
            (FLAG_DATE_ORDER, "date_order_conflict", "withdrawn before it entered the queue")
        )
    if isd and isd < qd:
        out.append(
            (FLAG_DATE_ORDER, "date_order_conflict", "in service before it entered the queue")
        )
    if status == "withdrawn" and isd:
        out.append(
            (
                FLAG_STATUS_DATE,
                "status_date_conflict",
                "withdrawn status carries an in-service date",
            )
        )
    if status == "operational" and wd:
        out.append(
            (
                FLAG_STATUS_DATE,
                "status_date_conflict",
                "operational status carries a withdrawal date",
            )
        )
    if status in ("active", "suspended") and isd:
        out.append(
            (
                FLAG_STATUS_DATE,
                "status_date_conflict",
                f"{status} status carries an in-service date",
            )
        )
    if status == "active" and wd:
        out.append(
            (FLAG_STATUS_DATE, "status_date_conflict", "active status carries a withdrawal date")
        )
    if status == "withdrawn" and wd is None:
        out.append(
            (
                FLAG_MISSING_WD,
                "missing_withdrawal_date",
                "withdrawn but no withdrawal date recorded",
            )
        )
    if status == "operational" and isd is None:
        out.append(
            (
                FLAG_MISSING_ON,
                "missing_in_service_date",
                "operational but no in-service date recorded",
            )
        )
    return out


def parse_rows(
    rows: Iterable[Mapping[str, Any]], cmap: ColumnMap
) -> tuple[list[QueueRecord], list[Finding]]:
    rows = list(rows)
    out: list[QueueRecord] = []
    findings: list[Finding] = []

    def key_of(row: Mapping[str, Any]) -> tuple[str, str] | None:
        pid = str(row.get(cmap.project_id) or "").strip()
        if not pid or (
            cmap.unassigned_id_literal is not None and pid == cmap.unassigned_id_literal
        ):
            return None
        entity = str(row.get(cmap.entity) or "").strip() if cmap.entity else ""
        return (entity, pid)

    key_counts = Counter(k for k in (key_of(r) for r in rows) if k is not None)
    for i, row in enumerate(rows):
        where = f"row {i}"
        flags: list[str] = []
        pid = str(row.get(cmap.project_id) or "").strip()
        if not pid:
            findings.append(Finding("missing_id", "project id empty", where=where))
            continue
        entity = str(row.get(cmap.entity) or "").strip() if cmap.entity else None
        if cmap.unassigned_id_literal is not None and pid == cmap.unassigned_id_literal:
            flags.append(FLAG_UNASSIGNED)
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
            if key_counts[key] > 1:
                flags.append(FLAG_DUP_KEY)
                findings.append(
                    Finding(
                        "duplicate_id",
                        f"(entity, id) {key!r} appears {key_counts[key]} times; kept and flagged "
                        "(identical copies and distinct requests are not distinguished here)",
                        "warning",
                        where,
                    )
                )
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
            findings.append(
                Finding(
                    "missing_queue_date",
                    "queue date missing: cannot be placed in an entry-year cohort",
                    where=where,
                )
            )
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
            flags.append(FLAG_SPLIT_CAP)
            findings.append(
                Finding(
                    "split_capacity",
                    f"{', '.join(cmap.extra_capacity)} also populated: capacity may be split across "
                    f"columns; their semantics are unverified, so only {cmap.capacity_mw} is used",
                    "warning",
                    where,
                )
            )
        for flag, code, msg in _lifecycle(status, qd, wd, isd):
            flags.append(flag)
            findings.append(Finding(code, msg, "warning", where))
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
                tuple(dict.fromkeys(flags)),
            )
        )
    return out, findings


def cohort_outcomes(
    records: Iterable[QueueRecord], *, entry_year: int, as_of: date, min_maturity_years: int = 5
) -> dict[str, Any]:
    """Outcomes of one explicit entry-year cohort, with censoring, flagged rows and denominators spelled out."""
    cohort = [r for r in records if r.queue_date.year == entry_year]
    n = len(cohort)
    op = [r for r in cohort if r.status == "operational"]
    wd = [r for r in cohort if r.status == "withdrawn"]
    active = [r for r in cohort if r.status == "active"]
    suspended = [r for r in cohort if r.status == "suspended"]
    unclassified = [r for r in cohort if r.status == "unknown"]
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
    if unclassified:
        warnings.append(
            f"{len(unclassified)} projects have status 'unknown' and count in no outcome"
        )
    no_date = [r for r in op if r.in_service_date is None]
    if no_date:
        warnings.append(
            f"{len(no_date)} operational projects have no in-service date and are excluded from durations"
        )
    conflicted = [r for r in op if FLAG_DATE_ORDER in r.flags]
    if conflicted:
        warnings.append(
            f"{len(conflicted)} operational projects have dates in impossible order and are excluded from durations"
        )
    dups = [r for r in cohort if FLAG_DUP_KEY in r.flags]
    if dups:
        warnings.append(
            f"{len(dups)} cohort rows share an (entity, id) key with another row; they are all counted"
        )
    durations = [
        (r.in_service_date - r.queue_date).days / 365.25
        for r in op
        if r.in_service_date and FLAG_DATE_ORDER not in r.flags
    ]
    return {
        "label": QUEUE_STAT_LABEL,
        "cohort_definition": f"requests with queue date in {entry_year}, as of {as_of.isoformat()}",
        "entered": n,
        "operational": len(op),
        "withdrawn": len(wd),
        "active_censored": len(active),
        "suspended_unresolved": len(suspended),
        "status_unknown": len(unclassified),
        "share_operational_of_entered": (len(op) / n) if n else None,
        "share_operational_of_resolved": (len(op) / resolved) if resolved else None,
        "median_years_queue_to_service_among_operational": median(durations) if durations else None,
        "durations_used": len(durations),
        "duration_caveat": "durations exist only for projects that reached service (survivorship); "
        "censored projects are excluded",
        "warnings": warnings,
        "mature": age >= min_maturity_years,
    }


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile (the common 'type 7' definition), q in [0, 1]."""
    if not sorted_values:
        raise ValueError("no values")
    if not 0 <= q <= 1:
        raise ValueError("q must be within [0, 1]")
    h = (len(sorted_values) - 1) * q
    lo, hi = math.floor(h), math.ceil(h)
    return sorted_values[lo] + (h - lo) * (sorted_values[hi] - sorted_values[lo])


def duration_summary(
    records: Iterable[QueueRecord],
    *,
    cod_year_min: int | None = None,
    cod_year_max: int | None = None,
    group_by: str | None = None,
) -> dict[str, Any]:
    """Years from queue entry (IR) to in-service date (COD) among OPERATIONAL requests that have both dates.

    Excluded, with counts disclosed: rows with impossible date order; rows sharing a (entity, id) key beyond the
    first (one request listed per location must not be counted once per row). The result is conditioned on
    reaching COD (survivorship): it is NOT the time a request takes in general, and says nothing about load
    connections.
    """
    recs = list(records)
    op = [r for r in recs if r.status == "operational" and r.in_service_date is not None]
    conflicted = [r for r in op if FLAG_DATE_ORDER in r.flags]
    seen: set[tuple[str, str]] = set()
    kept: list[QueueRecord] = []
    dup_dropped = 0
    for r in op:
        if FLAG_DATE_ORDER in r.flags:
            continue
        if FLAG_DUP_KEY in r.flags:
            key = (r.entity or "", r.project_id)
            if key in seen:
                dup_dropped += 1
                continue
            seen.add(key)
        kept.append(r)
    if cod_year_min is not None:
        kept = [r for r in kept if r.in_service_date and r.in_service_date.year >= cod_year_min]
    if cod_year_max is not None:
        kept = [r for r in kept if r.in_service_date and r.in_service_date.year <= cod_year_max]

    def stats(rows: list[QueueRecord]) -> dict[str, Any]:
        yrs = sorted(
            (r.in_service_date - r.queue_date).days / 365.25 for r in rows if r.in_service_date
        )
        if not yrs:
            return {"n": 0}
        return {
            "n": len(yrs),
            "p25_years": round(percentile(yrs, 0.25), 3),
            "median_years": round(percentile(yrs, 0.5), 3),
            "p75_years": round(percentile(yrs, 0.75), 3),
            "min_years": round(yrs[0], 3),
            "max_years": round(yrs[-1], 3),
        }

    out: dict[str, Any] = {
        "label": QUEUE_STAT_LABEL,
        "definition": "years from queue entry to in-service date, operational requests with both dates; "
        "conditioned on reaching COD (survivorship); not a time-to-power for a load",
        "cod_year_range": [cod_year_min, cod_year_max],
        "operational_with_in_service_date": len(op),
        "excluded_impossible_date_order": len(conflicted),
        "excluded_repeated_key_rows": dup_dropped,
        "overall": stats(kept),
    }
    if group_by:
        groups: dict[str, list[QueueRecord]] = {}
        for r in kept:
            groups.setdefault(str(r.raw.get(group_by, "")) or "(blank)", []).append(r)
        out["by_" + group_by] = {k: stats(v) for k, v in sorted(groups.items())}
    return out


def capacity_summary(records: Iterable[QueueRecord]) -> dict[str, Any]:
    """Capacity as published in the capacity column, counted ONCE per (entity, id) request.

    Measured on the real LBNL data (probe run 40638926321): 134 of the 135 repeated keys carry the same capacity on every
    row (one request listed per location), and the non-first rows hold 26,458.6 MW. Summing rows naively would
    double-count that. The first row of a repeated key is kept; later rows are counted in `double_counting_avoided_mw`.
    Keys whose rows disagree on capacity are listed in `keys_with_differing_capacity` (their first row is used).
    The unit of the capacity column is UNVERIFIED in the source documentation read so far.
    """
    first: dict[tuple[str, str], QueueRecord] = {}
    differing: set[tuple[str, str]] = set()
    naive = dedup = avoided = 0.0
    avoided_rows = 0
    by_status_dedup: dict[str, float] = {}
    by_status_naive: dict[str, float] = {}
    for r in records:
        naive += r.capacity_mw
        by_status_naive[r.status] = by_status_naive.get(r.status, 0.0) + r.capacity_mw
        if FLAG_DUP_KEY in r.flags:
            key = (r.entity or "", r.project_id)
            if key in first:
                avoided += r.capacity_mw
                avoided_rows += 1
                if r.capacity_mw != first[key].capacity_mw:
                    differing.add(key)
                continue
            first[key] = r
        dedup += r.capacity_mw
        by_status_dedup[r.status] = by_status_dedup.get(r.status, 0.0) + r.capacity_mw
    return {
        "label": "capacity as published in the capacity column (unit UNVERIFIED, presumed MW), counted once per "
        "(entity, id) request; not available capacity, not a connection probability",
        "naive_row_sum": round(naive, 3),
        "deduplicated_sum": round(dedup, 3),
        "double_counting_avoided_mw": round(avoided, 3),
        "rows_not_counted": avoided_rows,
        "keys_with_differing_capacity": len(differing),
        "by_status_deduplicated": {k: round(v, 3) for k, v in sorted(by_status_dedup.items())},
        "by_status_naive": {k: round(v, 3) for k, v in sorted(by_status_naive.items())},
    }


#: Built ONLY from the observed 2026 workbook: sheet '03. Complete Queue Data' header row, the codebook text
#: "q_status ... One of: active, withdrawn, suspended, or operational", and the measured distinct q_status values
#: (those four PLUS 'unknown': 10 rows, not listed in the codebook). `mw_1` pairs with `type_1` in every observed row;
#: the meaning of mw_2/mw_3 is not yet read, hence `extra_capacity` raises a warning instead of summing.
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
        "unknown": "unknown",
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
        ("q_status", "operational", "wd_date"),
        ("q_status", "unknown", "wd_date"),
        ("q_status", "unknown", "on_date"),
        ("q_status", "withdrawn", "on_date"),
        ("q_status", "active", "wd_date"),
        ("q_status", "active", "on_date"),
        ("q_status", "suspended", "wd_date"),
        ("q_status", "suspended", "on_date"),
    ),
    "order_checks": (("wd_date", "q_date"), ("on_date", "q_date"), ("on_date", "wd_date")),
    "dup_key": ("entity", "q_id"),
    "dup_exclude": {"q_id": "not assigned"},
    "dup_value_col": "mw_1",
}
