"""The source lifecycle is derived from recorded evidence; claims alone never raise it."""

from __future__ import annotations

import copy

import pytest

from gridforge_stress.lifecycle import State, lifecycle_of, lifecycle_report
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.registry import Source, load_json, load_sources, validate_against

pytestmark = pytest.mark.unit


def manifests():
    return [load_json(p) for p in sorted(fixtures_dir().rglob("*.manifest.json"))]


def src(**over):
    raw = copy.deepcopy(load_sources()["lbnl-queued-up"].raw)
    raw.update(over)
    return Source(raw)


def live(status="fetched_validated", sid="lbnl-queued-up"):
    return {"source_id": sid, "origin": "live_fetch", "retrieval_status": status}


def derived(sid="lbnl-queued-up", **over):
    m = {
        "source_id": sid,
        "origin": "source_derived_fixture",
        "retrieved_at": "2026-10-28T00:00:00Z",
        "upstream_release": "r1",
        "units": {"mw": "MW"},
        "transformations": ["t"],
    }
    m.update(over)
    return m


ACCESS = {
    "confirmed_on": "2026-10-31",
    "url": "https://example.test/x.xlsx",
    "http_status": 200,
    "bytes": 10,
    "evidence": "probe run 1",
}
READY = {
    "parser": "queue.parse_rows",
    "tests": ["tests/unit/test_queue.py"],
    "confirmed_on": "2026-10-31",
}


def test_today_no_source_is_beyond_the_publisher_page():
    reports = lifecycle_report(load_sources(), manifests())
    assert max(r.state for r in reports) == State.PUBLISHER_PAGE_READ


def test_a_fixture_built_outside_the_ladder_does_not_raise_the_state():
    by = {r.source_id: r for r in lifecycle_report(load_sources(), manifests())}
    assert (
        by["eia-epm"].state == State.UNVERIFIED and "out_of_ladder_fixture" in by["eia-epm"].flags
    )
    assert (
        by["lbnl-queued-up"].state == State.PUBLISHER_PAGE_READ
        and "out_of_ladder_fixture" in by["lbnl-queued-up"].flags
    )


def test_gated_and_unread_sources_are_flagged_not_hidden():
    by = {r.source_id: r for r in lifecycle_report(load_sources(), manifests())}
    assert {"BLOCKED_BY_POLICY", "ACCESS_RESTRICTED", "NOT_READ"} <= set(
        by["ercot-data-portal"].flags
    )


def test_the_full_chain_reaches_ready_for_oracle():
    s = src(data_access=ACCESS, oracle_ready=READY)
    assert lifecycle_of(s, [live(), derived()]).state == State.READY_FOR_ORACLE


@pytest.mark.parametrize(
    "name,source_over,ms,expected",
    [
        ("oracle_ready_claimed_alone", dict(oracle_ready=READY), [], State.PUBLISHER_PAGE_READ),
        ("access_without_a_fetch", dict(data_access=ACCESS), [], State.DATA_ACCESS_CONFIRMED),
        ("fetch_without_access_record", dict(), [live()], State.PUBLISHER_PAGE_READ),
        (
            "unparsed_fetch_is_not_validated",
            dict(data_access=ACCESS),
            [live("fetched_unparsed")],
            State.FETCHED_UNPARSED,
        ),
        (
            "fixture_without_a_validated_fetch",
            dict(data_access=ACCESS, oracle_ready=READY),
            [live("fetched_unparsed"), derived()],
            State.FETCHED_UNPARSED,
        ),
        (
            "fixture_with_incomplete_provenance",
            dict(data_access=ACCESS, oracle_ready=READY),
            [live(), derived(upstream_release="")],
            State.CONTENT_VALIDATED,
        ),
        (
            "fixture_with_no_units",
            dict(data_access=ACCESS, oracle_ready=READY),
            [live(), derived(units={})],
            State.CONTENT_VALIDATED,
        ),
        (
            "validated_fixture_but_no_oracle_record",
            dict(data_access=ACCESS),
            [live(), derived()],
            State.SOURCE_DERIVED_FIXTURE,
        ),
        (
            "other_sources_evidence_is_not_mine",
            dict(data_access=ACCESS, oracle_ready=READY),
            [live(sid="eia-930"), derived(sid="eia-930")],
            State.DATA_ACCESS_CONFIRMED,
        ),
    ],
)
def test_no_state_is_reached_without_every_earlier_one(name, source_over, ms, expected):
    assert lifecycle_of(src(**source_over), ms).state == expected, name


def test_an_unread_source_cannot_be_raised_by_its_other_evidence():
    s = Source(
        {
            **copy.deepcopy(load_sources()["ercot-data-portal"].raw),
            "data_access": ACCESS,
            "oracle_ready": READY,
        }
    )
    r = lifecycle_of(s, [live(sid="ercot-data-portal"), derived(sid="ercot-data-portal")])
    assert r.state == State.UNVERIFIED and "NOT_READ" in r.flags


def test_a_failed_validation_is_flagged():
    assert "FAILED_VALIDATION" in lifecycle_of(src(), [live("fetched_failed_validation")]).flags


def test_the_new_registry_fields_are_legal_and_the_shipped_registry_still_validates():
    doc = load_json(fixtures_dir().parent.parent / "configs" / "sources.json")
    validate_against("source", doc)
    doc["sources"][0]["data_access"] = ACCESS
    doc["sources"][0]["oracle_ready"] = READY
    validate_against("source", doc)
    doc["sources"][0]["data_access"] = {**ACCESS, "url": "http://insecure.test/x"}
    with pytest.raises(Exception, match="schema violation"):
        validate_against("source", doc)
