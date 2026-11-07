"""P2-3: a declared evidence class is exactly E0..E7, never a prefix of something else. Opt in with `pytest -m product`.

Base behaviour (verified on GridForge-AI b649b3e7): the intake loader reads `{"value": ..., "evidence": X}` with
`str(X).upper()[:2]` (gridforge/intake/loader.py `_annotation`; gridforge/change.py `declared_evidence`, used to report evidence changes
between two halls on /v1/diff, does the same). Only the first two characters are looked at, so 'E10', 'E1x', 'E1 (measured)', 'E39', 'E70'
and 'e5foo' are all ACCEPTED, silently as E1, E1, E1, E3, E7 and E5, on every one of the 30 numeric fields that take the object form
(listed in FIELDS), on /v1/compare, /v1/screen and /v1/diff, while 'E8' and 'E' are refused with a message naming the field. 'E39' is read as
an engineering estimate and 'E70' as the top of the scale, neither of which the caller wrote. Separately, the falsy values '', 0, False and
[] are accepted because the loader tests `if raw.get("evidence")`; they are silently ignored, and 'E3 ' (trailing space) is read as E3 while
' E3' (leading space) is refused.

Invariants:
  1. A string that is not an exact class name (E0..E7, any case) is REFUSED (a caller-facing 4xx, whatever the exception class) with a message
     that names the field and quotes the offending value (with or without quotes), on /v1/compare, /v1/screen and /v1/diff, in `before` and
     in `after`, on every evidence-bearing field.
  2. Forms that are not classes but may be tolerated ('E3 ', ' E3', 'e3', '', 0, False, []) are either refused the same way or accepted
     with an answer IDENTICAL to the canonical spelling ('E3', or no evidence key at all for the falsy values): tolerance must not change
     the answer.
The accept arm (E0..E7, 'e3') passes on the base product and shows the refusal assertions are not "refuse everything" in disguise.
Companion of test_an_unknown_evidence_class_is_refused_by_field_name in test_product_evidence_semantics.py, which pins 'E9' only. Not xfail'd.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ._common import mutated, run_many

pytestmark = [pytest.mark.integration, pytest.mark.product]

#: every numeric field of the reference intake that accepts {"value": .., "evidence": ..} (checked against the product: all accept 'E39' on base)
FIELDS = [
    "site.design_drybulb_C",
    "site.design_wetbulb_C",
    "hall.build_year",
    "hall.net_white_space_m2",
    "hall.floor_loading_kPa",
    "hall.clear_height_m",
    "hall.aisle_pitch_m",
    "hall.rack_positions",
    "hall.positions_available",
    "hall.design_density_kW_per_rack",
    "grid.firm_capacity_MVA",
    "grid.power_factor",
    "grid.contracted_MW",
    "grid.current_site_peak_MW",
    "grid.current_it_load_MW",
    "lv.voltage_V",
    "lv.phases",
    "lv.busway_ampacity_A",
    "lv.busway_runs",
    "lv.busway_utilisation_limit",
    "lv.tapoff_max_A",
    "thermal.plant.chilled_water_capacity_kW",
    "thermal.plant.design_supply_C",
    "thermal.plant.design_return_C",
    "thermal.plant.pump_flow_capacity_l_per_min",
    "thermal.plant.available_head_kPa",
    "thermal.plant.free_cooling_hours_per_year",
    "thermal.residual_air_capacity_kW_per_rack",
    "compute.utilisation",
    "economics.horizon_years",
]
BRIEF_BAD = ["E10", "E1x", "E1 (measured)", "E39", "E70", "e5foo"]
MORE_BAD = ["E7x", "E1.5", "E8", "E", "EE"]
CLASSES = ["E0", "E1", "E2", "E3", "E4", "E5", "E6", "E7", "e3"]
TOLERABLE = [
    ("E3 ", "E3"),
    (" E3", "E3"),
    ("e3", "E3"),
    ("", None),
    (0, None),
    (False, None),
    ([], None),
]
EQUIV_FIELDS = [
    "thermal.residual_air_capacity_kW_per_rack",
    "lv.busway_ampacity_A",
    "grid.firm_capacity_MVA",
]
DROP = {"request_digest", "project", "intake_completeness", "provenance", "cost_evidence"}


def _get(doc: dict[str, Any], path: str) -> Any:
    for k in path.split("."):
        doc = doc[k]
    return doc


def _declare(
    intake: dict[str, Any], path: str, evidence: Any, *, omit: bool = False
) -> dict[str, Any]:
    def edit(d: dict[str, Any]) -> None:
        parent = d
        keys = path.split(".")
        for k in keys[:-1]:
            parent = parent[k]
        cur = parent[keys[-1]]
        value = cur["value"] if isinstance(cur, dict) else cur
        wrapped: dict[str, Any] = {"value": value}
        if not omit:
            wrapped["evidence"] = evidence
        parent[keys[-1]] = wrapped

    return mutated(intake, edit)


def _says_value(message: str, bad: Any) -> bool:
    return any(s in message for s in (str(bad), repr(bad), json.dumps(bad)))


def _refused_by_field(row: dict[str, Any], path: str, bad: Any) -> str | None:
    """None when `row` is a caller-facing refusal naming the field and quoting the value; otherwise what is wrong with it."""
    if row["status"] == "ok":
        return "ACCEPTED"
    if row["status"] != "clean":
        return f"escaped as a server fault: {row.get('error')}: {row.get('message')}"
    leaf = path.split(".")[-1]
    message = row["message"]
    if leaf not in message:
        return f"the refusal does not name {leaf}: {message!r}"
    if not _says_value(message, bad):
        return f"the refusal does not quote {bad!r}: {message!r}"
    return None


def _answer(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k not in DROP}


@pytest.mark.parametrize("path", FIELDS)
def test_a_string_that_is_not_an_evidence_class_is_refused_by_field_name_on_every_route(
    intake, path
):
    cases: dict[str, Any] = {}
    for bad in BRIEF_BAD:
        cases[f"compare|{bad}"] = ("compare", {"intake": _declare(intake, path, bad)})
    cases["screen|E39"] = ("screen", {"intake": _declare(intake, path, "E39")})
    cases["diff-after|E39"] = ("diff", {"before": intake, "after": _declare(intake, path, "E39")})
    cases["diff-before|E39"] = ("diff", {"before": _declare(intake, path, "E39"), "after": intake})
    got = run_many(cases)
    problems = []
    for key, row in got.items():
        bad = key.split("|")[1]
        why = _refused_by_field(row, path, bad)
        if why:
            problems.append(f"{key}: evidence {bad!r} on {path}: {why}")
    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("path", EQUIV_FIELDS)
def test_more_strings_that_are_not_classes_are_refused(intake, path):
    got = run_many({b: ("compare", {"intake": _declare(intake, path, b)}) for b in MORE_BAD})
    problems = [
        f"{b!r}: {why}" for b, row in got.items() if (why := _refused_by_field(row, path, b))
    ]
    assert not problems, f"{path}: " + "; ".join(problems)


@pytest.mark.parametrize("path", EQUIV_FIELDS)
def test_tolerated_spellings_never_change_the_answer(intake, path):
    cases: dict[str, Any] = {
        "canonical-E3": ("compare", {"intake": _declare(intake, path, "E3")}),
        "no-evidence": ("compare", {"intake": _declare(intake, path, None, omit=True)}),
    }
    for i, (bad, _) in enumerate(TOLERABLE):
        cases[f"t{i}"] = ("compare", {"intake": _declare(intake, path, bad)})
    got = run_many(cases)
    ref = {"E3": got["canonical-E3"], None: got["no-evidence"]}
    assert ref["E3"]["status"] == "ok" and ref[None]["status"] == "ok", (ref["E3"], ref[None])
    for i, (bad, canon) in enumerate(TOLERABLE):
        row = got[f"t{i}"]
        if row["status"] == "clean":
            assert _refused_by_field(row, path, bad) is None, (bad, row)
            continue
        assert row["status"] == "ok", (bad, row)
        assert _answer(row["result"]) == _answer(ref[canon]["result"]), (
            f"{path}: evidence {bad!r} is accepted but its answer differs from the canonical {canon!r} spelling"
        )


@pytest.mark.parametrize("path", EQUIV_FIELDS)
@pytest.mark.parametrize("good", CLASSES)
def test_every_real_class_is_still_accepted(intake, path, good):
    """Control arm (satisfied on the base product): the exact names E0..E7, in either case, are not refused, on compare and diff."""
    got = run_many(
        {
            "compare": ("compare", {"intake": _declare(intake, path, good)}),
            "diff": ("diff", {"before": intake, "after": _declare(intake, path, good)}),
        }
    )
    assert got["compare"]["status"] == "ok", got["compare"]
    assert got["diff"]["status"] == "ok", got["diff"]
