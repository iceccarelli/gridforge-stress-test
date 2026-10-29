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
        (base(mw="nan"), "bad_capacity"),
        (base(mw="inf"), "bad_capacity"),
    ],
)
def test_rows_that_cannot_be_placed_are_rejected_with_an_error(row, code):
    recs, f = parse_rows([row], QUEUE_MAP)
    assert recs == [] and [(x.code, x.severity) for x in f] == [(code, "error")]


@pytest.mark.parametrize(
    "row,code,flag",
    [
        (
            base(stat="Withdrawn", withdrawn="2019-01-01"),
            "date_order_conflict",
            "date_order_conflict",
        ),
        (
            base(stat="Operational", in_service="2019-01-01"),
            "date_order_conflict",
            "date_order_conflict",
        ),
        (
            base(stat="Withdrawn", withdrawn="2021-01-01", in_service="2022-01-01"),
            "status_date_conflict",
            "status_date_conflict",
        ),
        (
            base(stat="Operational", in_service="2021-01-01", withdrawn="2022-01-01"),
            "status_date_conflict",
            "status_date_conflict",
        ),
        (
            base(stat="Active", in_service="2021-01-01"),
            "status_date_conflict",
            "status_date_conflict",
        ),
        (
            base(stat="Active", withdrawn="2021-01-01"),
            "status_date_conflict",
            "status_date_conflict",
        ),
    ],
)
def test_conflicts_are_flagged_and_kept_never_dropped_or_repaired(row, code, flag):
    """Measured on the real LBNL data: 158 'active' rows carry an in-service date, 20 carry a withdrawal date, 50 have
    on_date < q_date, 6 have wd_date < q_date. Rejecting them would delete real publisher rows."""
    recs, f = parse_rows([row], QUEUE_MAP)
    assert len(recs) == 1 and flag in recs[0].flags
    assert code in {x.code for x in f} and all(x.severity == "warning" for x in f)
    assert recs[0].raw == row, "original values must be untouched"


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
    assert len(recs) == 1 and code in recs[0].flags
    assert [(x.code, x.severity) for x in f] == [(code, "warning")]


def test_repeated_keys_keep_every_copy_flagged_because_identical_copies_cannot_be_told_from_distinct_requests():
    recs, f = parse_rows([base(), base()], QUEUE_MAP)
    assert len(recs) == 2 and all("duplicate_key" in r.flags for r in recs)
    assert [x.code for x in f] == ["duplicate_id", "duplicate_id"]
    assert all(x.severity == "warning" for x in f)


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
    from dataclasses import replace

    m = replace(QUEUE_MAP, entity="ent")
    recs, f = parse_rows([{**a, "ent": "APS"}, {**a, "ent": "PG&E"}], m)
    assert len(recs) == 2 and not [x for x in f if x.code == "duplicate_id"]
    assert not any("duplicate_key" in r.flags for r in recs)
    recs, f = parse_rows([{**a, "ent": "APS"}, {**a, "ent": "APS"}], m)
    assert len(recs) == 2 and all("duplicate_key" in r.flags for r in recs)


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


def test_status_the_codebook_does_not_list_is_mapped_only_when_evidence_says_so():
    from dataclasses import replace

    rows = [base(proj="a", stat="unknown")]
    assert [x.code for x in parse_rows(rows, QUEUE_MAP)[1]] == [
        "unknown_status"
    ]  # not mapped => rejected loudly
    m = replace(QUEUE_MAP, status_map={**QUEUE_MAP.status_map, "unknown": "unknown"})
    recs, f = parse_rows(rows, m)
    assert recs[0].status == "unknown" and not f
    out = cohort_outcomes(recs, entry_year=2020, as_of=date(2026, 1, 1))
    assert out["status_unknown"] == 1 and out["share_operational_of_resolved"] is None
    assert any("'unknown'" in w for w in out["warnings"])


def test_impossible_date_order_is_excluded_from_durations_and_the_exclusion_is_disclosed():
    good = base(proj="g", stat="Operational", entered="2018-01-01", in_service="2021-01-01")
    bad = base(proj="b", stat="Operational", entered="2018-06-01", in_service="2017-01-01")
    recs, _ = parse_rows([good, bad], QUEUE_MAP)
    out = cohort_outcomes(recs, entry_year=2018, as_of=date(2026, 1, 1))
    assert out["operational"] == 2, "the flagged row still counts as operational"
    assert out["durations_used"] == 1 and out[
        "median_years_queue_to_service_among_operational"
    ] == pytest.approx(3.0, abs=0.01)
    assert any("impossible order" in w for w in out["warnings"])


def test_cohort_discloses_rows_sharing_a_key():
    recs, _ = parse_rows([base(), base()], QUEUE_MAP)
    out = cohort_outcomes(recs, entry_year=2020, as_of=date(2026, 1, 1))
    assert out["entered"] == 2 and any("share an (entity, id) key" in w for w in out["warnings"])


# --- durations (GF-002) ----------------------------------------------------------------------------------------


def years(a: str, b: str) -> float:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 365.25


def test_percentile_is_linear_interpolation_with_hand_computed_values():
    from gridforge_stress.queue import percentile

    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(xs, 0.0) == 1.0 and percentile(xs, 0.5) == 3.0 and percentile(xs, 1.0) == 5.0
    assert percentile(xs, 0.25) == 2.0 and percentile(xs, 0.75) == 4.0
    assert percentile([10.0, 20.0], 0.5) == 15.0 and percentile([7.0], 0.9) == 7.0
    assert percentile([0.0, 10.0], 0.25) == 2.5
    for bad in (-0.1, 1.1):
        with pytest.raises(ValueError):
            percentile(xs, bad)
    with pytest.raises(ValueError):
        percentile([], 0.5)


def op(pid, entered, in_service, region="West", **kw):
    return base(
        proj=pid, stat="Operational", entered=entered, in_service=in_service, region=region, **kw
    )


def test_duration_summary_matches_hand_computed_quartiles_and_discloses_exclusions():
    from gridforge_stress.queue import duration_summary

    rows = [
        op("a", "2015-01-01", "2018-01-01"),
        op("b", "2015-01-01", "2020-01-01"),
        op("c", "2015-01-01", "2022-01-01"),
        op("d", "2015-01-01", "2024-01-01"),
        op("rev", "2019-01-01", "2018-01-01"),  # impossible order: excluded, disclosed
        base(proj="act", stat="Active"),  # not operational
    ]
    recs, _ = parse_rows(rows, QUEUE_MAP)
    out = duration_summary(recs)
    exp = sorted(
        years("2015-01-01", d) for d in ("2018-01-01", "2020-01-01", "2022-01-01", "2024-01-01")
    )
    assert out["overall"]["n"] == 4 and out["excluded_impossible_date_order"] == 1
    assert out["overall"]["median_years"] == pytest.approx((exp[1] + exp[2]) / 2, abs=1e-3)
    assert out["overall"]["p25_years"] == pytest.approx(exp[0] + 0.75 * (exp[1] - exp[0]), abs=1e-3)
    assert out["overall"]["min_years"] == pytest.approx(exp[0], abs=1e-3)
    assert (
        "not a time-to-power for a load" in out["definition"] and out["label"] == QUEUE_STAT_LABEL
    )


def test_duration_summary_counts_a_repeated_key_once_and_filters_by_cod_year_and_groups():
    from dataclasses import replace

    from gridforge_stress.queue import duration_summary

    m = replace(QUEUE_MAP, entity="ent")
    rows = [
        {**op("x", "2015-01-01", "2019-01-01"), "ent": "E"},
        {
            **op("x", "2015-01-01", "2019-01-01"),
            "ent": "E",
        },  # same request listed again (another location)
        {**op("y", "2015-01-01", "2023-01-01", region="PJM"), "ent": "E"},
        {**op("z", "2010-01-01", "2012-01-01", region="PJM"), "ent": "E"},
    ]
    recs, _ = parse_rows(rows, m)
    out = duration_summary(recs, group_by="region")
    assert out["overall"]["n"] == 3 and out["excluded_repeated_key_rows"] == 1
    assert out["by_region"]["West"]["n"] == 1 and out["by_region"]["PJM"]["n"] == 2
    late = duration_summary(recs, cod_year_min=2018, cod_year_max=2025)
    assert late["overall"]["n"] == 2 and late["cod_year_range"] == [2018, 2025]
    assert duration_summary([])["overall"] == {"n": 0}


def test_capacity_summary_counts_each_request_once_and_discloses_the_double_count():
    from dataclasses import replace

    from gridforge_stress.queue import capacity_summary

    m = replace(QUEUE_MAP, entity="ent")
    rows = [
        {**base(proj="a", stat="Active", mw="100"), "ent": "E"},
        {**base(proj="a", stat="Active", mw="100"), "ent": "E"},  # same request, second location
        {**base(proj="a", stat="Active", mw="100"), "ent": "E"},  # third location
        {**base(proj="b", stat="Active", mw="50"), "ent": "E"},
        {**base(proj="b", stat="Active", mw="30"), "ent": "E"},  # same key, capacity disagrees
        {**base(proj="c", stat="Operational", mw="7", in_service="2021-01-01"), "ent": "E"},
        {
            **base(proj="c", stat="Operational", mw="7", in_service="2021-01-01"),
            "ent": "F",
        },  # other entity: distinct
    ]
    recs, _ = parse_rows(rows, m)
    out = capacity_summary(recs)
    assert out["naive_row_sum"] == 100 * 3 + 50 + 30 + 7 + 7  # 394
    assert out["deduplicated_sum"] == 100 + 50 + 7 + 7  # 164: first row of each repeated key only
    assert out["double_counting_avoided_mw"] == 100 + 100 + 30  # 230 = 394 - 164
    assert out["naive_row_sum"] - out["deduplicated_sum"] == out["double_counting_avoided_mw"]
    assert out["rows_not_counted"] == 3 and out["keys_with_differing_capacity"] == 1
    assert out["by_status_deduplicated"] == {"active": 150.0, "operational": 14.0}
    assert "unit UNVERIFIED" in out["label"] and "not a connection probability" in out["label"]


def test_capacity_summary_breaks_active_and_other_capacity_down_by_region_once_per_request():
    from dataclasses import replace

    from gridforge_stress.queue import capacity_summary

    m = replace(QUEUE_MAP, entity="ent")
    rows = [
        {**base(proj="a", stat="Active", mw="100"), "ent": "E", "region": "PJM"},
        {
            **base(proj="a", stat="Active", mw="100"),
            "ent": "E",
            "region": "PJM",
        },  # same request again
        {**base(proj="b", stat="Active", mw="40"), "ent": "E", "region": "ERCOT"},
        {
            **base(proj="c", stat="Withdrawn", mw="9", withdrawn="2021-01-01"),
            "ent": "E",
            "region": "PJM",
        },
        {**base(proj="d", stat="Active", mw="5"), "ent": "E"},  # no region in the source row
    ]
    recs, _ = parse_rows(rows, m)
    by_region = capacity_summary(recs)["by_region_status_deduplicated"]
    assert by_region["PJM"] == {
        "active": 100.0,
        "withdrawn": 9.0,
    }  # the repeated request is counted once
    assert by_region["ERCOT"] == {"active": 40.0}
    assert by_region["(blank)"] == {"active": 5.0}
    # the regional cells add up to the same deduplicated total as the overall figure
    overall = capacity_summary(recs)
    assert (
        sum(v for cell in by_region.values() for v in cell.values()) == overall["deduplicated_sum"]
    )


def test_capacity_summary_without_repeats_equals_the_naive_sum():
    from gridforge_stress.queue import capacity_summary

    recs, _ = parse_rows([base(proj="x", mw="5"), base(proj="y", mw="6")], QUEUE_MAP)
    out = capacity_summary(recs)
    assert (
        out["naive_row_sum"] == out["deduplicated_sum"] == 11.0
        and out["double_counting_avoided_mw"] == 0.0
    )
    assert capacity_summary([])["naive_row_sum"] == 0.0


def test_non_positive_capacity_is_kept_and_flagged_and_excluded_only_from_capacity_sums():
    """The real data has 2 zero and 16 negative mw_1 values. Their dates are fine, so they stay in duration statistics."""
    from gridforge_stress.queue import capacity_summary, duration_summary

    rows = [
        base(
            proj="neg", stat="Operational", mw="-75", entered="2015-01-01", in_service="2019-01-01"
        ),
        base(
            proj="zero", stat="Operational", mw="0", entered="2015-01-01", in_service="2019-01-01"
        ),
        base(proj="ok", stat="Operational", mw="40", entered="2015-01-01", in_service="2019-01-01"),
    ]
    recs, f = parse_rows(rows, QUEUE_MAP)
    assert len(recs) == 3 and [r.flags for r in recs[:2]] == [("nonpositive_capacity",)] * 2
    assert [x.code for x in f] == ["nonpositive_capacity", "nonpositive_capacity"]
    assert all(x.severity == "warning" for x in f)
    cap = capacity_summary(recs)
    assert (
        cap["naive_row_sum"] == cap["deduplicated_sum"] == 40.0
        and cap["rows_excluded_nonpositive_capacity"] == 2
    )
    assert duration_summary(recs)["overall"]["n"] == 3, (
        "dates are valid: all three belong in duration statistics"
    )


def test_non_finite_capacity_is_still_rejected():
    for v in ("nan", "inf", "-inf", "abc", ""):
        recs, f = parse_rows([base(mw=v)], QUEUE_MAP)
        assert recs == [] and f[0].code == "bad_capacity" and f[0].severity == "error", v


def test_population_option_separates_operational_from_any_status_with_an_in_service_date():
    from gridforge_stress.queue import duration_summary

    rows = [
        base(proj="op", stat="Operational", entered="2015-01-01", in_service="2019-01-01"),
        base(
            proj="act", stat="Active", entered="2015-01-01", in_service="2021-01-01"
        ),  # status/date conflict row
        base(proj="wd", stat="Withdrawn", entered="2015-01-01", in_service="2017-01-01"),
    ]
    recs, _ = parse_rows(rows, QUEUE_MAP)
    op = duration_summary(recs)
    anyst = duration_summary(recs, population="has_in_service_date")
    assert op["population"] == "operational" and op["overall"]["n"] == 1
    assert anyst["population"] == "has_in_service_date" and anyst["overall"]["n"] == 3
    assert op["operational_with_in_service_date"] == anyst["operational_with_in_service_date"] == 1
    assert anyst["population_with_in_service_date"] == 3 and "any status" in anyst["definition"]
    with pytest.raises(ValueError):
        duration_summary(recs, population="bogus")


def test_cod_year_grouping_and_months_match_hand_computation():
    from gridforge_stress.queue import duration_summary

    rows = [
        base(proj="a", stat="Operational", entered="2015-01-01", in_service="2019-01-01"),
        base(proj="b", stat="Operational", entered="2017-01-01", in_service="2019-06-01"),
        base(proj="c", stat="Operational", entered="2010-01-01", in_service="2020-01-01"),
    ]
    recs, _ = parse_rows(rows, QUEUE_MAP)
    out = duration_summary(recs, group_by="cod_year")
    assert set(out["by_cod_year"]) == {"2019", "2020"} and out["by_cod_year"]["2019"]["n"] == 2
    ya, yb = years("2015-01-01", "2019-01-01"), years("2017-01-01", "2019-06-01")
    assert out["by_cod_year"]["2019"]["median_years"] == pytest.approx((ya + yb) / 2, abs=1e-3)
    assert out["by_cod_year"]["2019"]["median_months"] == pytest.approx(
        (ya + yb) / 2 * 12, abs=2e-3
    )
    assert out["by_cod_year"]["2020"]["n"] == 1
