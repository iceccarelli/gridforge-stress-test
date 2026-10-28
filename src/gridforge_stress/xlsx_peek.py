"""Read-only structural peek at an .xlsx held in memory: sheet names, dimensions, a few leading rows.

Stdlib only. The bytes are untrusted: bounded member sizes and compression ratio, no DOCTYPE/ENTITY, nothing is
written to disk, and only a handful of cells are returned. Used to learn a workbook's real layout before any
parser is written against it; it is NOT a data parser.
"""

from __future__ import annotations

import io
import math
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


_DOC_SHEET = re.compile(
    r"code ?book|read ?me|definition|dictionary|legend|contents|about|notes|glossary", re.I
)


def compact_report(result: dict[str, Any], *, detail_rows: int = 8, largest: int = 3) -> list[str]:
    """One short line per sheet, plus leading rows for the largest sheets and any documentation-like sheets."""
    sheets = result["sheets"]
    big = {
        s["name"]
        for s in sorted(sheets, key=lambda x: x.get("rows_counted", 0), reverse=True)[:largest]
    }
    lines = [f"sheet_count={result['sheet_count']}"]
    for i, sh in enumerate(sheets):
        lines.append(
            f"[{i}] {sh['name']!r} state={sh.get('state')} dim={sh.get('dimension')} "
            f"rows={sh.get('rows_counted')}"
        )
    for sh in sheets:
        if "leading_rows" not in sh or not (sh["name"] in big or _DOC_SHEET.search(sh["name"])):
            continue
        lines.append(f"--- leading rows of {sh['name']!r} ---")
        for row in sh["leading_rows"][:detail_rows]:
            cells = list(row)
            while cells and not cells[-1]:
                cells.pop()
            lines.append("  " + " | ".join(cells))
    return lines


# ---------------------------------------------------------------------------------------------------------------
# Value profile: bounded, in-memory statistics over named columns of one sheet. Answers "what values/vocabulary and
# data-quality patterns does the real data contain?" before a parser makes assumptions.
MAX_PROFILE_ROWS = 600_000
HEADER_SCAN_ROWS = 12


def _row_cells(row_el: Any) -> list[tuple[int, str, str]]:
    cells = []
    for c in row_el.findall("m:c", _NS):
        v = c.find("m:v", _NS)
        inline = c.find("m:is", _NS)
        text = (
            "".join(t.text or "" for t in inline.iter(f"{{{_NS['m']}}}t"))
            if inline is not None
            else (v.text if v is not None else "")
        )
        cells.append((_col_index(c.get("r", "A1")), c.get("t", ""), text or ""))
    return cells


def _sheet_path(zf: zipfile.ZipFile, sheet_name: str) -> str:
    for name, _state, path in _sheet_targets(zf):
        if name == sheet_name:
            if path not in zf.namelist():
                raise UnsafeWorkbook(f"sheet part for {sheet_name!r} missing")
            return path
    raise UnsafeWorkbook(f"no sheet named {sheet_name!r}")


def profile_sheet(
    data: bytes,
    sheet_name: str,
    *,
    header_marker: str,
    columns: tuple[str, ...],
    max_distinct: int = 50,
    blank_checks: tuple[tuple[str, str, str], ...] = (),
    order_checks: tuple[tuple[str, str], ...] = (),
    dup_key: tuple[str, ...] = (),
    dup_exclude: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Profile named columns. `blank_checks`: (when_col, when_value, blank_col) counts rows where the first equals the
    value and the last is blank. `order_checks`: (a, b) counts rows where both are numeric and a < b.
    `dup_key`: columns forming a key; rows whose `dup_exclude` column equals the excluded literal are skipped."""
    if not data.startswith(b"PK"):
        raise UnsafeWorkbook("not a zip/xlsx container")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _check_archive(zf)
        path = _sheet_path(zf, sheet_name)
        raw_rows: list[list[tuple[int, str, str]]] = []
        with _open_xml(zf, path) as fh:
            for _, el in ET.iterparse(fh, events=("end",)):  # noqa: S314
                if el.tag == f"{{{_NS['m']}}}row":
                    raw_rows.append(_row_cells(el))
                    el.clear()
                    if len(raw_rows) >= MAX_PROFILE_ROWS:
                        break
        needed = {int(t) for row in raw_rows for _, ty, t in row if ty == "s" and t.isdigit()}
        strings = _shared_strings(zf, needed)

    def val(cell: tuple[int, str, str]) -> str:
        _, ty, t = cell
        return strings.get(int(t), "") if ty == "s" and t.isdigit() else t

    header_at = next(
        (
            i
            for i, row in enumerate(raw_rows[:HEADER_SCAN_ROWS])
            if any(val(c) == header_marker for c in row)
        ),
        None,
    )
    if header_at is None:
        raise UnsafeWorkbook(
            f"header marker {header_marker!r} not found in first {HEADER_SCAN_ROWS} rows"
        )
    names = {c[0]: val(c) for c in raw_rows[header_at]}
    index = {name: i for i, name in names.items()}
    wanted = set(columns) | {x for c in blank_checks for x in (c[0], c[2])}
    wanted |= {x for c in order_checks for x in c} | set(dup_key) | set(dup_exclude or {})
    missing = sorted(wanted - set(index))
    if missing:
        raise UnsafeWorkbook(f"columns not in header: {missing}")
    body = raw_rows[header_at + 1 :]
    table: list[dict[str, str]] = []
    for row in body:
        by_col = {c[0]: val(c) for c in row}
        table.append({name: by_col.get(index[name], "") for name in wanted})

    def num(x: str) -> float | None:
        try:
            f = float(x)
        except ValueError:
            return None
        return f if math.isfinite(f) else None

    out_cols: dict[str, Any] = {}
    for col in columns:
        vals = [r[col] for r in table]
        nb = [v for v in vals if v.strip()]
        counts: dict[str, int] = {}
        for v in nb:
            counts[v] = counts.get(v, 0) + 1
        nums = [n for n in (num(v) for v in nb) if n is not None]
        entry: dict[str, Any] = {
            "non_blank": len(nb),
            "blank": len(vals) - len(nb),
            "distinct_total": len(counts),
            "top_values": dict(
                sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_distinct]
            ),
        }
        if nb and len(nums) == len(nb):
            entry["numeric_min"], entry["numeric_max"] = min(nums), max(nums)
        out_cols[col] = entry
    checks = []
    for when, value, blank in blank_checks:
        hit = [r for r in table if r[when] == value]
        checks.append(
            {
                "when": f"{when}={value!r}",
                "rows": len(hit),
                "blank": blank,
                "rows_with_blank": sum(1 for r in hit if not r[blank].strip()),
            }
        )
    orders = []
    for a, b in order_checks:
        both = [(num(r[a]), num(r[b])) for r in table]
        pairs = [(x, y) for x, y in both if x is not None and y is not None]
        orders.append(
            {
                "check": f"{a} < {b}",
                "rows_with_both": len(pairs),
                "rows_violating": sum(1 for x, y in pairs if x < y),
            }
        )
    dups: dict[str, Any] = {}
    if dup_key:
        excl = dup_exclude or {}
        keys: dict[tuple[str, ...], int] = {}
        skipped = 0
        for r in table:
            if any(r[c] == lit for c, lit in excl.items()):
                skipped += 1
                continue
            k = tuple(r[c] for c in dup_key)
            keys[k] = keys.get(k, 0) + 1
        dups = {
            "key": list(dup_key),
            "excluded_rows": skipped,
            "distinct_keys": len(keys),
            "keys_repeated": sum(1 for n in keys.values() if n > 1),
            "rows_in_repeated_keys": sum(n for n in keys.values() if n > 1),
        }
    return {
        "sheet": sheet_name,
        "header_row_index": header_at,
        "data_rows": len(table),
        "columns": out_cols,
        "blank_checks": checks,
        "order_checks": orders,
        "duplicate_key": dups,
    }


def profile_lines(result: dict[str, Any]) -> list[str]:
    lines = [
        f"profile of {result['sheet']!r}: data_rows={result['data_rows']} header_row_index={result['header_row_index']}"
    ]
    for name, c in result["columns"].items():
        top = ", ".join(f"{k!r}:{v}" for k, v in list(c["top_values"].items())[:12])
        rng = f" min={c['numeric_min']} max={c['numeric_max']}" if "numeric_min" in c else ""
        lines.append(
            f"  {name}: non_blank={c['non_blank']} blank={c['blank']} distinct={c['distinct_total']}{rng} top=[{top}]"
        )
    for b in result["blank_checks"]:
        lines.append(
            f"  blank_check when {b['when']}: rows={b['rows']} with blank {b['blank']}={b['rows_with_blank']}"
        )
    for o in result["order_checks"]:
        lines.append(
            f"  order_check {o['check']}: rows_with_both={o['rows_with_both']} violating={o['rows_violating']}"
        )
    if result["duplicate_key"]:
        d = result["duplicate_key"]
        lines.append(
            f"  duplicate_key {d['key']}: distinct={d['distinct_keys']} repeated={d['keys_repeated']} rows_in_repeated={d['rows_in_repeated_keys']} excluded={d['excluded_rows']}"
        )
    return lines
