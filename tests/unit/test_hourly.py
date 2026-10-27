from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gridforge_stress.hourly import SCOPE_LABEL, forecast_error, validate_hourly

pytestmark = pytest.mark.unit
NOW = datetime(2026, 3, 9, 0, tzinfo=UTC)


def row(h, v=1.0, pub=None, day=8):
    r = {"period_utc": f"2026-03-{day:02d}T{h:02d}:00:00Z", "value": v}
    if pub:
        r["published_at"] = pub
    return r


def codes(rows, **kw):
    return [f.code for f in validate_hourly(rows, as_of=kw.pop("as_of", NOW), **kw)[1]]


def test_clean_series_has_no_errors():
    assert codes([row(h) for h in range(5, 9)]) == []


def test_naive_local_and_offset_timestamps_rejected():
    for ts in ("2026-03-08T02:30:00", "2026-03-08T03:00:00-05:00", "garbage"):
        assert "bad_timestamp" in codes([{"period_utc": ts, "value": 1}])


def test_dst_spring_forward_local_gap_is_not_a_utc_gap():
    # US spring-forward 2026-03-08: UTC remains continuous hourly
    assert "gap" not in codes([row(h) for h in range(5, 12)])


def test_missing_hours_and_duplicates_and_revisions():
    assert "gap" in codes([row(1), row(4)])
    assert "duplicate" in codes([row(1, 5), row(1, 5)])
    series, f = validate_hourly(
        [row(1, 5, "2026-03-08T02:00:00Z"), row(1, 6, "2026-03-08T03:00:00Z")], as_of=NOW
    )
    assert series[0]["value"] == 6 and "revision" in {x.code for x in f}
    series2, _ = validate_hourly(
        [row(1, 6, "2026-03-08T03:00:00Z"), row(1, 5, "2026-03-08T02:00:00Z")], as_of=NOW
    )
    assert series2[0]["value"] == 6, "order of arrival must not change which revision wins"


def test_staleness_and_empty_and_bad_values():
    assert "stale" in codes([row(1)], as_of=NOW + timedelta(days=5))
    assert "empty" in codes([])
    assert "bad_value" in codes([{"period_utc": "2026-03-08T01:00:00Z", "value": "x"}])
    assert "bad_value" in codes([row(1, float("nan"))])
    assert "not_hour_aligned" in codes([{"period_utc": "2026-03-08T01:30:00Z", "value": 1}])


def test_forecast_vs_actual_reports_coverage_and_scope():
    f = [row(1, 110), row(2, 120), row(3, 130)]
    a = [row(1, 100), row(2, 100)]
    out = forecast_error(f, a)
    assert out["intervals_compared"] == 2 and out["forecast_only"] == 1
    assert out["mean_error"] == 15 and out["scope"] == SCOPE_LABEL
    assert forecast_error([], [])["mean_error"] is None
