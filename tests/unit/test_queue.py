from __future__ import annotations

import csv
from datetime import date

import pytest

from gridforge_stress.fixtures import QUEUE_MAP
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.queue import QUEUE_STAT_LABEL, cohort_outcomes, parse_rows

pytestmark = pytest.mark.unit


def base(**kw):
    r = {
        "proj": "P1",
        "stat": "Active",
        "entered": "2020-01-01",
        "withdrawn": "",
        "in_service": "",
        "mw": "10",
        "tech": "solar",
    }
    r.update(kw)
    return r


def codes(rows):
    return {f.code for f in parse_rows(rows, QUEUE_MAP)[1]}


def fixture_records():
    with (fixtures_dir() / "queue_synthetic.csv").open(newline="") as fh:
        recs, f = parse_rows(csv.DictReader(fh), QUEUE_MAP)
    assert not f
    return recs


@pytest.mark.parametrize(
    "row,code",
    [
        (base(proj=""), "missing_id"),
        (base(stat="Mystery"), "unknown_status"),
        (base(entered="31/12/2020"), "bad_date"),
        (base(entered=""), "missing_queue_date"),
        (base(mw="abc"), "bad_capacity"),
        (base(mw="-5"), "bad_capacity"),
        (base(mw="nan"), "bad_capacity"),
        (base(mw="inf"), "bad_capacity"),
        (base(stat="Withdrawn", withdrawn="2019-01-01"), "lifecycle_contradiction"),
        (
            base(stat="Withdrawn", withdrawn="2021-01-01", in_service="2022-01-01"),
            "lifecycle_contradiction",
        ),
        (base(stat="Operational", in_service="2019-01-01"), "lifecycle_contradiction"),
        (
            base(stat="Operational", in_service="2021-01-01", withdrawn="2022-01-01"),
            "lifecycle_contradiction",
        ),
        (base(stat="Active", in_service="2021-01-01"), "lifecycle_contradiction"),
        (base(stat="Active", withdrawn="2021-01-01"), "lifecycle_contradiction"),
    ],
)
def test_malformed_and_contradictory_rows_flagged_not_repaired(row, code):
    recs, f = parse_rows([row], QUEUE_MAP)
    assert code in {x.code for x in f} and recs == []


@pytest.mark.parametrize(
    "row,code",
    [
        (base(stat="Withdrawn"), "missing_withdrawal_date"),
        (base(stat="Operational"), "missing_in_service_date"),
    ],
)
def test_missing_terminal_dates_are_warnings_and_the_row_is_kept(row, code):
    """The publisher states withdrawal dates exist for only part of withdrawn requests: dropping or erroring on
    them would delete real records."""
    recs, f = parse_rows([row], QUEUE_MAP)
    assert len(recs) == 1
    assert [(x.code, x.severity) for x in f] == [(code, "warning")]


def test_duplicate_ids_flagged():
    assert "duplicate_id" in codes([base(), base()])


def test_original_values_preserved():
    recs, _ = parse_rows([base(stat="Active", mw="12.50")], QUEUE_MAP)
    assert recs[0].raw["stat"] == "Active" and recs[0].raw["mw"] == "12.50"


def test_cohort_2018_denominators_and_censoring():
    out = cohort_outcomes(fixture_records(), entry_year=2018, as_of=date(2026, 1, 1))
    assert (out["entered"], out["operational"], out["withdrawn"], out["active_censored"]) == (
        5,
        2,
        2,
        1,
    )
    assert out["share_operational_of_entered"] == pytest.approx(0.4)
    assert out["share_operational_of_resolved"] == pytest.approx(0.5)
    assert out["mature"] and any("censored" in w for w in out["warnings"])
    assert out["label"] == QUEUE_STAT_LABEL and "not a data-centre load-connection" in out["label"]


def test_immature_cohort_flagged_and_empty_cohort_has_no_false_precision():
    out = cohort_outcomes(fixture_records(), entry_year=2024, as_of=date(2026, 1, 1))
    assert not out["mature"] and any("immature" in w for w in out["warnings"])
    assert out["share_operational_of_resolved"] is None
    empty = cohort_outcomes(fixture_records(), entry_year=1999, as_of=date(2026, 1, 1))
    assert empty["share_operational_of_entered"] is None


def test_duplicate_key_is_entity_and_id_not_id_alone():
    a = base(proj="Q1", stat="Active")
    b = dict(a)
    # same id, different entity: distinct requests. QUEUE_MAP has no entity column, so use a map with one.
    from dataclasses import replace

    m = replace(QUEUE_MAP, entity="ent")
    recs, f = parse_rows([{**a, "ent": "APS"}, {**b, "ent": "PG&E"}], m)
    assert len(recs) == 2 and not [x for x in f if x.code == "duplicate_id"]
    recs, f = parse_rows([{**a, "ent": "APS"}, {**b, "ent": "APS"}], m)
    assert len(recs) == 1 and "duplicate_id" in {x.code for x in f}


def test_unassigned_id_literal_is_kept_with_a_warning_and_never_deduplicated():
    from dataclasses import replace

    m = replace(QUEUE_MAP, unassigned_id_literal="not assigned")
    rows = [base(proj="not assigned", stat="Active"), base(proj="not assigned", stat="Active")]
    recs, f = parse_rows(rows, m)
    assert len(recs) == 2
    assert [x.code for x in f] == ["unassigned_id", "unassigned_id"]


def test_excel_serial_dates_and_hostile_serials():
    from dataclasses import replace

    m = replace(QUEUE_MAP, date_format="excel_serial_1900")
    recs, _ = parse_rows([base(entered="43511", stat="Active")], m)
    assert recs[0].queue_date == date(2019, 2, 15)
    for bad in ("0", "-5", "999999", "nan", "abc", "inf"):
        _, f = parse_rows([base(entered=bad, stat="Active")], m)
        assert "bad_date" in {x.code for x in f}, bad
    recs, _ = parse_rows([base(entered="43511.9", stat="Active")], m)
    assert recs[0].queue_date == date(2019, 2, 15), "time-of-day fraction must not move the date"


def test_split_capacity_columns_warn_instead_of_silently_summing():
    from dataclasses import replace

    m = replace(QUEUE_MAP, extra_capacity=("mw2",))
    recs, f = parse_rows([{**base(stat="Active"), "mw2": "5"}], m)
    assert recs[0].capacity_mw == 10.0
    assert [x.code for x in f] == ["split_capacity"]


def test_suspended_is_unresolved_not_a_failure_in_cohort_outcomes():
    from dataclasses import replace

    m = replace(QUEUE_MAP, status_map={**QUEUE_MAP.status_map, "Susp": "suspended"})
    rows = [
        base(proj="a", stat="Active"),
        base(proj="b", stat="Susp"),
        base(proj="c", stat="Operational", in_service="2024-01-01"),
        base(proj="d", stat="Withdrawn", withdrawn="2021-01-01"),
    ]
    recs, f = parse_rows(rows, m)
    assert not [x for x in f if x.severity == "error"]
    out = cohort_outcomes(recs, entry_year=2020, as_of=date(2026, 1, 1))
    assert (out["entered"], out["suspended_unresolved"], out["active_censored"]) == (4, 1, 1)
    assert out["share_operational_of_resolved"] == pytest.approx(0.5)
    assert any("suspended" in w for w in out["warnings"])


def test_operational_without_in_service_date_is_reported_and_excluded_from_durations():
    recs, _ = parse_rows([base(proj="x", stat="Operational")], QUEUE_MAP)
    out = cohort_outcomes(recs, entry_year=2020, as_of=date(2026, 1, 1))
    assert out["median_years_queue_to_service_among_operational"] is None
    assert any("no in-service date" in w for w in out["warnings"])
