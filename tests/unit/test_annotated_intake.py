"""Mutation proofs for the annotated-integer and empty-hall judges: a control that a right answer passes, then wrong answers that must not."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from gridforge_stress.annotated_intake import (
    declared_rank,
    expected_recorded_rank,
    parity_violations,
    recorded_class_violations,
    refusal_violations,
    source_rows,
    zero_occupied_violations,
)

pytestmark = pytest.mark.unit

PATH = "hall.positions_available"


def result(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "request_digest": "aaaa",
        "scenarios": [
            {
                "id": "s1",
                "constraints": [
                    {
                        "id": "floor_space",
                        "racks_permitted": 180,
                        "basis": "180 of 560 positions available in H",
                    }
                ],
            }
        ],
        "intake": {"completeness": 0.4, "warnings": ["w1"]},
        "caveats": ["c1"],
    }
    base.update(over)
    return base


def ok(res: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "result": res}


def test_parity_passes_for_the_same_answer_with_a_new_digest_and_a_line_naming_the_field():
    annotated = result(request_digest="bbbb")
    annotated["intake"]["warnings"] = ["w1", f"{PATH}: declared evidence E7 capped at E5"]
    assert parity_violations("t", ok(result()), ok(annotated), PATH) == []
    assert (
        parity_violations(
            "t", {"status": "clean", "message": "m"}, {"status": "clean", "message": "n"}, PATH
        )
        == []
    )


def edited(edit) -> dict[str, Any]:
    res = copy.deepcopy(result())
    edit(res)
    return ok(res)


@pytest.mark.parametrize(
    "plain,annotated",
    [
        (
            ok(result()),
            edited(lambda r: r["scenarios"][0]["constraints"][0].update(racks_permitted=179)),
        ),
        (
            ok(result()),
            edited(
                lambda r: r["scenarios"][0]["constraints"][0].update(
                    basis="180 of 400 positions available in H"
                )
            ),
        ),
        (ok(result()), edited(lambda r: r["intake"].update(completeness=0.5))),
        (ok(result()), edited(lambda r: r["scenarios"].append({}))),
        (ok(result()), edited(lambda r: r["intake"].update(warnings=[]))),  # a line lost
        (
            ok(result()),
            edited(lambda r: r["caveats"].append("something unrelated")),
        ),  # a line added that does not name the field
        (
            ok(result()),
            {"status": "clean", "message": "refused"},
        ),  # plain answered, annotated refused
        (
            {"status": "clean", "message": "refused"},
            ok(result()),
        ),  # plain refused, annotated answered
        (ok(result()), {"status": "fault", "error": "KeyError", "message": "'value'"}),
        (
            {"status": "fault", "error": "KeyError", "message": "x"},
            {"status": "fault", "error": "KeyError", "message": "x"},
        ),
    ],
)
def test_every_difference_the_annotation_must_not_make_is_flagged(plain, annotated):
    assert parity_violations("t", plain, annotated, PATH)


def with_gaps(
    res: dict[str, Any], *, issue: bool, sentence: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    res = copy.deepcopy(res)
    res["intake"].update(can_issue_a_study=issue, recommended_engagement=sentence, gaps=rows)
    return res


OPEN_ROW = {
    "path": PATH,
    "required": True,
    "assumed": "180, declared as an assumption (E0): it counts as open",
}
READY = "No required inputs are missing, so this supports a full Study."
OPEN = "1 required input was sent but declared as an assumption (class E0), not the hall's own figure, so it counts as open."


def declared_e0(**over: Any) -> dict[str, Any]:
    """A correct answer to a required figure annotated E0: its one open row, the Study not issuable, the sentence says so."""
    return with_gaps(
        result(),
        issue=over.get("issue", False),
        sentence=over.get("sentence", OPEN),
        rows=over.get("rows", [OPEN_ROW]),
    )


def plain_ready() -> dict[str, Any]:
    return with_gaps(result(), issue=True, sentence=READY, rows=[])


def test_a_required_figure_declared_e0_may_open_its_row_and_close_the_study_and_nothing_else():
    assert parity_violations("t", ok(plain_ready()), ok(declared_e0()), PATH, (PATH,)) == []


@pytest.mark.parametrize(
    "annotated",
    [
        declared_e0(issue=True),  # the Study stays issuable on an assumption
        declared_e0(sentence=READY),  # the sentence does not say so
        declared_e0(rows=[{**OPEN_ROW, "required": False}]),  # the row is not required
        declared_e0(
            rows=[{**OPEN_ROW, "assumed": "180"}]
        ),  # the row does not say it is an assumption
        declared_e0(rows=[OPEN_ROW, dict(OPEN_ROW)]),  # listed twice
        declared_e0(
            rows=[OPEN_ROW, {"path": "transformers", "required": True}]
        ),  # a second, unrelated row
    ],
)
def test_the_open_assumption_allowance_is_bounded(annotated):
    assert parity_violations("t", ok(plain_ready()), ok(annotated), PATH, (PATH,))


def test_the_allowance_is_only_for_a_figure_declared_e0_that_the_product_opened():
    # the same differences with the figure declared at another class, or with no declaration, are changes the annotation must not make
    assert parity_violations("t", ok(plain_ready()), ok(declared_e0()), PATH)
    assert parity_violations("t", ok(plain_ready()), ok(declared_e0()), PATH, ())
    # a figure that is not required gets no allowance: no row was added, so a flipped readiness is a difference
    flipped = with_gaps(result(), issue=False, sentence=OPEN, rows=[])
    assert parity_violations("t", ok(plain_ready()), ok(flipped), PATH, (PATH,))


def test_the_allowance_does_not_hide_a_changed_number():
    wrong = declared_e0()
    wrong["scenarios"][0]["constraints"][0]["racks_permitted"] = 179
    assert parity_violations("t", ok(plain_ready()), ok(wrong), PATH, (PATH,))


def doc(*rows: str) -> str:
    return (
        "## 4\n\nFigures supplied with a source. These are the client's own data.\n\n| Input | Value | Evidence | Source | Note |\n| --- | --- | --- | --- | --- |\n"
        + "\n".join(rows)
        + "\n\n## 5. Next\n\n| a | 1 | E0 | - | - |\n"
    )


BASE = doc("| floor loading | 12 kPa | E5 | — | design value |")


def with_row(row: str) -> str:
    return doc("| floor loading | 12 kPa | E5 | — | design value |", row)


def test_the_study_table_is_read_without_the_next_section():
    rows = source_rows(with_row("| Rack positions available | 180 positions | E3 | — | n |"))
    assert [(r["label"], r["number"], r["rank"]) for r in rows] == [
        ("floor loading", 12.0, 5),
        ("Rack positions available", 180.0, 3),
    ]
    assert source_rows("no table here") == []


@pytest.mark.parametrize(
    "declared,recorded",
    [
        ("E0", 0),
        ("E3", 3),
        ("E5", 5),
        ("E6", 5),
        ("E7", 5),
        ("e2", 2),
        ("E5_CUSTOMER_DATA", 5),
        (None, 5),
        ("E0_ASSUMPTION", 0),
    ],
)
def test_a_right_record_passes(declared, recorded):
    annotated = with_row(f"| Rack positions available | 180 positions | E{recorded} | — | n |")
    assert recorded_class_violations("t", declared, 180.0, BASE, annotated) == []


def test_no_class_recorded_below_the_ceiling_is_accepted_but_above_is_not():
    assert (
        recorded_class_violations("t", None, 180.0, BASE, with_row("| P | 180 x | E3 | — | n |"))
        == []
    )
    assert recorded_class_violations(
        "t", None, 180.0, BASE, with_row("| P | 180 x | E6 | — | n |")
    )  # a figure with no class, recorded above the ceiling
    assert expected_recorded_rank(None) == 5
    assert expected_recorded_rank("E7") == 5 and declared_rank("measured") is None


@pytest.mark.parametrize(
    "declared,annotated",
    [
        (
            "E0",
            with_row("| P | 180 x | E5 | — | n |"),
        ),  # promoted from assumed to the client ceiling
        ("E3", with_row("| P | 180 x | E4 | — | n |")),
        ("E7", with_row("| P | 180 x | E7 | — | n |")),  # the ceiling is E5
        ("E3", with_row("| P | 180 x | E1 | — | n |")),  # not the declared class
        ("E3", BASE),  # no row at all
        (
            "E3",
            doc(
                "| floor loading | 12 kPa | E5 | — | design value |",
                "| P | 180 x | E3 | — | n |",
                "| Q | 9 x | E3 | — | n |",
            ),
        ),  # two rows
        ("E3", with_row("| P | 181 x | E3 | — | n |")),  # another number
        ("E3", doc("| P | 180 x | E3 | — | n |")),  # the existing row vanished
    ],
)
def test_every_wrong_record_is_flagged(declared, annotated):
    assert recorded_class_violations("t", declared, 180.0, BASE, annotated)


def test_the_row_regex_reads_e0_to_e7_only():
    assert [
        r["rank"] for r in source_rows(doc("| a | 1 | E7 | - | - |", "| b | 1 | E8 | - | - |"))
    ] == [7]


def test_refusal_must_be_a_clean_naming_and_quoting_refusal():
    good = {
        "status": "clean",
        "message": "hall.positions_available: declared evidence 'measured' is not an evidence class",
    }
    assert refusal_violations("t", good, PATH, "measured") == []
    assert (
        refusal_violations(
            "t",
            {"status": "clean", "message": "positions_available: declared evidence 5 is not"},
            PATH,
            5,
        )
        == []
    )
    assert refusal_violations("t", {"status": "ok", "result": {}}, PATH, "measured")
    assert refusal_violations(
        "t", {"status": "fault", "error": "KeyError", "message": "x"}, PATH, "measured"
    )
    assert refusal_violations(
        "t",
        {"status": "clean", "message": "declared evidence 'measured' is not a class"},
        PATH,
        "measured",
    )  # field not named
    assert refusal_violations(
        "t", {"status": "clean", "message": "positions_available is not valid"}, PATH, "measured"
    )  # value not quoted


# --- empty vs occupied --------------------------------------------------------------------------------------------------------------
def hall(
    positions: str, *, extra_warning: bool, rank: int = 0, completeness: float = 0.4
) -> dict[str, Any]:
    return {
        "scenarios": [
            {
                "constraints": [
                    {
                        "id": "floor",
                        "racks_permitted": 180,
                        "basis": f"180 of {positions} positions available in H",
                        "evidence": f"E{rank}",
                    },
                    {
                        "id": "busway",
                        "basis": "existing load and occupied positions are not subtracted",
                    },
                ]
            }
        ],
        "intake": {
            "completeness": completeness,
            "warnings": ["generic warning"]
            + (["Busway: the 380 occupied positions would draw 3,040 kW"] if extra_warning else []),
        },
    }


def test_the_right_pair_passes_and_a_warning_about_occupied_positions_may_differ():
    assert (
        zero_occupied_violations(hall("180", extra_warning=False), hall("560", extra_warning=True))
        == []
    )


@pytest.mark.parametrize(
    "zero",
    [
        {
            **hall("180", extra_warning=False),
            "scenarios": [
                {
                    "constraints": [
                        {
                            "id": "floor",
                            "racks_permitted": 181,
                            "basis": "180 of 180 positions available in H",
                            "evidence": "E0",
                        },
                        {
                            "id": "busway",
                            "basis": "existing load and occupied positions are not subtracted",
                        },
                    ]
                }
            ],
        },
        hall("180", extra_warning=False, rank=5),  # a higher class for the empty hall
        hall("180", extra_warning=False, completeness=0.9),
        {
            **hall("180", extra_warning=False),
            "scenarios": [
                {
                    "constraints": [
                        {
                            "id": "floor",
                            "racks_permitted": 180,
                            "basis": "180 of 180 positions available in H",
                            "evidence": "E0",
                        }
                    ]
                }
            ],
        },  # disclosure lost
    ],
)
def test_an_empty_hall_that_looks_better_is_flagged(zero):
    assert zero_occupied_violations(zero, hall("560", extra_warning=True))


def test_a_warning_lost_that_is_not_about_occupied_positions_is_flagged():
    zero = hall("180", extra_warning=False)
    zero["intake"]["warnings"] = []
    assert zero_occupied_violations(zero, hall("560", extra_warning=True))
