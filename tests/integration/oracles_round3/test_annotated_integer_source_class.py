"""An annotated whole number is the same number, and its source class is never promoted. Opt in with `pytest -m product`.

SOURCE-CLASS oracle for the intake's integer figures, which accept `{"value": N, "evidence": <class>, "note": ...}` in place of `N`:
hall.build_year, hall.rack_positions, hall.positions_available, lv.phases, lv.busway_runs (the loader's own list of fields read through its
integer path). The product's documented reading: `value` is the number; `evidence` is exactly one of E0..E7 (or the eight canonical enum names,
case-insensitive on the bare form); a client's own attestation is capped at E5 ("E6 and E7 are earned through the calibration ledger"); a note
or source is printed beside the figure in the study document's table of 'Figures supplied with a source'.

Invariants, per field, over every route that takes an intake document:
  1. NUMERIC IDENTITY. The annotated figure and the bare figure with the same number give identical answers on /v1/screen and /v1/compare (every
     number and label; the prose keeps every line and adds only lines that name the field, such as the E6/E7 cap warning). An integer-valued
     float inside `value` behaves as the integer.
  2. NO PROMOTION. In the study document the annotated figure is recorded once, with its number, at a class no higher than the one declared and
     never above E5 (declared E6 or E7 is recorded E5); a figure with NO class is recorded no higher than E5. A declared class is recorded as
     declared (E0 stays E0, E3 stays E3).
  3. NO GUESSING. A class that is not E0..E7 ('measured', 'observed', 'quoted', 'assumed', 'E9', '', 'E') is REFUSED in the caller's terms,
     naming the field and quoting the value, on every route; it is never read as something else.
KNOWN LIMIT, NOT ASSERTED. The loader records an annotated whole number in the study table only when a note or source accompanies it; a class
declared with neither (`{"value": 180, "evidence": "E0"}`) leaves no trace in the study document or the JSON answers (checked on the product at
8f944ac), although it is not promoted either. That is a design choice of the loader ("the object form promises that a source and a note are
recorded"), reported to the owner; the judge here covers the case where a record exists.
Judges: `annotated_intake.parity_violations`, `recorded_class_violations`, `refusal_violations`; their unit proofs are in
tests/unit/test_annotated_intake.py. Companion of test_evidence_class_is_parsed_exactly (30 fields, 'E39'-style prefixes). Not xfail'd.
"""

from __future__ import annotations

from typing import Any

import pytest

from gridforge_stress.annotated_intake import (
    declared_rank,
    parity_violations,
    recorded_class_violations,
    refusal_violations,
)

from ..oracles_engine_defects._common import reference_doc
from ._intake import rows_for, set_path

pytestmark = [pytest.mark.integration, pytest.mark.product]

FIELDS = [
    "hall.build_year",
    "hall.rack_positions",
    "hall.positions_available",
    "lv.phases",
    "lv.busway_runs",
]
CLASSES = [
    "E0",
    "E1",
    "E2",
    "E3",
    "E4",
    "E5",
    "E6",
    "E7",
    "e2",
    "E5_CUSTOMER_DATA",
    "E0_ASSUMPTION",
]
NOT_CLASSES = [
    "measured",
    "observed",
    "quoted",
    "assumed",
    "E9",
    "",
    "E",
    "E10",
    "E5 (measured)",
    5,
    True,
    ["E5"],
]


def value_of(path: str) -> int:
    node: Any = reference_doc()
    for k in path.split("."):
        node = node[k]
    return int(node["value"] if isinstance(node, dict) else node)


@pytest.fixture(scope="module")
def answers():
    cases: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in FIELDS:
        n = value_of(path)
        variants: dict[str, Any] = {"plain": n}
        for c in CLASSES:
            variants[f"cls={c}"] = {"value": n, "evidence": c, "note": "synthetic note"}
        variants["no class"] = {"value": n, "note": "synthetic note"}
        variants["source only"] = {"value": n, "source": "synthetic source"}
        variants["float value"] = {"value": float(n), "evidence": "E3", "note": "synthetic note"}
        for i, bad in enumerate(NOT_CLASSES):
            variants[f"bad#{i}"] = {"value": n, "evidence": bad, "note": "synthetic note"}
        for tag, v in variants.items():
            doc = set_path(reference_doc(), path, v)
            cases[f"screen|{path}|{tag}"] = ("screen", {"intake": doc})
            cases[f"compare|{path}|{tag}"] = ("compare", {"intake": doc})
    study = {
        k.replace("screen|", "study|"): ("study", {**body, "format": "md"})
        for k, (route, body) in cases.items()
        if route == "screen"
    }
    return rows_for({**cases, **study})


@pytest.mark.parametrize("path", FIELDS)
@pytest.mark.parametrize("route", ["screen", "compare"])
def test_an_annotated_figure_gives_the_answer_of_the_bare_number(answers, route, path):
    plain = answers[f"{route}|{path}|plain"]
    problems = []
    for c in CLASSES:
        problems += parity_violations(
            f"{route} {path} {c}",
            plain,
            answers[f"{route}|{path}|cls={c}"],
            path,
            (path,) if declared_rank(c) == 0 else (),
        )
    for tag in ("no class", "source only", "float value"):
        problems += parity_violations(
            f"{route} {path} {tag}", plain, answers[f"{route}|{path}|{tag}"], path
        )
    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("path", FIELDS)
def test_an_annotated_figure_is_recorded_no_higher_than_declared_or_e5(answers, path):
    n = value_of(path)
    plain = answers[f"study|{path}|plain"]
    assert plain["status"] == "ok", plain
    problems = []
    declared: list[tuple[str, Any]] = [(f"cls={c}", c) for c in CLASSES] + [("no class", None)]
    declared += [("source only", None), ("float value", "E3")]
    for tag, evidence in declared:
        row = answers[f"study|{path}|{tag}"]
        assert row["status"] == "ok", (tag, row)
        problems += recorded_class_violations(
            f"{path} {tag}",
            evidence,
            float(n),
            plain["result"]["document"],
            row["result"]["document"],
        )
    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("path", FIELDS)
@pytest.mark.parametrize("route", ["screen", "compare", "study"])
def test_a_source_class_that_is_not_a_class_is_refused_by_field(answers, route, path):
    problems = []
    for i, bad in enumerate(NOT_CLASSES):
        problems += refusal_violations(
            f"{route} {path} #{i}", answers[f"{route}|{path}|bad#{i}"], path, bad
        )
    assert not problems, "; ".join(problems)


def test_the_catalogue_is_not_all_refusals(answers):
    """Control arm: every real class is accepted on every field, so 'refuse everything' cannot pass the refusal test."""
    refused = [k for k, row in answers.items() if "|cls=" in k and row["status"] != "ok"]
    assert not refused, refused[:5]
