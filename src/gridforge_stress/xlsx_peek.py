"""Read-only structural peek at an .xlsx held in memory: sheet names, dimensions, a few leading rows.

Stdlib only. The bytes are untrusted: bounded member sizes and compression ratio, no DOCTYPE/ENTITY, nothing is
written to disk, and only a handful of cells are returned. Used to learn a workbook's real layout before any
parser is written against it; it is NOT a data parser.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

MAX_TOTAL_UNCOMPRESSED = 400_000_000
MAX_MEMBER_UNCOMPRESSED = 300_000_000
MAX_RATIO = 400
MAX_ROWS_COUNTED = 3_000_000
CELL_CHARS = 80
_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_COL = re.compile(r"[A-Z]+")


class UnsafeWorkbook(ValueError):
    """The archive violates the safety bounds; nothing was parsed."""


def _check_archive(zf: zipfile.ZipFile) -> None:
    total = 0
    for info in zf.infolist():
        total += info.file_size
        if info.file_size > MAX_MEMBER_UNCOMPRESSED:
            raise UnsafeWorkbook(f"member {info.filename} too large uncompressed")
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise UnsafeWorkbook(f"member {info.filename} has suspicious compression ratio")
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise UnsafeWorkbook("archive too large uncompressed")


def _open_xml(zf: zipfile.ZipFile, name: str) -> Any:
    head = zf.open(name).read(4096).lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise UnsafeWorkbook(f"{name} declares a DTD/entity")
    return zf.open(name)


def _col_index(ref: str) -> int:
    letters = _COL.match(ref)
    n = 0
    for ch in letters.group(0) if letters else "A":
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile, needed: set[int]) -> dict[int, str]:
    if not needed or "xl/sharedStrings.xml" not in zf.namelist():
        return {}
    out: dict[int, str] = {}
    last = max(needed)
    idx = -1
    with _open_xml(zf, "xl/sharedStrings.xml") as fh:
        for _, el in ET.iterparse(fh, events=("end",)):  # noqa: S314
            if el.tag == f"{{{_NS['m']}}}si":
                idx += 1
                if idx in needed:
                    out[idx] = "".join(t.text or "" for t in el.iter(f"{{{_NS['m']}}}t"))
                el.clear()
                if idx >= last:
                    break
    return out


def _sheet_targets(zf: zipfile.ZipFile) -> list[tuple[str, str, str]]:
    with _open_xml(zf, "xl/workbook.xml") as fh:
        wb = ET.parse(fh).getroot()  # noqa: S314
    with _open_xml(zf, "xl/_rels/workbook.xml.rels") as fh:
        rels = ET.parse(fh).getroot()  # noqa: S314
    target = {r.get("Id", ""): r.get("Target", "") for r in rels.findall("pr:Relationship", _NS)}
    out = []
    for sh in wb.findall("m:sheets/m:sheet", _NS):
        rid = sh.get(f"{{{_NS['r']}}}id", "")
        path = target.get(rid, "")
        path = path.lstrip("/") if path.startswith("/") else "xl/" + path
        out.append((sh.get("name", ""), sh.get("state", "visible"), path))
    return out


def _peek_sheet(zf: zipfile.ZipFile, path: str, max_rows: int) -> dict[str, Any]:
    raw_rows: list[list[tuple[int, str, str]]] = []
    dimension = None
    row_count = 0
    with _open_xml(zf, path) as fh:
        for _, el in ET.iterparse(fh, events=("end",)):  # noqa: S314
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "dimension":
                dimension = el.get("ref")
            elif tag == "row":
                row_count += 1
                if len(raw_rows) < max_rows:
                    cells = []
                    for c in el.findall("m:c", _NS):
                        v = c.find("m:v", _NS)
                        inline = c.find("m:is", _NS)
                        text = (
                            "".join(t.text or "" for t in inline.iter(f"{{{_NS['m']}}}t"))
                            if inline is not None
                            else (v.text if v is not None else "")
                        )
                        cells.append((_col_index(c.get("r", "A1")), c.get("t", ""), text or ""))
                    raw_rows.append(cells)
                el.clear()
                if row_count >= MAX_ROWS_COUNTED:
                    break
    return {"dimension": dimension, "rows_counted": row_count, "raw_rows": raw_rows}


def peek(data: bytes, *, max_rows: int = 6) -> dict[str, Any]:
    if not data.startswith(b"PK"):
        raise UnsafeWorkbook("not a zip/xlsx container")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _check_archive(zf)
        sheets: list[dict[str, Any]] = []
        peeks: dict[str, dict[str, Any]] = {}
        needed: set[int] = set()
        for name, state, path in _sheet_targets(zf):
            if path not in zf.namelist():
                sheets.append({"name": name, "state": state, "error": "sheet part missing"})
                continue
            p = _peek_sheet(zf, path, max_rows)
            peeks[name] = p
            needed |= {
                int(t) for row in p["raw_rows"] for _, ty, t in row if ty == "s" and t.isdigit()
            }
        strings = _shared_strings(zf, needed)
        for name, state, _path in _sheet_targets(zf):
            if name not in peeks:
                continue
            p = peeks[name]
            rows = []
            for row in p["raw_rows"]:
                width = max((i for i, _, _ in row), default=-1) + 1
                vals = [""] * width
                for i, ty, t in row:
                    val = strings.get(int(t), "") if ty == "s" and t.isdigit() else t
                    vals[i] = val[:CELL_CHARS]
                rows.append(vals)
            sheets.append(
                {
                    "name": name,
                    "state": state,
                    "dimension": p["dimension"],
                    "rows_counted": p["rows_counted"],
                    "leading_rows": rows,
                }
            )
    return {"sheet_count": len(sheets), "sheets": sheets}
