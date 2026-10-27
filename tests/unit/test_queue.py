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
        (base(stat="Withdrawn"), "lifecycle_contradiction"),
        (base(stat="Operational"), "lifecycle_contradiction"),
        (base(stat="Operational", in_service="2019-01-01"), "lifecycle_contradiction"),
        (base(stat="Active", in_service="2021-01-01"), "lifecycle_contradiction"),
    ],
)
def test_malformed_and_contradictory_rows_flagged_not_repaired(row, code):
    recs, f = parse_rows([row], QUEUE_MAP)
    assert code in {x.code for x in f} and recs == []


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
