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
