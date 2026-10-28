"""Pins what the LBNL Queued Up 2026 workbook was OBSERVED to contain.

Label: observed 2026-10-28 from GitHub Actions probe run 40615136879 (workflow `probe`, step "Peek at registered
workbooks"); workbook https://eta-publications.lbl.gov/sites/default/files/2026-05/lbnl_ix_queue_data_file_thru2025.xlsx,
15,571,236 bytes, sha256 794582d3281c6a305e9615fcfec3fae9dc85be2165216d33760b677e976a08b6.
Data: LBNL / GridTracker, CC BY 4.0. These constants are evidence copied from the log, not assumptions; when a newer
edition changes the layout this test should fail so the ColumnMap is re-derived from fresh evidence.
"""

from __future__ import annotations

import json

import pytest

from gridforge_stress.paths import fixtures_dir
from gridforge_stress.queue import LBNL_2026_MAP, cohort_outcomes, parse_rows
from gridforge_stress.registry import load_sources

pytestmark = pytest.mark.contract

OBSERVED_DATA_SHEET = "03. Complete Queue Data"
OBSERVED_DATA_DIMENSION = (
    "A1:AD38203"  # 30 columns (A..AD), 38,203 rows incl. 'RETURN TO CONTENTS' and header rows
)
OBSERVED_HEADER = [
    "q_id",
    "q_status",
    "q_date",
    "prop_date",
    "on_date",
    "wd_date",
    "ia_date",
    "IA_phase_raw",
    "IA_phase_clean",
    "county",
    "state",
    "fips_code",
    "poi_name",
    "region",
    "project_name",
    "utility",
    "entity",
    "developer",
    "cluster",
    "service",
    "project_type",
    "type_1",
    "type_2",
    "type_3",
    "type_clean",
    "mw_1",
    "mw_2",
    "mw_3",
    "q_year",
    "prop_year",
]
# Codebook ('04. Data Codebook', 'Field Name | Description | Notes'), rows read so far:
CODEBOOK_SEEN = {
    "q_id": 'queue position / ID number | Combine with "entity" to form a unique identifier across the full dataset',
    "q_status": "current queue status | One of: active, withdrawn, suspended, or operational",
    "q_date": "interconnection request date (date project entered queue)",
    "prop_date": "proposed online date from interconnection application | Proposed date can be revised",
    "on_date": "date project became operational (if applicable)",
    "wd_date": "date project withdrawn from queue (if applicable)",
}


# MEASURED 2026-10-28 from GitHub Actions probe run 40615868160 (step "Profile the observed project sheet"), same
# workbook sha256 794582d3281c6a305e9615fcfec3fae9dc85be2165216d33760b677e976a08b6. Counts over the whole sheet.
MEASURED_DATA_ROWS = 38_201
MEASURED_STATUS_COUNTS = {
    "withdrawn": 24_221,
    "active": 8_513,
    "operational": 4_789,
    "suspended": 668,
    "unknown": 10,
}
MEASURED = {
    "q_date_blank": 665,  # rows that cannot be placed in an entry-year cohort
    "wd_date_blank_when_withdrawn": 8_639,
    "on_date_blank_when_operational": 1_500,
    "active_rows_with_wd_date": 8_513 - 8_493,
    "active_rows_with_on_date": 8_513 - 8_355,
    "suspended_rows_with_wd_date": 668 - 661,
    "wd_before_q_date": (6, 15_414),  # (violating, rows with both dates)
    "on_before_q_date": (50, 3_566),
    "on_before_wd_date": (56, 241),
    "distinct_entity_id_keys": 38_065,
    "repeated_entity_id_keys": 135,
    "rows_in_repeated_keys": 270,
    "unassigned_id_rows": 1,
    "mw_1_min": -75.0,
    "mw_2_populated": 1_096,
    "mw_3_populated": 45,
    "non_generation_project_types": {"Surplus": 100, "Upgrade": 80, "Replacement": 21},
    # run 40616451452 (extended profile; same workbook):
    "mw_1_zero": 2,
    "mw_1_negative": 16,
    "withdrawn_rows_with_on_date": 24_221 - 23_964,
    "operational_rows_with_wd_date": 4_789 - 4_775,
    "suspended_rows_with_on_date": 668 - 663,
    "unknown_rows_with_any_terminal_date": 0,
    "repeated_keys_fully_identical": 0,
    "repeated_keys_with_differences": 135,
    "repeated_keys_county_differs": 134,
    "repeated_keys_state_differs": 33,
    "repeated_keys_on_date_differs": 79,
    # run 40638926321 (dup_value_col=mw_1):
    "repeated_keys_mw_1_identical": 134,
    "repeated_keys_mw_1_differing": 1,
    "mw_1_sum_all_rows_in_repeated_keys": 53_917.2,
    "mw_1_sum_on_non_first_rows": 26_458.6,
}


def test_observed_header_has_30_columns_matching_the_sheet_dimension():
    assert len(OBSERVED_HEADER) == 30 == 26 + 4  # 'AD' is column 30
    assert OBSERVED_DATA_DIMENSION.endswith("AD38203")
    assert len(set(OBSERVED_HEADER)) == 30


def test_column_map_uses_only_observed_columns():
    m = LBNL_2026_MAP
    used = {
        m.project_id,
        m.entity,
        m.status,
        m.queue_date,
        m.withdrawn_date,
        m.in_service_date,
        m.capacity_mw,
        m.technology,
        *m.extra_capacity,
    }
    assert used <= set(OBSERVED_HEADER), used - set(OBSERVED_HEADER)


def test_status_map_covers_exactly_the_measured_values_and_the_data_disagrees_with_the_codebook():
    codebook = {"active", "withdrawn", "suspended", "operational"}
    assert "One of: active, withdrawn, suspended, or operational" in CODEBOOK_SEEN["q_status"]
    assert set(MEASURED_STATUS_COUNTS) == codebook | {"unknown"}, (
        "the data has a fifth status the codebook omits"
    )
    assert set(LBNL_2026_MAP.status_map) == set(MEASURED_STATUS_COUNTS)
    assert sum(MEASURED_STATUS_COUNTS.values()) == MEASURED_DATA_ROWS


def test_codebook_says_id_is_unique_only_together_with_entity():
    assert "entity" in CODEBOOK_SEEN["q_id"] and LBNL_2026_MAP.entity == "entity"


def test_observed_sample_fixture_matches_the_pinned_layout_and_is_attributed():
    doc = json.loads((fixtures_dir() / "lbnl_observed_sample.json").read_text())
    assert doc["observed_layout"]["header"] == OBSERVED_HEADER
    assert all(list(r) == OBSERVED_HEADER for r in doc["rows"])
    man = json.loads((fixtures_dir() / "lbnl_observed_sample.json.manifest.json").read_text())
    assert man["origin"] == "source_derived_fixture"
    assert "CC BY 4.0" in man["attribution"] and "GridTracker" in man["attribution"]
    assert "40615136879" in man["upstream_release"] and "794582d3" in man["upstream_release"]


def test_real_observed_rows_parse_with_known_quirks_as_warnings_not_rejections():
    rows = json.loads((fixtures_dir() / "lbnl_observed_sample.json").read_text())["rows"]
    recs, findings = parse_rows(rows, LBNL_2026_MAP)
    assert len(recs) == 6
    assert not [f for f in findings if f.severity == "error"]
    codes = {f.code for f in findings}
    assert {"unassigned_id", "missing_withdrawal_date", "missing_in_service_date"} <= codes
    first = recs[0]
    assert (first.project_id, first.entity, first.status, first.technology, first.capacity_mw) == (
        "not assigned",
        "APS",
        "withdrawn",
        "Solar",
        20.0,
    )
    assert [r.queue_date.year for r in recs] == [2019, 2005, 2008, 2008, 2004, 2006], (
        "serials agree with q_year"
    )
    assert [int(r.raw["q_year"]) for r in recs] == [r.queue_date.year for r in recs]


def test_a_six_row_sample_cannot_support_cohort_shares_as_if_representative():
    rows = json.loads((fixtures_dir() / "lbnl_observed_sample.json").read_text())["rows"]
    recs, _ = parse_rows(rows, LBNL_2026_MAP)
    from datetime import date

    out = cohort_outcomes(recs, entry_year=2008, as_of=date(2026, 1, 1))
    assert out["entered"] == 2 and out["operational"] == 0 and out["withdrawn"] == 2
    assert "not a data-centre load-connection probability" in out["label"]


def test_registry_states_the_observed_layout_and_keeps_the_rest_unverified():
    lbnl = load_sources()["lbnl-queued-up"]
    text = " ".join(lbnl.raw["limitations"])
    assert OBSERVED_DATA_SHEET in text and "04. Data Codebook" in text
    assert "UNVERIFIED" in text, "units and unread codebook rows must stay unverified"
    assert lbnl.live_fetch == "disabled_until_verified"


def test_real_profile_spec_is_callable_and_uses_only_observed_columns():
    """The CLI test monkeypatches the spec; this checks the REAL one against the REAL signature and header."""
    import inspect

    from gridforge_stress.queue import LBNL_2026_PROFILE
    from gridforge_stress.xlsx_peek import profile_sheet

    inspect.signature(profile_sheet).bind(b"", **LBNL_2026_PROFILE)  # raises TypeError on a bad key
    spec = LBNL_2026_PROFILE
    assert spec["sheet_name"] == OBSERVED_DATA_SHEET
    used = set(spec["columns"]) | {c for t in spec["blank_checks"] for c in (t[0], t[2])}
    used |= (
        {c for t in spec["order_checks"] for c in t}
        | set(spec["dup_key"])
        | set(spec["dup_exclude"])
    )
    assert spec["header_marker"] in OBSERVED_HEADER
    assert used <= set(OBSERVED_HEADER), used - set(OBSERVED_HEADER)


def test_measured_numbers_are_internally_consistent():
    """Arithmetic over the pinned measurements, so a transcription error here is caught."""
    m = MEASURED
    assert m["active_rows_with_wd_date"] == 20 and m["active_rows_with_on_date"] == 158
    assert m["suspended_rows_with_wd_date"] == 7
    assert (
        24_221 - m["wd_date_blank_when_withdrawn"] == 15_582
    )  # withdrawn rows that DO have wd_date
    assert 4_789 - m["on_date_blank_when_operational"] == 3_289
    assert (
        m["distinct_entity_id_keys"]
        + m["unassigned_id_rows"]
        + (m["rows_in_repeated_keys"] - m["repeated_entity_id_keys"])
        == MEASURED_DATA_ROWS
    )
    assert m["rows_in_repeated_keys"] == 2 * m["repeated_entity_id_keys"]


def test_parser_rules_are_consistent_with_what_the_real_data_contains():
    """The measurements above mean these combinations EXIST in the publisher's data, so each must be kept and flagged,
    never rejected: that is the contract between the measured data and the parser."""
    base = {
        "q_id": "Q1",
        "entity": "APS",
        "q_status": "active",
        "q_date": "43511",
        "wd_date": "",
        "on_date": "",
        "mw_1": "10",
        "mw_2": "",
        "mw_3": "",
        "type_clean": "Solar",
    }
    cases = {
        "active_with_on_date": {**base, "on_date": "44000"},
        "active_with_wd_date": {**base, "wd_date": "44000"},
        "suspended_with_wd_date": {**base, "q_status": "suspended", "wd_date": "44000"},
        "operational_without_on_date": {**base, "q_status": "operational"},
        "withdrawn_without_wd_date": {**base, "q_status": "withdrawn"},
        "operational_before_queue_entry": {**base, "q_status": "operational", "on_date": "40000"},
        "withdrawn_before_queue_entry": {**base, "q_status": "withdrawn", "wd_date": "40000"},
        "status_unknown": {**base, "q_status": "unknown"},
    }
    for name, row in cases.items():
        recs, findings = parse_rows([row], LBNL_2026_MAP)
        assert len(recs) == 1, f"{name}: real publisher row was rejected"
        assert not [f for f in findings if f.severity == "error"], name


def test_only_rows_that_cannot_be_placed_in_a_cohort_are_rejected():
    base = {"q_id": "Q1", "entity": "APS", "q_status": "active", "q_date": "43511", "mw_1": "10"}
    assert (
        parse_rows([{**base, "q_date": ""}], LBNL_2026_MAP)[0] == []
    )  # 665 such rows exist in the data
    kept, found = parse_rows(
        [{**base, "mw_1": "-75"}], LBNL_2026_MAP
    )  # a negative mw_1 exists (min -75)
    assert (
        len(kept) == 1
        and kept[0].flags == ("nonpositive_capacity",)
        and found[0].severity == "warning"
    )
    assert len(parse_rows([{**base, "q_status": "unknown"}], LBNL_2026_MAP)[0]) == 1


def test_terminal_date_populations_reconcile_with_the_column_totals():
    m = MEASURED
    wd_total = 15_623  # non-blank wd_date over the sheet
    on_total = 3_709  # non-blank on_date over the sheet
    assert (
        wd_total
        == (24_221 - m["wd_date_blank_when_withdrawn"])
        + m["active_rows_with_wd_date"]
        + m["suspended_rows_with_wd_date"]
        + m["operational_rows_with_wd_date"]
        + m["unknown_rows_with_any_terminal_date"]
    )
    assert (
        on_total
        == (4_789 - m["on_date_blank_when_operational"])
        + m["active_rows_with_on_date"]
        + m["withdrawn_rows_with_on_date"]
        + m["suspended_rows_with_on_date"]
        + m["unknown_rows_with_any_terminal_date"]
    )


def test_repeated_keys_are_distinct_rows_not_copies_so_the_parser_keeps_every_row_and_the_count_matches():
    """135 repeated keys, none identical (county differs in 134): they are NOT duplicate copies, so de-duplicating by key
    would delete real rows. Capacity repeated across such rows is measured separately before any summing is allowed."""
    assert MEASURED["repeated_keys_fully_identical"] == 0
    assert MEASURED["repeated_keys_with_differences"] == MEASURED["repeated_entity_id_keys"]
    assert MEASURED["repeated_keys_county_differs"] <= MEASURED["repeated_entity_id_keys"]
    a = {
        "q_id": "Q9",
        "entity": "APS",
        "q_status": "active",
        "q_date": "43511",
        "mw_1": "10",
        "county": "A",
    }
    b = {**a, "county": "B"}
    recs, _ = parse_rows([a, b], LBNL_2026_MAP)
    assert len(recs) == 2 and all("duplicate_key" in r.flags for r in recs)


def test_non_positive_capacity_rows_are_kept_flagged_and_counted():
    assert MEASURED["mw_1_zero"] + MEASURED["mw_1_negative"] == 18
    base = {"q_id": "Q1", "entity": "APS", "q_status": "active", "q_date": "43511"}
    for v in ("0", "-1", "0.5"):
        recs, _ = parse_rows([{**base, "mw_1": v}], LBNL_2026_MAP)
        assert len(recs) == 1, "valid dates: kept for duration statistics"
        assert ("nonpositive_capacity" in recs[0].flags) is (v != "0.5")


def test_measured_capacity_repeat_is_consistent_and_motivates_counting_requests_once():
    m = MEASURED
    assert (
        m["repeated_keys_mw_1_identical"] + m["repeated_keys_mw_1_differing"]
        == m["repeated_entity_id_keys"]
    )
    first_rows = m["mw_1_sum_all_rows_in_repeated_keys"] - m["mw_1_sum_on_non_first_rows"]
    assert first_rows == pytest.approx(27_458.6)
    # 134 identical pairs => the non-first rows mirror the first rows, apart from the one differing key (1,000 MW)
    assert first_rows - m["mw_1_sum_on_non_first_rows"] == pytest.approx(1_000.0)
    share = m["mw_1_sum_on_non_first_rows"] / m["mw_1_sum_all_rows_in_repeated_keys"]
    assert 0.49 < share < 0.50, (
        "about half of the capacity sitting in repeated keys is on rows that must not be re-counted"
    )


def test_capacity_summary_reproduces_the_measured_double_count_shape_on_the_observed_sample_rows():
    from gridforge_stress.queue import capacity_summary

    rows = json.loads((fixtures_dir() / "lbnl_observed_sample.json").read_text())["rows"]
    recs, _ = parse_rows(rows, LBNL_2026_MAP)
    out = capacity_summary(recs)
    assert (
        out["naive_row_sum"] == out["deduplicated_sum"] == 20 + 24 + 900 + 300 + 700 + 60
    )  # no repeats in these rows


# --- run 40639536058: real-data durations/capacity and LBNL's own published table -----------------------------------
# Measured 2026-10-28 by `probe lbnl-queued-up --queue-stats --show-sheet "37. IR to COD - all"` (same workbook sha256).
# OUR numbers were produced by the parser BEFORE non-positive-capacity rows were kept (those 18 rows were rejected then),
# so the kept/rejected counts and duration n below describe that parser version, not the current one.
OUR_RUN2 = {
    "rows": 38_201,
    "kept": 37_518,
    "rejected": 683,
    "operational_with_in_service_date": 3_145,
    "excluded_impossible_date_order": 48,
    "excluded_repeated_key_rows": 0,
    "all_cod_years": {"n": 3_097, "p25_years": 1.96, "median_years": 3.581, "p75_years": 5.183},
    "cod_2018_2025": {"n": 1_439, "p25_years": 3.086, "median_years": 4.394, "p75_years": 5.844},
    "capacity": {
        "naive_row_sum": 6_686_499.302,
        "deduplicated_sum": 6_660_040.702,
        "double_counting_avoided_mw": 26_458.6,
        "rows_not_counted": 135,
        "keys_with_differing_capacity": 1,
        "dedup_by_status": {
            "active": 1_739_387.301,
            "operational": 502_925.395,
            "suspended": 133_561.625,
            "unknown": 967.0,
            "withdrawn": 4_283_199.381,
        },
        "naive_by_status": {
            "active": 1_739_387.301,
            "operational": 529_383.995,
            "suspended": 133_561.625,
            "unknown": 967.0,
            "withdrawn": 4_283_199.381,
        },
    },
}
# LBNL's own sheet '37. IR to COD - all': in-service year -> (n requests, median duration in months), as published.
PUBLISHER_IR_TO_COD = {
    2005: (63, 17.674113009198422),
    2006: (76, 22.519710906701711),
    2007: (96, 25.558475689881728),
    2008: (114, 21.764126149802891),
    2009: (97, 31.077529566360049),
    2010: (107, 33.508541392904071),
    2011: (122, 35.348226018396844),
    2012: (141, 31.471747700394221),
    2013: (102, 35.791721419185279),
    2014: (108, 45.71287779237845),
    2015: (164, 37.812089356110377),
    2016: (180, 43.988173455978973),
    2017: (138, 39.881734559789749),
    2018: (152, 41.869250985545342),
    2019: (153, 46.484888304862032),
    2020: (227, 46.057818659658338),
    2021: (200, 49.967148488830482),
    2022: (176, 55.157687253613673),
    2023: (265, 55.749014454664909),
    2024: (335, 62.713534822601837),
    2025: (294, 60.82457293035479),
}
PUBLISHER_NOTE_SAMPLE = 3_310  # 'Sample includes 3,310 projects from 6 ISOs and 19 non-ISO BAs ...'


def test_our_run2_counts_reconcile():
    o = OUR_RUN2
    assert o["kept"] + o["rejected"] == o["rows"]
    assert (
        o["rejected"]
        == MEASURED["q_date_blank"] + MEASURED["mw_1_zero"] + MEASURED["mw_1_negative"]
    )  # 665 + 18
    assert (
        o["operational_with_in_service_date"] - o["excluded_impossible_date_order"]
        == o["all_cod_years"]["n"]
    )
    for k in ("all_cod_years", "cod_2018_2025"):
        d = o[k]
        assert d["p25_years"] < d["median_years"] < d["p75_years"]
    assert o["cod_2018_2025"]["n"] < o["all_cod_years"]["n"]


def test_capacity_run2_reconciles_with_the_double_count_and_the_status_split():
    c = OUR_RUN2["capacity"]
    assert c["naive_row_sum"] - c["deduplicated_sum"] == pytest.approx(
        c["double_counting_avoided_mw"], abs=1e-3
    )
    assert sum(c["dedup_by_status"].values()) == pytest.approx(c["deduplicated_sum"], abs=1e-3)
    assert sum(c["naive_by_status"].values()) == pytest.approx(c["naive_row_sum"], abs=1e-3)
    assert c["rows_not_counted"] == MEASURED["repeated_entity_id_keys"]
    assert c["double_counting_avoided_mw"] == pytest.approx(
        MEASURED["mw_1_sum_on_non_first_rows"]
    ), "matches run 40638926321"
    # every repeated-key extra row is operational: the whole double count sits in the operational status
    diffs = {k: c["naive_by_status"][k] - c["dedup_by_status"][k] for k in c["naive_by_status"]}
    assert diffs["operational"] == pytest.approx(c["double_counting_avoided_mw"], abs=1e-3)
    assert all(v == pytest.approx(0.0) for k, v in diffs.items() if k != "operational")


def test_publisher_table_transcription_reconciles_with_its_own_sample_note():
    assert sum(n for n, _ in PUBLISHER_IR_TO_COD.values()) == PUBLISHER_NOTE_SAMPLE
    assert sum(PUBLISHER_IR_TO_COD[y][0] for y in range(2018, 2026)) == 1_802
    assert min(PUBLISHER_IR_TO_COD) == 2005 and max(PUBLISHER_IR_TO_COD) == 2025


def test_cross_check_finding_our_operational_population_is_not_the_publishers_sample():
    """The publisher's sample (3,310) exceeds our ENTIRE operational-with-in-service-date population (3,145 before any
    exclusion), so its sample is not a subset of ours. 420 requests have an in-service date but are not 'operational'.
    Hypothesis (UNVERIFIED): the publisher keys on the presence of both dates, not on status. Measured next with
    population='has_in_service_date' per COD year."""
    assert OUR_RUN2["operational_with_in_service_date"] < PUBLISHER_NOTE_SAMPLE
    non_operational_with_on_date = (
        MEASURED["active_rows_with_on_date"]
        + MEASURED["withdrawn_rows_with_on_date"]
        + MEASURED["suspended_rows_with_on_date"]
    )
    assert non_operational_with_on_date == 420
    assert OUR_RUN2["cod_2018_2025"]["n"] < sum(
        PUBLISHER_IR_TO_COD[y][0] for y in range(2018, 2026)
    )
    # the publisher's medians sit in a plausible band around ours (months -> years); this is context, NOT a validation
    pub_years = [PUBLISHER_IR_TO_COD[y][1] / 12 for y in range(2018, 2026)]
    assert min(pub_years) < OUR_RUN2["cod_2018_2025"]["median_years"] < max(pub_years)
