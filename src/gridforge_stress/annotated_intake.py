"""Pure judges for annotated whole-number figures in an intake: `{"value": 180, "evidence": "E3", "note": "..."}` in place of `180`.

The product's intake loader reads this object form on its integer fields (hall.build_year, hall.rack_positions, hall.positions_available,
lv.phases, lv.busway_runs). Its own documentation of the form: the number is inside `value`; `evidence` is a class E0..E7 (E6 and E7 are earned
through the calibration ledger and are capped at E5 for a client's own attestation); `note` and `source` are shown beside the figure. These
judges take answers the real handlers already produced and say whether the annotated form behaved like the plain number it wraps. They call
nothing and compute no capacity.
"""

from __future__ import annotations

import re
from typing import Any

from .assumed_fields import (
    added_open_paths,
    class_ranks,
    first_difference,
    open_paths_violations,
    without_open_declared,
    without_readiness,
)

#: The highest class a client's own attestation can earn (the product's own text: "E6 and E7 are earned through the calibration ledger").
CLIENT_CEILING = 5
#: Keys whose lists are prose about the intake, not engineering numbers: judged separately, because a declaration may legitimately ADD a line.
PROSE_KEYS = ("warnings", "caveats")
#: Per-request fingerprints: they differ whenever any byte of the request differs, including the annotation itself.
FINGERPRINT_KEYS = ("request_digest", "provenance")

_CLASS = re.compile(r"^[Ee]([0-7])(?:_|$)")


def declared_rank(evidence: Any) -> int | None:
    """0..7 for a class the product accepts ('E3', 'e3', 'E3_ENGINEERING_ESTIMATE'); None for anything else (including a missing class)."""
    if isinstance(evidence, str) and (m := _CLASS.match(evidence)):
        return int(m.group(1))
    return None


def expected_recorded_rank(evidence: Any) -> int:
    """The class a figure declared with `evidence` may be RECORDED under: the declared one, never above the client ceiling; a figure with no
    class is recorded no higher than the ceiling either (a client's own data)."""
    rank = declared_rank(evidence)
    return CLIENT_CEILING if rank is None else min(rank, CLIENT_CEILING)


def _drop(node: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(node, dict):
        return {k: _drop(v, keys) for k, v in node.items() if k not in keys}
    if isinstance(node, list):
        return [_drop(v, keys) for v in node]
    return node


def prose(node: Any) -> set[str]:
    """Every line under a `warnings` / `caveats` key anywhere in the result."""
    out: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in PROSE_KEYS and isinstance(v, list):
                out.update(x for x in v if isinstance(x, str))
            else:
                out |= prose(v)
    elif isinstance(node, list):
        for v in node:
            out |= prose(v)
    return out


def engine_view(result: Any) -> Any:
    """The answer without per-request fingerprints and without the prose lists: what is left is every number and every label."""
    return _drop(result, (*PROSE_KEYS, *FINGERPRINT_KEYS))


def parity_violations(
    label: str,
    plain: dict[str, Any],
    annotated: dict[str, Any],
    path: str,
    e0_paths: tuple[str, ...] = (),
) -> list[str]:
    """`plain` and `annotated` are batch rows ({status: ok|clean|fault, result?, message?}) for the same hall with figure `path` given as a bare
    number and as an annotated object with the SAME number. Empty means the annotation changed nothing it should not.

    EVALUATES: neither is a server fault; the plain figure is answered exactly when the annotated one is (a refusal for one and an answer for the
    other is a difference in the NUMBER's treatment); when both are answered, every number and label is identical, and the annotated answer's
    prose keeps every line the plain one has and adds only lines that name `path`. DOES NOT EVALUATE the answer itself.

    `e0_paths` names the intake paths this annotated answer declared at class E0. A REQUIRED figure declared an assumption is open (the product's rule:
    it is "a template's value and not this hall's"), so for each such path for which the answer lists a gap row the plain one did not, exactly that
    row and the intake's readiness fields (`can_issue_a_study`, `recommended_engagement`) may differ, and only as `assumed_fields.open_paths_violations`
    states them: one required row that says it is an assumption, the Study not issuable, the sentence saying so. Every other figure, every other
    class and every other key is held to numeric identity as before; a figure that is not required gets no allowance.
    """
    out: list[str] = []
    for name, row in (("plain", plain), ("annotated", annotated)):
        if row["status"] == "fault":
            out.append(
                f"{label}: the {name} request escaped as a server fault: {row.get('error')}: {row.get('message')}"
            )
    if out:
        return out
    if plain["status"] != annotated["status"]:
        refused = plain if plain["status"] == "clean" else annotated
        return [
            f"{label}: plain is {plain['status']!r} but annotated is {annotated['status']!r} "
            f"(refusal: {str(refused.get('message'))[:140]!r})"
        ]
    if plain["status"] != "ok":
        return out
    left, right = plain["result"], annotated["result"]
    if added := added_open_paths(set(e0_paths), left, right):
        out += [f"{label}: {v}" for v in open_paths_violations(added, left, right)]
        left, right = without_readiness(left), without_open_declared(left, right, added)
    if (d := first_difference(engine_view(left), engine_view(right))) is not None:
        out.append(f"{label}: the annotated figure changed the answer: {d}")
    before, after = prose(plain["result"]), prose(annotated["result"])
    for line in sorted(before - after):
        out.append(f"{label}: the annotated answer lost a line the plain one has: {line[:100]!r}")
    for line in sorted(after - before):
        if path not in line:
            out.append(
                f"{label}: the annotated answer added a line that does not name {path}: {line[:100]!r}"
            )
    return out


# --- the study document's table of figures supplied with a source ----------------------------------------------------------------------
_ROW = re.compile(
    r"^\|\s*(?P<label>.+?)\s*\|\s*(?P<value>.+?)\s*\|\s*(?P<evidence>E[0-7])\s*\|\s*(?P<source>.*?)\s*\|\s*(?P<note>.*?)\s*\|$"
)
_NUMBER = re.compile(r"^\s*(-?\d+(?:\.\d+)?)")


def source_rows(document: str) -> list[dict[str, Any]]:
    """The rows of 'Figures supplied with a source': label, number, class rank, source, note. Rows are read, not interpreted."""
    start = document.find("Figures supplied with a source")
    if start < 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in document[start:].splitlines()[1:]:
        if line.startswith("## "):
            break
        m = _ROW.match(line)
        if m is None:
            continue
        n = _NUMBER.match(m["value"])
        rows.append(
            {
                "label": m["label"],
                "number": float(n.group(1)) if n else None,
                "rank": int(m["evidence"][1]),
                "source": m["source"],
                "note": m["note"],
            }
        )
    return rows


def recorded_class_violations(
    label: str, declared: Any, number: float, plain_doc: str, annotated_doc: str
) -> list[str]:
    """The study documents for the same hall with a figure plain and annotated (value `number`, class `declared`, a note). Empty means the
    record of the annotated figure never claims more than the client may.

    EVALUATES: exactly one row is added to the table of supplied figures; it carries the figure's number; its class is no higher than the
    client ceiling (E5) and no higher than the class declared; it IS the declared class (capped at E5) when one was declared, and no
    higher than E5 when none was. DOES NOT EVALUATE a figure declared with neither note nor source: the loader records those only when one of
    the two is given, and a declaration with no trace in the document is outside this judge.
    """
    before = source_rows(plain_doc)
    added = [r for r in source_rows(annotated_doc) if r not in before]
    gone = [r for r in before if r not in source_rows(annotated_doc)]
    out: list[str] = []
    if gone:
        out.append(f"{label}: the annotation removed a recorded figure: {gone[0]['label']}")
    if len(added) != 1:
        return [*out, f"{label}: expected one recorded figure to be added, found {len(added)}"]
    row = added[0]
    if row["number"] != number:
        out.append(f"{label}: recorded {row['number']!r}, the request gave {number!r}")
    cap = expected_recorded_rank(declared)
    if row["rank"] > cap:
        out.append(
            f"{label}: recorded as E{row['rank']}, above the E{cap} the declaration {declared!r} allows"
        )
    elif declared_rank(declared) is not None and row["rank"] != cap:
        out.append(f"{label}: declared {declared!r} but recorded as E{row['rank']}, not E{cap}")
    return out


# --- refusals -----------------------------------------------------------------------------------------------------------------------------
def refusal_violations(label: str, row: dict[str, Any], path: str, bad: Any) -> list[str]:
    """`row` is the answer to an annotated figure whose class is `bad`, which is not a class. Empty means it was refused, in the caller's terms,
    naming the field and quoting the value (with or without Python quotes)."""
    if row["status"] == "ok":
        return [f"{label}: evidence {bad!r} on {path} was ACCEPTED"]
    if row["status"] != "clean":
        return [
            f"{label}: evidence {bad!r} on {path} escaped as a server fault: {row.get('error')}: {row.get('message')}"
        ]
    message = str(row.get("message", ""))
    out: list[str] = []
    if path.split(".")[-1] not in message:
        out.append(f"{label}: the refusal does not name {path.split('.')[-1]}: {message!r}")
    if str(bad) not in message and repr(bad) not in message:
        out.append(f"{label}: the refusal does not quote {bad!r}: {message!r}")
    return out


# --- zero occupied positions ------------------------------------------------------------------------------------------------------------
def disclosures(result: Any) -> set[str]:
    """Every text in the result that says occupied or existing load is not subtracted."""
    found: set[str] = set()
    if isinstance(result, dict):
        for v in result.values():
            found |= disclosures(v)
    elif isinstance(result, list):
        for v in result:
            found |= disclosures(v)
    elif isinstance(result, str) and re.search(r"not subtracted", result, re.IGNORECASE):
        found.add(result)
    return found


def zero_occupied_violations(zero: dict[str, Any], occupied: dict[str, Any]) -> list[str]:
    """`zero` answers a hall whose rack_positions equals positions_available (nothing occupied); `occupied` the same hall with a larger
    rack_positions (some occupied), figures otherwise identical, both on the same route. Empty means the empty hall is not made to look better
    evidenced than the occupied one.

    EVALUATES: (1) every capacity number and label (the answer minus prose, and minus the text that merely restates the position counts) is
    identical, because the free positions are the same; (2) no stated evidence class is higher for the empty hall; (3) every disclosure that
    occupied or existing load is not subtracted, present for the occupied hall, is present for the empty one; (4) every warning for the occupied
    hall that is not about occupied positions is present for the empty one; (5) `intake` completeness is equal.
    DOES NOT EVALUATE whether the empty hall should carry a warning of its own (see the module docstring of the oracle: an open question).
    """
    out: list[str] = []
    a, b = _mask_position_counts(engine_view(zero)), _mask_position_counts(engine_view(occupied))
    if (d := first_difference(a, b)) is not None:
        out.append(f"the free positions are the same but the answer differs: {d}")
    ranks_zero, ranks_occupied = class_ranks(zero), class_ranks(occupied)
    for where in sorted(set(ranks_zero) & set(ranks_occupied)):
        if any(x > y for x, y in zip(ranks_zero[where], ranks_occupied[where], strict=False)):
            out.append(
                f"{where}: the empty hall states a higher class {ranks_zero[where]} than the occupied one {ranks_occupied[where]}"
            )
    lost = sorted(
        x for x in disclosures(occupied) - disclosures(zero) if "occupied positions" not in x
    )
    out += [f"the empty hall lost a disclosure the occupied hall has: {x[:110]!r}" for x in lost]
    for x in sorted(prose(occupied) - prose(zero)):
        if "occupied position" not in x and "not subtracted" not in x:
            out.append(f"the empty hall lost a warning the occupied hall has: {x[:110]!r}")
    za, zb = _intake(zero), _intake(occupied)
    if za is not None and zb is not None and za != zb:
        out.append(f"intake completeness differs: {za!r} (empty) vs {zb!r} (occupied)")
    return out


def _intake(result: Any) -> Any:
    if isinstance(result, dict):
        block = result.get("intake")
        if isinstance(block, dict) and "completeness" in block:
            return block["completeness"]
        return result.get("intake_completeness")
    return None


_COUNTS = re.compile(r"\b\d+ of \d+ positions available\b")


def _mask_position_counts(node: Any) -> Any:
    """The one sentence that legitimately differs between the two halls is the restatement '180 of 560 positions available'."""
    if isinstance(node, dict):
        return {k: _mask_position_counts(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_mask_position_counts(v) for v in node]
    if isinstance(node, str):
        return _COUNTS.sub("N of M positions available", node)
    return node
