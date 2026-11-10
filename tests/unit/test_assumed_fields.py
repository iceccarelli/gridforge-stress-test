"""Mutation proofs for the `assumed_fields` judges: each one is fed a deliberately wrong response and must object, after a control that a
right response passes. An oracle that cannot fail proves nothing (tests/integration/oracles_round3/)."""

from __future__ import annotations

import copy
import json
import math
from typing import Any

import pytest

from gridforge_stress.assumed_fields import (
    annotation_violations,
    capability_violations,
    class_rank,
    completeness_family_violations,
    completeness_violations,
    honouring,
    hostile_response_violations,
    intake_blocks,
)

pytestmark = pytest.mark.unit

REQUEST: dict[str, Any] = {
    "contracted_MW": 12,
    "current_site_peak_MW": 7.4,
    "current_it_load_MW": 4.9,
    "busway_ampacity_A": 400,
    "tapoff_max_A": 63,
    "plant_supply_C": 6,
    "positions_available": 180,
}
NOTE = "Declared assumed by the caller. It is not this hall's figure."


def plain() -> dict[str, Any]:
    return {
        "platform": {"rack_kW": {"value": 135, "evidence": "E0_ASSUMPTION"}},
        "as_found": {
            "racks": 0,
            "it_load_kW": {"value": 0.0, "evidence": "E0_ASSUMPTION"},
            "binding_constraint": "Rack feed / tap-off rating",
            "basis": "needs 205 A (E0) per rack; installed tap-offs rated 63 A (E5). Compared at 100%",
        },
        "after_relief": {"racks": 28},
        "intake": {
            "completeness": 0.29,
            "required_inputs_missing": 1,
            "gaps": [{"path": "ups", "required": True}],
        },
    }


def declared(names: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(request, a CORRECT annotated answer) for declaring `names` assumed."""
    paths = {
        "tapoff_max_A": ("lv.tapoff_max_A", "Installed tap-off rating"),
        "contracted_MW": ("grid.contracted_MW", "Contracted capacity"),
    }
    out = plain()
    if "tapoff_max_A" in names:
        out["as_found"]["basis"] = out["as_found"]["basis"].replace("63 A (E5)", "63 A (E0)")
    order = [n for n in REQUEST if n in names]
    out["inputs_declared_assumed"] = [
        {
            "path": paths[n][0],
            "input": paths[n][1],
            "unit": "-",
            "value": float(REQUEST[n]),
            "evidence": "E0",
            "note": NOTE,
        }
        for n in order
    ]
    out["intake"]["assumed_count"] = len(names)
    out["intake"]["assumed_count_note"] = (
        "completeness counts figures declared assumed as supplied; assumed_count of them are assumptions"
    )
    return {**REQUEST, "assumed_fields": names}, out


def test_a_correct_annotated_answer_passes():
    for names in (
        ["tapoff_max_A"],
        ["contracted_MW", "tapoff_max_A"],
        ["tapoff_max_A", "contracted_MW"],
    ):
        request, answer = declared(names)
        assert annotation_violations(request, plain(), answer) == []


def test_an_empty_declaration_must_be_the_plain_answer():
    assert annotation_violations({**REQUEST, "assumed_fields": []}, plain(), plain()) == []
    changed = plain()
    changed["inputs_declared_assumed"] = []
    assert annotation_violations({**REQUEST, "assumed_fields": []}, plain(), changed)


def mutate(edit) -> list[str]:
    request, answer = declared(["tapoff_max_A", "contracted_MW"])
    answer = copy.deepcopy(answer)
    edit(answer)
    return annotation_violations(request, plain(), answer)


MUTATIONS = {
    "racks changed": lambda a: a["as_found"].update(racks=3),
    "kW changed": lambda a: a["as_found"]["it_load_kW"].update(value=1.0),
    "binding limit changed": lambda a: a["as_found"].update(binding_constraint="Something else"),
    "after relief changed": lambda a: a["after_relief"].update(racks=29),
    "class raised in prose": lambda a: a["as_found"].update(
        basis=a["as_found"]["basis"].replace("(E0) per rack", "(E3) per rack")
    ),
    "class raised in a field": lambda a: a["platform"]["rack_kW"].update(
        evidence="E5_CUSTOMER_DATA"
    ),
    "a class tag removed": lambda a: a["as_found"].update(
        basis=a["as_found"]["basis"].replace(" (E0)", "")
    ),
    "record dropped": lambda a: a.pop("inputs_declared_assumed"),
    "record not a list": lambda a: a.update(inputs_declared_assumed={"path": "lv.tapoff_max_A"}),
    "a declared figure missing": lambda a: a["inputs_declared_assumed"].pop(),
    "an undeclared figure listed": lambda a: a["inputs_declared_assumed"].append(
        {"path": "hall.positions_available", "input": "x", "value": 180.0, "evidence": "E0"}
    ),
    "order not the qualifier's": lambda a: a["inputs_declared_assumed"].reverse(),
    "recorded above E0": lambda a: a["inputs_declared_assumed"][0].update(evidence="E5"),
    "recorded value not the sent one": lambda a: a["inputs_declared_assumed"][1].update(value=64.0),
    "figure not named in words": lambda a: a["inputs_declared_assumed"][0].update(input=""),
    "count wrong": lambda a: a["intake"].update(assumed_count=1),
    "count absent": lambda a: a["intake"].pop("assumed_count"),
    "count unexplained": lambda a: a["intake"].update(assumed_count_note="see docs"),
    "completeness changed": lambda a: a["intake"].update(completeness=0.5),
    "a gap hidden": lambda a: a["intake"].update(gaps=[]),
}


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_every_wrong_annotated_answer_is_flagged(name):
    assert mutate(MUTATIONS[name]), name


def test_a_lowered_class_with_no_other_change_is_not_flagged_but_a_raised_one_is():
    """Direction matters: E5 -> E0 passes (that is the contract), E0 -> E5 does not."""
    request, answer = declared(["tapoff_max_A"])
    assert annotation_violations(request, plain(), answer) == []
    swapped = annotation_violations(request, answer, plain())
    assert any("RAISED" in v or "class" in v for v in swapped)


def test_class_rank_reads_names_and_tags_and_nothing_else():
    assert [class_rank(x) for x in ("E0", "E5", "E0_ASSUMPTION", "E7_X")] == [0, 5, 0, 7]
    assert [class_rank(x) for x in ("E8", "e5", "assumed", "", None, 5, "E10")] == [None] * 7


# --- honouring and the capability flag ------------------------------------------------------------------------------------------------
def test_honouring_distinguishes_full_partial_and_none():
    _request, answer = declared(["tapoff_max_A"])
    assert honouring(plain(), answer) == "full"
    assert honouring(plain(), plain()) == "none"  # the key was ignored
    assert honouring(plain(), None) == "none"  # the key was refused
    recorded_only = copy.deepcopy(answer)
    recorded_only["as_found"]["basis"] = plain()["as_found"]["basis"]
    assert honouring(plain(), recorded_only) == "partial"
    lowered_only = copy.deepcopy(answer)
    lowered_only.pop("inputs_declared_assumed")
    assert honouring(plain(), lowered_only) == "partial"
    wrong_row = copy.deepcopy(answer)
    wrong_row["inputs_declared_assumed"][0]["evidence"] = "E5"
    assert honouring(plain(), wrong_row) == "partial"


@pytest.mark.parametrize(
    "caps,honours,flagged",
    [
        ({"qualify_assumed_fields": True}, "full", False),
        ({}, "none", False),
        ({"qualify_assumed_fields": False}, "none", False),
        ({"qualify_assumed_fields": True}, "none", True),  # a claim the route lacks
        ({}, "full", True),  # a capability with no flag
        ({"qualify_assumed_fields": False}, "full", True),
        ({"qualify_assumed_fields": True}, "partial", True),
        ({}, "partial", True),
        (
            {"qualify_assumed_fields": "true"},
            "full",
            True,
        ),  # not a boolean: a client reading `=== true` sees nothing
        ({"qualify_assumed_fields": 1}, "none", True),
        ({"other": "yes", "qualify_assumed_fields": True}, "full", True),
    ],
)
def test_the_capability_flag_must_match_the_route(caps, honours, flagged):
    assert bool(capability_violations({"capabilities": caps}, honours)) is flagged


def test_a_version_without_capabilities_is_a_flag_that_is_absent():
    assert capability_violations({}, "none") == []
    assert capability_violations({}, "full")
    assert capability_violations({"capabilities": ["qualify_assumed_fields"]}, "full")


# --- hostile values -------------------------------------------------------------------------------------------------------------------
def clean_refusal(message: str = "assumed_fields[0] 'x' is not a qualify field") -> str:
    return json.dumps({"error": message})


def judged(
    status=422,
    content_type="application/json",
    body=None,
    elapsed=0.01,
    hostile: Any = "x",
    may_succeed=False,
) -> list[str]:
    return hostile_response_violations(
        "t",
        hostile,
        status,
        content_type,
        clean_refusal() if body is None else body,
        elapsed,
        may_succeed=may_succeed,
    )


def test_a_clean_refusal_passes():
    assert judged() == []
    assert judged(status=400) == []
    assert judged(status=200, body="{}", may_succeed=True) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 500},
        {"status": 502},
        {"status": 200},  # hostile value quietly accepted
        {"status": 301},
        {"status": -1},
        {"elapsed": 3.5},
        {"content_type": "text/html"},
        {"body": "<html>boom</html>"},
        {"body": json.dumps({"error": "x" * 700})},
        {"body": json.dumps({"error": "bad \x1b[31m name"})},
        {"body": json.dumps({"error": "bad " + chr(0x202E) + " name"})},
        {"body": json.dumps({"error": "bad " + chr(0x2028) + " name"})},
        {
            "body": json.dumps({"error": "bad \ud800 name"}),
        },
        {"body": json.dumps({"error": "'" + "A" * 200 + "'"}), "hostile": "A" * 200},
    ],
    ids=lambda k: next(iter(k)) + "=" + str(next(iter(k.values())))[:12],
)
def test_every_unclean_answer_is_flagged(kwargs):
    assert judged(**kwargs)


def test_a_slow_answer_is_flagged_at_the_fuzz_limit_and_not_before():
    assert judged(elapsed=3.0) == []
    assert judged(elapsed=3.01)


# --- completeness -----------------------------------------------------------------------------------------------------------------------
def block(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "completeness": 0.5,
        "required_inputs_missing": 1,
        "can_issue_a_study": False,
        "gaps": [{"path": "a", "required": True}, {"path": "b", "required": False}],
    }
    base.update(kw)
    return base


def test_a_consistent_block_passes():
    assert completeness_violations(block()) == []
    assert (
        completeness_violations(
            block(
                completeness=1.0,
                required_inputs_missing=0,
                can_issue_a_study=True,
                gaps=[{"path": "b", "required": False}],
            )
        )
        == []
    )
    assert (
        completeness_violations(
            block(completeness=1.0, required_inputs_missing=0, can_issue_a_study=True, gaps=[])
        )
        == []
    )


@pytest.mark.parametrize(
    "bad",
    [
        block(completeness=1.0),  # 100% with a required gap listed and a count of 1
        block(
            completeness=1.0, required_inputs_missing=0, can_issue_a_study=True
        ),  # 100% with a required gap listed
        block(completeness=0.4, gaps=[]),  # short of full with nothing short
        block(required_inputs_missing=0),  # count disagrees with the list
        block(required_inputs_missing=3),
        block(can_issue_a_study=True),  # can issue with a required gap
        block(
            can_issue_a_study=False,
            required_inputs_missing=0,
            gaps=[{"path": "b", "required": False}],
            completeness=0.9,
        ),
        block(completeness=1.2),
        block(completeness=-0.1),
        block(completeness=math.nan),
        block(completeness=math.inf),
        block(completeness=True),
        block(completeness="0.5"),
    ],
)
def test_every_inconsistent_block_is_flagged(bad):
    assert completeness_violations(bad)


def test_family_judge_flags_a_score_that_falls_as_gaps_close_and_an_assumed_that_raises():
    few = block(completeness=0.9, gaps=[{"path": "a", "required": True}])
    many = block(completeness=0.5)
    assert completeness_family_violations({"few": few, "many": many}) == []
    assert completeness_family_violations(
        {"few": block(completeness=0.4, gaps=[{"path": "a", "required": True}]), "many": many}
    )
    assert completeness_family_violations(
        {"x": block(completeness=0.5), "y": block(completeness=0.6)}
    )  # same gaps, different scores
    ok = {"none": block(completeness=0.5), "two": block(completeness=0.5)}
    assert completeness_family_violations(ok, {"none": 0, "two": 2}) == []
    raised = {"none": block(completeness=0.5), "two": block(completeness=0.5)}
    raised["two"]["completeness"] = 0.5
    # the same gap list scoring higher when more is declared is caught by the equal-gaps rule and by the assumed rule
    assert completeness_family_violations(
        {"none": block(completeness=0.5), "two": block(completeness=0.6)}, {"none": 0, "two": 2}
    )


def test_intake_blocks_finds_the_block_wherever_it_is():
    assert intake_blocks({"intake": block()}) == [block()]
    assert intake_blocks({"a": [{"intake": block()}]}) == [block()]
    assert intake_blocks({"intake_completeness": 0.4}) == []
