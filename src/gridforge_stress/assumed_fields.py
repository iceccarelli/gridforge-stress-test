"""Pure judges for the `assumed_fields` parameter of the product's free qualifier (POST /v1/qualify) and its capability flag.

Nothing here calls the product and nothing re-implements its arithmetic: every function takes responses the real handler already produced and
says whether they satisfy an invariant, so a test can feed a deliberately wrong response and watch the judge object (tests/unit/test_assumed_fields.py).

The contract being judged is the product's published one (lib/openapi-discovery.json, `capabilities`): a request MAY name, in `assumed_fields`, the
qualify figures it sends but did not measure. Declaring a figure assumed can only LOWER what is claimed for it (class E0, the bottom of the scale);
it cannot change the engineering answer; the response must name what was declared. Classes are ordered E0 < E1 < ... < E7.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any

#: The seven figures the qualifier takes (the request's names) and the intake path the engine records each under (published in
#: lib/qualify-assumed.ts QUALIFY_ENGINE_PATH). In the qualifier's own order, which is the order `inputs_declared_assumed` must list them in.
QUALIFY_ENGINE_PATH: dict[str, str] = {
    "contracted_MW": "grid.contracted_MW",
    "current_site_peak_MW": "grid.current_site_peak_MW",
    "current_it_load_MW": "grid.current_it_load_MW",
    "busway_ampacity_A": "lv.busway_ampacity_A",
    "tapoff_max_A": "lv.tapoff_max_A",
    "plant_supply_C": "thermal.plant.design_supply_C",
    "positions_available": "hall.positions_available",
}
FIELD_ORDER = tuple(QUALIFY_ENGINE_PATH)

#: The capability flag the engine publishes in GET /v1/version for this behaviour.
CAPABILITY = "qualify_assumed_fields"

#: Keys the engine may ADD to a response when (and only when) at least one figure is declared assumed.
ADDED_TOP = "inputs_declared_assumed"
ADDED_INTAKE = ("assumed_count", "assumed_count_note")

_CLASS_NAME = re.compile(r"^E([0-7])(?:_|$)")
_CLASS_TAG = re.compile(r"\(E([0-7])\)")
_MASK = "(E?)"


def class_rank(label: Any) -> int | None:
    """0..7 for 'E0', 'E5' or 'E0_ASSUMPTION'-style names; None for anything else."""
    if isinstance(label, str) and (m := _CLASS_NAME.match(label)):
        return int(m.group(1))
    return None


def without_additions(response: dict[str, Any]) -> dict[str, Any]:
    """The response with the keys a declaration may add taken out (a deep copy of the containers it touches)."""
    out = {k: v for k, v in response.items() if k != ADDED_TOP}
    intake = out.get("intake")
    if isinstance(intake, dict):
        out["intake"] = {k: v for k, v in intake.items() if k not in ADDED_INTAKE}
    return out


#: The readiness fields of an `intake` block. A required figure declared assumed is OPEN (the product's rule, stated in its loader: "a required input that
#: was sent but declared an assumption (class E0) is a template's value and not this hall's"), so declaring it may move exactly these, and the one gap row
#: that names it. They are judged on their own (`open_declared_violations`), not by equality with the undeclared answer.
READINESS_KEYS = ("can_issue_a_study", "recommended_engagement")


def declared_paths(declared: Any) -> set[str]:
    """The intake paths of the qualify figures a request declared assumed (names the qualifier does not take are ignored here: the route refuses them)."""
    if not isinstance(declared, list):
        return set()
    return {
        QUALIFY_ENGINE_PATH[f] for f in declared if isinstance(f, str) and f in QUALIFY_ENGINE_PATH
    }


def _gaps_of(response: dict[str, Any]) -> list[dict[str, Any]]:
    block = response.get("intake")
    rows = block.get("gaps") if isinstance(block, dict) else None
    return [g for g in rows if isinstance(g, dict)] if isinstance(rows, list) else []


def _open_rows(
    plain: dict[str, Any], annotated: dict[str, Any], paths: set[str]
) -> dict[str, list[dict[str, Any]]]:
    """Per declared path, the rows `annotated` lists for it that `plain` did not: a declared required figure is listed once, as an open required input."""
    before = {g.get("path") for g in _gaps_of(plain)}
    return {
        p: [g for g in _gaps_of(annotated) if g.get("path") == p]
        for p in sorted(paths)
        if p not in before
    }


def without_open_declared(
    plain: dict[str, Any], annotated: dict[str, Any], paths: set[str]
) -> dict[str, Any]:
    """`annotated` with the gap row of each declared figure taken out (only a row `plain` did not list, only a required one) and the readiness fields
    taken out of its `intake` block: what is left is what a declaration is not allowed to change. Everything else is kept, so a row that is wrong in
    any other way, or a second row, still shows."""
    rows = _open_rows(plain, annotated, paths)
    drop = {id(g) for found in rows.values() for g in found[:1] if g.get("required") is True}
    out = dict(annotated)
    block = annotated.get("intake")
    if isinstance(block, dict):
        kept = (
            [g for g in block["gaps"] if id(g) not in drop]
            if isinstance(block.get("gaps"), list)
            else block.get("gaps")
        )
        out["intake"] = {k: v for k, v in block.items() if k not in READINESS_KEYS}
        if kept is not None:
            out["intake"]["gaps"] = kept
    return out


def without_readiness(response: dict[str, Any]) -> dict[str, Any]:
    """The same for the undeclared answer: its `intake` block without the readiness fields."""
    block = response.get("intake")
    if not isinstance(block, dict):
        return response
    return {**response, "intake": {k: v for k, v in block.items() if k not in READINESS_KEYS}}


def added_open_paths(paths: set[str], plain: dict[str, Any], annotated: dict[str, Any]) -> set[str]:
    """The declared paths among `paths` for which `annotated` lists a gap row `plain` did not: the figures the product treated as open required inputs."""
    return {p for p, rows in _open_rows(plain, annotated, paths).items() if rows}


def open_paths_violations(
    paths: set[str], plain: dict[str, Any], annotated: dict[str, Any]
) -> list[str]:
    """What declaring the figures at `paths` assumed does to the readiness of the answer. Empty means the product's rule holds on this pair.

    EVALUATES, for each path the undeclared answer did not already list as a gap: it is listed ONCE, as a REQUIRED gap, with an `assumed` text that
    says it is an assumption; the Study is not issuable (`can_issue_a_study` is false); the readiness sentence says an assumption is open.
    DOES NOT EVALUATE whether that rule is the right one, nor the figures' completeness (that is judged elsewhere).
    """
    if not paths:
        return []
    out: list[str] = []
    for path, rows in _open_rows(plain, annotated, paths).items():
        if len(rows) != 1:
            out.append(
                f"{path} was declared assumed but the answer lists {len(rows)} gap rows for it, not one"
            )
            continue
        if rows[0].get("required") is not True:
            out.append(f"{path} was declared assumed but its gap row is not marked required")
        if "assum" not in str(rows[0].get("assumed", "")).lower():
            out.append(
                f"{path} was declared assumed but its gap row does not say it is an assumption: {str(rows[0].get('assumed'))[:80]!r}"
            )
    raw = annotated.get("intake")
    block: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if block.get("can_issue_a_study") is not False:
        out.append(
            f"{sorted(paths)} declared assumed but can_issue_a_study is {block.get('can_issue_a_study')!r}: the Study must not be issuable on an assumption"
        )
    if "assum" not in str(block.get("recommended_engagement", "")).lower():
        out.append(
            "a figure was declared assumed but the readiness sentence (recommended_engagement) does not mention an assumption"
        )
    return out


def open_declared_violations(
    declared: Any, plain: dict[str, Any], annotated: dict[str, Any]
) -> list[str]:
    """`open_paths_violations` for the qualify figures a request named in `assumed_fields`: every one of the seven is a required input."""
    return open_paths_violations(declared_paths(declared), plain, annotated)


def class_ranks(node: Any, path: str = "") -> dict[str, tuple[int, ...]]:
    """Every evidence class the response states, keyed by where: an `evidence` value is one rank, a text with '(E5)' tags is its tags in order."""
    out: dict[str, tuple[int, ...]] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "evidence" and (r := class_rank(v)) is not None:
                out[f"{path}.{k}"] = (r,)
            else:
                out.update(class_ranks(v, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.update(class_ranks(v, f"{path}[{i}]"))
    elif isinstance(node, str) and (tags := _CLASS_TAG.findall(node)):
        out[path] = tuple(int(t) for t in tags)
    return out


def class_free(node: Any) -> Any:
    """The response with every stated evidence class masked: what is left is the engineering answer and the prose around the classes."""
    if isinstance(node, dict):
        return {
            k: (_MASK if k == "evidence" and class_rank(v) is not None else class_free(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [class_free(v) for v in node]
    if isinstance(node, str):
        return _CLASS_TAG.sub(_MASK, node)
    return node


def _same(a: Any, b: Any) -> bool:
    """Equality that treats NaN as equal to itself, so a judge is never fooled by a non-finite number being unequal to its twin."""
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return bool(a == b)


def first_difference(a: Any, b: Any, path: str = "") -> str | None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k} present in only one"
            if (d := first_difference(a[k], b[k], f"{path}.{k}")) is not None:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path} has {len(a)} vs {len(b)} entries"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            if (d := first_difference(x, y, f"{path}[{i}]")) is not None:
                return d
        return None
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


def annotation_violations(
    request: dict[str, Any], plain: dict[str, Any], annotated: dict[str, Any]
) -> list[str]:
    """Where `annotated` (the answer to `request`, which carries `assumed_fields`) breaks the declaration contract against `plain` (the answer
    to the same request without the key). Empty means the contract holds on this pair.

    EVALUATES, for the declared set D: (0) each declared figure is an open required input (`open_declared_violations`); (1) the engineering answer is
    identical, class labels, the keys a declaration adds and the gap row and readiness fields of (0) aside; (2) no stated
    class is higher than in `plain`, at any path, and the same paths state a class; (3) `inputs_declared_assumed` lists exactly D, once each, in
    the qualifier's order, each with the figure the request sent and class E0; (4) `intake.assumed_count` equals |D| and explains itself.
    With D empty: the answer is IDENTICAL to `plain` and carries none of the added keys.
    DOES NOT EVALUATE whether the engineering answer is right, or what class an undeclared figure should have.
    """
    declared = request.get("assumed_fields") or []
    out: list[str] = []
    paths = declared_paths(declared)
    out += open_declared_violations(declared, plain, annotated)
    # What is compared is the answer without the one gap row and the readiness fields that declaring a required figure moves (judged just above).
    base = without_readiness(without_additions(plain)) if paths else without_additions(plain)
    stripped = (
        without_additions(without_open_declared(plain, annotated, paths))
        if paths
        else without_additions(annotated)
    )
    if (diff := first_difference(class_free(base), class_free(stripped))) is not None:
        out.append(f"declaring {declared} changed more than class labels: {diff}")
    before, after = class_ranks(base), class_ranks(stripped)
    if set(before) != set(after):
        out.append(f"the places that state a class changed: {sorted(set(before) ^ set(after))[:4]}")
    for where in sorted(set(before) & set(after)):
        if len(before[where]) != len(after[where]) or any(
            a > b for a, b in zip(after[where], before[where], strict=False)
        ):
            out.append(
                f"{where}: class RAISED by declaring {declared}: {before[where]} -> {after[where]}"
            )
    rows = annotated.get(ADDED_TOP)
    block = annotated.get("intake")
    intake: dict[str, Any] = block if isinstance(block, dict) else {}
    if not declared:
        if annotated != plain:
            out.append("an empty declaration is not byte-for-byte the plain answer")
        return out
    want = [f for f in FIELD_ORDER if f in declared]
    if not isinstance(rows, list):
        return [*out, f"declared {want} but the response has no {ADDED_TOP} list"]
    got = [r.get("path") if isinstance(r, dict) else None for r in rows]
    if got != [QUALIFY_ENGINE_PATH[f] for f in want]:
        out.append(f"{ADDED_TOP} lists {got}, expected {[QUALIFY_ENGINE_PATH[f] for f in want]}")
    for r in rows:
        if not isinstance(r, dict):
            out.append(f"{ADDED_TOP} entry is not an object: {r!r}")
            continue
        if class_rank(r.get("evidence")) != 0:
            out.append(f"{r.get('path')}: recorded as {r.get('evidence')!r}, not E0")
        field = next((f for f in want if QUALIFY_ENGINE_PATH[f] == r.get("path")), None)
        if field is not None and not _same(r.get("value"), float(request[field])):
            out.append(
                f"{r.get('path')}: recorded value {r.get('value')!r}, the request sent {request[field]!r}"
            )
        if not r.get("input"):
            out.append(f"{r.get('path')}: the entry does not name the figure in words")
    if intake.get("assumed_count") != len(want):
        out.append(
            f"intake.assumed_count is {intake.get('assumed_count')!r}, {len(want)} were declared"
        )
    note = intake.get("assumed_count_note")
    if not isinstance(note, str) or "assum" not in note.lower():
        out.append("intake.assumed_count carries no explanation that the count is of assumptions")
    return out


# --- the capability flag ---------------------------------------------------------------------------------------------------------------
def honouring(
    plain: dict[str, Any] | None, annotated: dict[str, Any] | None, field: str = "tapoff_max_A"
) -> str:
    """'full' when the route visibly honoured declaring `field` assumed, 'none' when it ignored or refused it, else 'partial'.

    `plain`/`annotated` are the 200 bodies of the same request without and with `assumed_fields: [field]` (None for a non-200). Honoured means
    BOTH observable effects: the figure is recorded in `inputs_declared_assumed` as E0, and some stated class is strictly LOWER than in the plain
    answer. Only one of the two is 'partial': a half-implemented capability, which a flag must not call whole.
    """
    if plain is None or annotated is None:
        return "none"
    recorded = any(
        isinstance(r, dict)
        and r.get("path") == QUALIFY_ENGINE_PATH[field]
        and class_rank(r.get("evidence")) == 0
        for r in (annotated.get(ADDED_TOP) or [])
    )
    core = without_additions(without_open_declared(plain, annotated, {QUALIFY_ENGINE_PATH[field]}))
    before, after = class_ranks(without_readiness(plain)), class_ranks(core)
    lowered = any(
        w in after
        and len(after[w]) == len(before[w])
        and any(a < b for a, b in zip(after[w], before[w], strict=True))
        for w in before
    )
    if recorded and lowered:
        return "full"
    return "partial" if recorded or lowered else "none"


def capability_violations(version: dict[str, Any], honours: str) -> list[str]:
    """Where GET /v1/version's `capabilities` and what POST /v1/qualify really does disagree. Empty means they agree.

    A flag is a claim: present and exactly `true` iff the route honours the parameter. Absent (or false) iff it does not. A flag that is not a
    boolean is a violation in itself (a client that reads only `=== true` would see no capability where the engine meant one, or the reverse).
    """
    caps = version.get("capabilities")
    out: list[str] = []
    if caps is not None and not isinstance(caps, dict):
        return [f"capabilities is a {type(caps).__name__}, not an object"]
    flags = caps or {}
    out += [
        f"capability {k!r} is {v!r}, not a boolean"
        for k, v in flags.items()
        if not isinstance(v, bool)
    ]
    flag = flags.get(CAPABILITY)
    advertised = flag is True
    if honours == "partial":
        out.append(
            f"{CAPABILITY}: /v1/qualify honours assumed_fields only partly (record or class lowering, not both)"
        )
    elif advertised and honours == "none":
        out.append(f"{CAPABILITY} is true but /v1/qualify does not honour assumed_fields")
    elif not advertised and honours == "full":
        out.append(
            f"/v1/qualify honours assumed_fields but /v1/version does not advertise {CAPABILITY} (capabilities has {flag!r})"
        )
    return out


# --- hostile values ---------------------------------------------------------------------------------------------------------------------
#: A request that takes longer than this is a denial-of-service lever on a free public route (the fuzz runner's own default).
SLOW_S = 3.0
#: The longest refusal text a hostile value may provoke. The product echoes at most 60 characters of an offending name.
MAX_ERROR_CHARS = 600
MAX_ECHO_CHARS = 80


def _unsafe_chars(text: str) -> list[str]:
    """Characters that must not reach a log line, a terminal or a page raw: controls, format characters (bidi overrides), line/paragraph
    separators, surrogates, private-use and unassigned."""
    return sorted(
        {
            f"U+{ord(c):04X}"
            for c in text
            if unicodedata.category(c) in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
        }
    )


def hostile_response_violations(
    label: str,
    hostile: Any,
    status: int,
    content_type: str,
    body: str,
    elapsed_s: float,
    *,
    may_succeed: bool = False,
    slow_s: float = SLOW_S,
) -> list[str]:
    """A request that carried a hostile `assumed_fields` was answered how? Empty means cleanly.

    Clean is: a status below 500 (4xx for a value that must be refused, 200 only where `may_succeed`); inside `slow_s` seconds; a JSON body of
    type application/json; and, for a refusal, a message that quotes no more than MAX_ECHO_CHARS of the offending text and holds no raw control,
    format (bidi), separator, surrogate or private-use character, so the hostile text is shown escaped or not at all.
    """
    out: list[str] = []
    if status < 0:
        return [f"{label}: no HTTP answer ({body})"]
    if status >= 500:
        out.append(f"{label}: HTTP {status}, a server fault, for caller input")
    elif status == 200 and not may_succeed:
        out.append(f"{label}: HTTP 200, the hostile value was accepted")
    elif status != 200 and not 400 <= status < 500:
        out.append(f"{label}: HTTP {status} is neither an answer nor a caller-facing refusal")
    if elapsed_s > slow_s:
        out.append(f"{label}: took {elapsed_s:.2f}s (limit {slow_s:g}s)")
    if "json" not in content_type.lower():
        out.append(f"{label}: content type {content_type!r}, not JSON")
    try:
        parsed = json.loads(body)
    except ValueError:
        return [*out, f"{label}: the body is not JSON: {body[:80]!r}"]
    if status != 200 and isinstance(parsed, dict):
        message = str(parsed.get("error", ""))
        if len(message) > MAX_ERROR_CHARS:
            out.append(f"{label}: the refusal is {len(message)} characters long")
        if bad := _unsafe_chars(message):
            out.append(f"{label}: the refusal contains raw {bad[:5]}")
        if (
            isinstance(hostile, str)
            and len(hostile) > MAX_ECHO_CHARS
            and hostile[:MAX_ECHO_CHARS] in message
        ):
            out.append(
                f"{label}: the refusal echoes more than {MAX_ECHO_CHARS} characters of the offending text"
            )
    return out


# --- completeness -----------------------------------------------------------------------------------------------------------------------
def intake_blocks(response: Any) -> list[dict[str, Any]]:
    """Every object in the response that reports a `completeness` beside a `gaps` list (the shape of the qualifier's and the screen's `intake`)."""
    found: list[dict[str, Any]] = []
    if isinstance(response, dict):
        if "completeness" in response and isinstance(response.get("gaps"), list):
            found.append(response)
        for v in response.values():
            found += intake_blocks(v)
    elif isinstance(response, list):
        for v in response:
            found += intake_blocks(v)
    return found


def gap_paths(block: dict[str, Any]) -> set[str]:
    return {
        g["path"] for g in block["gaps"] if isinstance(g, dict) and isinstance(g.get("path"), str)
    }


def completeness_violations(
    block: dict[str, Any], declared_open: frozenset[str] = frozenset()
) -> list[str]:
    """A reported completeness against the missing / required lists in the SAME block. Empty means they agree.

    EVALUATES: a fraction in [0, 1]; 1.0 only with no REQUIRED gap listed and `required_inputs_missing` (when reported) zero; below 1.0 only
    with at least one gap listed (a score short of full must show what is short); `required_inputs_missing` equal to the number of gaps listed
    as required, not counting the gap rows of figures the request declared assumed (`declared_open`, intake paths): those were sent, so the count
    of MISSING inputs leaves them out, and the product says so in `assumed_count_note`; `can_issue_a_study` true exactly when no required gap is
    listed at all (a declared one included); `assumed_count` (when reported) not above the number of figures the response lists as declared.
    DOES NOT EVALUATE the denominator, the weighting, or whether a gap really is a gap.
    """
    out: list[str] = []
    c = block.get("completeness")
    if (
        isinstance(c, bool)
        or not isinstance(c, (int, float))
        or not math.isfinite(c)
        or not 0 <= c <= 1
    ):
        return [f"completeness {c!r} is not a fraction in [0, 1]"]
    gaps = [g for g in block["gaps"] if isinstance(g, dict)]
    required = [g for g in gaps if g.get("required") is True]
    missing_rows = [g for g in required if g.get("path") not in declared_open]
    if c >= 1 and missing_rows:
        out.append(
            f"completeness is {c:g} but required gaps are listed: {[g.get('path') for g in missing_rows][:4]}"
        )
    if c < 1 and not gaps:
        out.append(f"completeness is {c:g} but no gap is listed to account for it")
    missing = block.get("required_inputs_missing")
    if missing is not None:
        if missing != len(missing_rows):
            out.append(
                f"required_inputs_missing is {missing!r}, {len(missing_rows)} gaps are listed as required"
                + (
                    f" besides {len(required) - len(missing_rows)} declared assumed"
                    if len(required) != len(missing_rows)
                    else ""
                )
            )
        if c >= 1 and missing:
            out.append(f"completeness is {c:g} with {missing} required inputs missing")
    issue = block.get("can_issue_a_study")
    if issue is not None and issue is not (not required):
        out.append(f"can_issue_a_study is {issue!r} with {len(required)} required gaps listed")
    return out


def completeness_family_violations(
    blocks: dict[str, dict[str, Any]], assumed: dict[str, int] | None = None
) -> list[str]:
    """Across responses to related requests: more listed gaps never means a higher score, and equal gap lists mean equal scores.

    `blocks` maps a label to an `intake` block. For any two, gaps(A) a subset of gaps(B) implies completeness(A) >= completeness(B)
    (a score cannot fall as gaps close). `assumed` optionally maps a label to how many figures were declared assumed; among responses that
    list the same gaps, declaring MORE assumed never raises completeness.
    """
    out: list[str] = []
    labels = sorted(blocks)
    for a in labels:
        for b in labels:
            ga, gb = gap_paths(blocks[a]), gap_paths(blocks[b])
            ca, cb = blocks[a]["completeness"], blocks[b]["completeness"]
            if ga < gb and ca < cb:
                out.append(
                    f"{a} lists fewer gaps than {b} ({len(ga)} < {len(gb)}) but scores lower ({ca:g} < {cb:g})"
                )
            if ga == gb and ca != cb:
                out.append(f"{a} and {b} list the same gaps but score {ca:g} and {cb:g}")
            if assumed and ga == gb and assumed.get(a, 0) > assumed.get(b, 0) and ca > cb:
                out.append(
                    f"declaring more assumed ({a}: {assumed[a]}, {b}: {assumed.get(b, 0)}) raised completeness {cb:g} -> {ca:g}"
                )
    return out
