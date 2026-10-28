from __future__ import annotations

import io
import zipfile

import pytest

from gridforge_stress import fetch as F
from gridforge_stress import xlsx_peek as X
from gridforge_stress.registry import load_sources

pytestmark = pytest.mark.unit
M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def make_xlsx(
    extra: dict[str, bytes] | None = None, strings=("id", "status", "MW"), rows=3
) -> bytes:
    """A tiny SYNTHETIC workbook written by hand; no source data."""
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
    body = "".join(
        f'<row r="{i + 2}"><c r="A{i + 2}" t="inlineStr"><is><t>P{i}</t></is></c><c r="C{i + 2}"><v>{10 * i}</v></c></row>'
        for i in range(rows)
    )
    sheet = (
        f'<worksheet xmlns="{M}"><dimension ref="A1:C{rows + 1}"/><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
        f"{body}</sheetData></worksheet>"
    )
    parts = {
        "xl/workbook.xml": f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/>'
        '<sheet name="Codebook" sheetId="2" state="hidden" r:id="rId2"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{M}">{sst}</sst>',
        "xl/worksheets/sheet1.xml": sheet,
        "xl/worksheets/sheet2.xml": f'<worksheet xmlns="{M}"><sheetData/></worksheet>',
    }
    parts.update({k: v for k, v in (extra or {}).items()})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for k, v in parts.items():
            zf.writestr(k, v)
    return buf.getvalue()


def test_peek_reports_sheet_names_states_dimensions_and_leading_rows():
    out = X.peek(make_xlsx(rows=3), max_rows=2)
    assert out["sheet_count"] == 2
    data = out["sheets"][0]
    assert data["name"] == "Data" and data["dimension"] == "A1:C4" and data["rows_counted"] == 4
    assert data["leading_rows"][0] == ["id", "status", "MW"]
    assert data["leading_rows"][1] == ["P0", "", "0"] and len(data["leading_rows"]) == 2
    assert out["sheets"][1]["name"] == "Codebook" and out["sheets"][1]["state"] == "hidden"


def test_peek_truncates_long_cells():
    out = X.peek(make_xlsx(strings=("x" * 500, "b", "c")))
    assert len(out["sheets"][0]["leading_rows"][0][0]) == X.CELL_CHARS


@pytest.mark.parametrize(
    "blob",
    [b"not a zip", b"", b"PK\x03\x04garbage"],
)
def test_non_workbook_input_is_rejected(blob):
    with pytest.raises((X.UnsafeWorkbook, zipfile.BadZipFile)):
        X.peek(blob)


def test_entity_and_doctype_payloads_are_refused():
    evil = (
        f'<!DOCTYPE x [<!ENTITY a "aaaa">]><worksheet xmlns="{M}"><sheetData/></worksheet>'.encode()
    )
    with pytest.raises(X.UnsafeWorkbook, match="DTD"):
        X.peek(make_xlsx({"xl/worksheets/sheet1.xml": evil}))


def test_zip_bomb_ratio_is_refused():
    bomb = b"0" * 5_000_000
    with pytest.raises(X.UnsafeWorkbook, match="ratio"):
        X.peek(make_xlsx({"xl/media/bomb.bin": bomb}))


def test_cli_probe_peek_prints_workbook_layout_for_registered_xlsx_only(monkeypatch, capsys):
    from gridforge_stress import cli

    blob = make_xlsx()
    seen = []

    def fake_probe(sid, url=None, allow_large=False, **kw):
        seen.append((url, allow_large))
        man = F.Manifest(  # type: ignore[attr-defined]
            source_id=sid,
            source_url=url or "",
            origin="live_fetch",
            retrieval_status="fetched_unparsed",
            attribution="a",
            byte_count=len(blob),
        )
        return blob, man, {"content-type": "application/octet-stream"}

    monkeypatch.setattr(F, "probe_source", fake_probe)
    assert cli.main(["probe", "lbnl-queued-up", "--peek-xlsx"]) == 0
    out = capsys.readouterr().out
    assert "'Codebook' state=hidden" in out and "leading rows of 'Data'" in out
    assert "id | status | MW" in out
    assert seen and all(u.endswith(".xlsx") and big for u, big in seen)


def test_allow_large_uses_the_registry_cap_not_the_probe_cap():
    reg = load_sources()
    cap = reg["lbnl-queued-up"].max_bytes
    got = {}

    def spy(ip, host, target, headers, max_bytes, deadline):
        got["cap"] = max_bytes
        return F.Response(200, {"content-type": "x"}, b"PK")

    url = reg["lbnl-queued-up"].probe_urls[-1]
    F.probe_source(
        "lbnl-queued-up",
        url=url,
        allow_large=True,
        resolver=lambda h: ["93.184.216.34"],
        transport=spy,
    )
    assert got["cap"] == cap > F.PROBE_MAX_BYTES
    F.probe_source("lbnl-queued-up", url=url, resolver=lambda h: ["93.184.216.34"], transport=spy)
    assert got["cap"] == F.PROBE_MAX_BYTES


def test_compact_report_is_one_line_per_sheet_and_shows_doc_sheets_and_largest():
    lines = X.compact_report(X.peek(make_xlsx(rows=3), max_rows=3), detail_rows=2, largest=1)
    assert lines[0] == "sheet_count=2"
    assert lines[1].startswith("[0] 'Data'") and "rows=4" in lines[1]
    assert any("leading rows of 'Data'" in x for x in lines)
    assert any("leading rows of 'Codebook'" in x for x in lines), (
        "codebook-like names are always detailed"
    )
    assert not any("," in x and "[" in x and x.startswith("  [") for x in lines)


# --- value profile ---------------------------------------------------------------------------------------------


def make_data_workbook(rows: list[list[str]], header=None, sheet_name: str = "Data") -> bytes:
    """SYNTHETIC workbook: row1 'RETURN TO CONTENTS', row2 header, then `rows` (strings; numeric-looking => numbers)."""
    header = header or ["q_id", "q_status", "q_date", "wd_date", "on_date", "entity", "mw_1"]
    strings: list[str] = []

    def sid(t: str) -> int:
        if t not in strings:
            strings.append(t)
        return strings.index(t)

    def col(i: int) -> str:
        return "ABCDEFGHIJ"[i]

    def cell(r: int, i: int, v: str) -> str:
        if v == "":
            return ""
        try:
            float(v)
            return f'<c r="{col(i)}{r}"><v>{v}</v></c>'
        except ValueError:
            return f'<c r="{col(i)}{r}" t="s"><v>{sid(v)}</v></c>'

    xml_rows = [f'<row r="1"><c r="A1" t="s"><v>{sid("RETURN TO CONTENTS")}</v></c></row>']
    for n, row in enumerate([header, *rows], start=2):
        xml_rows.append(
            f'<row r="{n}">' + "".join(cell(n, i, v) for i, v in enumerate(row)) + "</row>"
        )
    sheet = f'<worksheet xmlns="{M}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
    parts = {
        "xl/workbook.xml": f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{M}">{sst}</sst>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for k, v in parts.items():
            zf.writestr(k, v)
    return buf.getvalue()


DATA = [
    # q_id, q_status, q_date, wd_date, on_date, entity, mw_1
    ["Q1", "withdrawn", "40000", "", "", "APS", "10"],
    ["Q2", "withdrawn", "40100", "40200", "", "APS", "20"],
    ["Q3", "operational", "40000", "", "41000", "APS", "30"],
    ["Q4", "operational", "40000", "", "", "PGE", "40"],
    ["Q5", "active", "42000", "", "", "PGE", "50"],
    ["Q1", "active", "42000", "", "", "PGE", "60"],  # same id, other entity: NOT a duplicate
    [
        "Q3",
        "operational",
        "40000",
        "",
        "39000",
        "APS",
        "70",
    ],  # duplicate (APS,Q3) AND on_date < q_date
    [
        "not assigned",
        "withdrawn",
        "40500",
        "40400",
        "",
        "APS",
        "80",
    ],  # wd < q_date; excluded from dup check
    ["not assigned", "withdrawn", "40500", "", "", "APS", "5"],
]


def prof(**kw):
    return X.profile_sheet(
        make_data_workbook(DATA),
        "Data",
        header_marker="q_id",
        columns=("q_status", "wd_date", "mw_1", "entity"),
        blank_checks=(("q_status", "withdrawn", "wd_date"), ("q_status", "operational", "on_date")),
        order_checks=(("wd_date", "q_date"), ("on_date", "q_date")),
        dup_key=("entity", "q_id"),
        dup_exclude={"q_id": "not assigned"},
        **kw,
    )


def test_profile_finds_header_after_the_banner_row_and_counts_rows():
    p = prof()
    assert p["header_row_index"] == 1 and p["data_rows"] == len(DATA)


def test_profile_value_frequencies_blanks_and_numeric_ranges_match_independent_counts():
    cols = prof()["columns"]
    assert cols["q_status"]["top_values"] == {"withdrawn": 4, "operational": 3, "active": 2}
    assert cols["q_status"]["distinct_total"] == 3 and cols["q_status"]["blank"] == 0
    assert cols["wd_date"]["non_blank"] == 2 and cols["wd_date"]["blank"] == 7
    assert (cols["mw_1"]["numeric_min"], cols["mw_1"]["numeric_max"]) == (5.0, 80.0)
    assert "numeric_min" not in cols["q_status"]


def test_profile_blank_order_and_duplicate_checks():
    p = prof()
    wd, op = p["blank_checks"]
    assert (wd["rows"], wd["rows_with_blank"]) == (4, 2)  # Q1 and one 'not assigned' lack wd_date
    assert (op["rows"], op["rows_with_blank"]) == (3, 1)  # only Q4 lacks on_date
    o_wd, o_on = p["order_checks"]
    assert (o_wd["rows_with_both"], o_wd["rows_violating"]) == (2, 1)
    assert (o_on["rows_with_both"], o_on["rows_violating"]) == (2, 1)
    d = p["duplicate_key"]
    assert d["excluded_rows"] == 2 and d["keys_repeated"] == 1 and d["rows_in_repeated_keys"] == 2
    # (APS,Q1) (APS,Q2) (APS,Q3) (PGE,Q4) (PGE,Q5) (PGE,Q1); (APS,Q3) repeats
    assert d["distinct_keys"] == 6


def test_profile_caps_listed_distinct_values_but_reports_the_true_total():
    rows = [[f"Q{i}", f"s{i}", "40000", "", "", "E", "1"] for i in range(80)]
    p = X.profile_sheet(
        make_data_workbook(rows),
        "Data",
        header_marker="q_id",
        columns=("q_status",),
        max_distinct=10,
    )
    c = p["columns"]["q_status"]
    assert c["distinct_total"] == 80 and len(c["top_values"]) == 10


def test_profile_rejects_missing_sheet_marker_and_columns():
    wb = make_data_workbook(DATA)
    with pytest.raises(X.UnsafeWorkbook, match="no sheet"):
        X.profile_sheet(wb, "Nope", header_marker="q_id", columns=("q_status",))
    with pytest.raises(X.UnsafeWorkbook, match="header marker"):
        X.profile_sheet(wb, "Data", header_marker="zzz", columns=("q_status",))
    with pytest.raises(X.UnsafeWorkbook, match="columns not in header"):
        X.profile_sheet(wb, "Data", header_marker="q_id", columns=("nope",))


def test_profile_lines_are_compact_and_cli_profile_wires_through(monkeypatch, capsys):
    from gridforge_stress import cli

    wb = make_data_workbook(
        DATA, header=["q_id", "q_status", "q_date", "wd_date", "on_date", "entity", "mw_1"]
    )
    lines = X.profile_lines(prof())
    assert lines[0].startswith("profile of 'Data'") and all("\n" not in x for x in lines)

    def fake_probe(sid, url=None, allow_large=False, **kw):
        man = F.Manifest(
            source_id=sid,
            source_url=url or "",
            origin="live_fetch",
            retrieval_status="fetched_unparsed",
            attribution="a",
            byte_count=len(wb),
        )
        return wb, man, {"content-type": "x"}

    monkeypatch.setattr(F, "probe_source", fake_probe)
    import gridforge_stress.queue as Q

    monkeypatch.setattr(
        Q,
        "LBNL_2026_PROFILE",
        {
            "sheet_name": "Data",
            "header_marker": "q_id",
            "columns": ("q_status",),
            "blank_checks": (),
            "order_checks": (),
            "dup_key": (),
            "dup_exclude": None,
        },
    )
    assert cli.main(["probe", "lbnl-queued-up", "--profile"]) == 0
    out = capsys.readouterr().out
    assert "profile of 'Data'" in out and "'withdrawn':4" in out


def test_profile_numeric_zero_and_negative_counts():
    rows = [
        ["Q1", "a", "40000", "", "", "E", "-75"],
        ["Q2", "a", "40000", "", "", "E", "0"],
        ["Q3", "a", "40000", "", "", "E", "5"],
    ]
    c = X.profile_sheet(make_data_workbook(rows), "Data", header_marker="q_id", columns=("mw_1",))[
        "columns"
    ]["mw_1"]
    assert (c["numeric_min"], c["numeric_zero"], c["numeric_negative"]) == (-75.0, 1, 1)


def test_profile_tells_identical_copies_from_differing_ones_and_names_the_columns():
    rows = [
        ["Q1", "a", "40000", "", "", "E", "10"],
        ["Q1", "a", "40000", "", "", "E", "10"],  # identical copy of the row above
        ["Q2", "a", "40000", "", "", "E", "10"],
        ["Q2", "a", "40000", "", "", "E", "99"],  # same key, different capacity
        ["Q3", "a", "40000", "", "", "E", "1"],
    ]
    d = X.profile_sheet(
        make_data_workbook(rows),
        "Data",
        header_marker="q_id",
        columns=("q_status",),
        dup_key=("entity", "q_id"),
    )["duplicate_key"]
    assert d["keys_repeated"] == 2 and d["rows_in_repeated_keys"] == 4
    assert d["repeated_keys_fully_identical"] == 1 and d["repeated_keys_with_differences"] == 1
    assert d["differing_columns_top"] == {"mw_1": 1}


def test_profile_duplicate_content_on_the_main_synthetic_data():
    d = prof()["duplicate_key"]
    assert d["repeated_keys_fully_identical"] == 0 and d["repeated_keys_with_differences"] == 1
    assert d["differing_columns_top"] == {"mw_1": 1, "on_date": 1}


def test_profile_measures_capacity_repeated_across_rows_of_one_key():
    rows = [
        ["Q1", "a", "40000", "", "", "E", "10"],
        [
            "Q1",
            "a",
            "40000",
            "",
            "",
            "E",
            "10",
        ],  # same key, same capacity (would be double counted if summed)
        ["Q2", "a", "40000", "", "", "E", "10"],
        ["Q2", "a", "40000", "", "", "E", "99"],  # same key, different capacity
        ["Q3", "a", "40000", "", "", "E", "1"],
    ]
    d = X.profile_sheet(
        make_data_workbook(rows),
        "Data",
        header_marker="q_id",
        columns=("q_status",),
        dup_key=("entity", "q_id"),
        dup_value_col="mw_1",
    )["duplicate_key"]
    assert (d["repeated_keys_value_identical"], d["repeated_keys_value_differing"]) == (1, 1)
    assert d["value_sum_all_rows_in_repeated_keys"] == 10 + 10 + 10 + 99  # 129
    assert d["value_sum_on_non_first_rows"] == 10 + 99  # 109
    line = [
        x
        for x in X.profile_lines(
            X.profile_sheet(
                make_data_workbook(rows),
                "Data",
                header_marker="q_id",
                columns=("q_status",),
                dup_key=("entity", "q_id"),
                dup_value_col="mw_1",
            )
        )
        if "keys_identical" in x
    ]
    assert line and "sum_on_non_first_rows=109" in line[0]


LBNL_HEADER = [
    "q_id",
    "q_status",
    "q_date",
    "wd_date",
    "on_date",
    "entity",
    "mw_1",
    "type_clean",
    "region",
]


def lbnl_like_workbook() -> bytes:
    rows = [
        [
            "Q1",
            "operational",
            "42005",
            "",
            "43831",
            "APS",
            "10",
            "Solar",
            "West",
        ],  # 2015-01-01 -> 2020-01-01
        [
            "Q2",
            "operational",
            "42005",
            "",
            "44927",
            "APS",
            "10",
            "Wind",
            "West",
        ],  # 2015-01-01 -> 2023-01-01
        ["Q3", "withdrawn", "42005", "42370", "", "APS", "10", "Solar", "West"],
    ]
    return make_data_workbook(rows, header=LBNL_HEADER, sheet_name="03. Complete Queue Data")


def test_sheet_rows_returns_dicts_keyed_by_header_after_the_banner():
    rows = X.sheet_rows(lbnl_like_workbook(), "03. Complete Queue Data", header_marker="q_id")
    assert len(rows) == 3 and rows[0]["q_id"] == "Q1" and rows[2]["q_status"] == "withdrawn"
    assert rows[0]["on_date"] == "43831" and rows[2]["on_date"] == ""
    with pytest.raises(X.UnsafeWorkbook):
        X.sheet_rows(lbnl_like_workbook(), "Nope", header_marker="q_id")


def test_show_sheet_lines_prints_named_sheet_only_and_rejects_unknown():
    lines = X.show_sheet_lines(lbnl_like_workbook(), "03. Complete Queue Data")
    assert lines[0].startswith("--- '03. Complete Queue Data'") and any(
        "q_id | q_status" in x for x in lines
    )
    with pytest.raises(X.UnsafeWorkbook):
        X.show_sheet_lines(lbnl_like_workbook(), "missing")


def test_cli_queue_stats_computes_durations_from_the_workbook_in_memory(monkeypatch, capsys):
    import json as _json

    from gridforge_stress import cli

    wb = lbnl_like_workbook()

    def fake_probe(sid, url=None, allow_large=False, **kw):
        man = F.Manifest(
            source_id=sid,
            source_url=url or "",
            origin="live_fetch",
            retrieval_status="fetched_unparsed",
            attribution="a",
            byte_count=len(wb),
        )
        return wb, man, {"content-type": "x"}

    monkeypatch.setattr(F, "probe_source", fake_probe)
    assert cli.main(["probe", "lbnl-queued-up", "--queue-stats"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("parsed rows=3 kept=3 rejected=0")
    overall = _json.loads(out[2])
    assert overall["overall"]["n"] == 2 and overall["by_region"]["West"]["n"] == 2
    expected = sorted(
        [(5 * 365 + 1) / 365.25, (8 * 365 + 2) / 365.25]
    )  # 2015-01-01 -> 2020-01-01 and -> 2023-01-01
    assert overall["overall"]["median_years"] == pytest.approx(sum(expected) / 2, abs=1e-3)
    cap = _json.loads(out[4])["capacity_summary"]
    assert (
        cap["naive_row_sum"] == cap["deduplicated_sum"] == 30.0
        and cap["double_counting_avoided_mw"] == 0.0
    )
