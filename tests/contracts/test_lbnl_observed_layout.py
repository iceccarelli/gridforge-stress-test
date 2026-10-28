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


def test_status_vocabulary_is_exactly_the_one_the_codebook_states():
    stated = {"active", "withdrawn", "suspended", "operational"}
    assert "One of: active, withdrawn, suspended, or operational" in CODEBOOK_SEEN["q_status"]
    assert set(LBNL_2026_MAP.status_map) == stated
    assert set(LBNL_2026_MAP.status_map.values()) == stated


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
