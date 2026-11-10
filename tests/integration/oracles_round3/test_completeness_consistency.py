"""A reported completeness agrees with the gaps listed beside it, and declaring figures assumed never raises it. Opt in with `pytest -m product`.

CONSISTENCY oracle over every response that reports a completeness: the `intake` block of POST /v1/qualify and /v1/screen (completeness,
`gaps`, `required_inputs_missing`, `can_issue_a_study`, and on a declared request `assumed_count`) and `intake_completeness` of /v1/compare.
The score is judged only against the lists in the SAME response, never against a count computed here (the denominator is the product's).

Invariants:
  1. WITHIN a response: completeness is a finite fraction in [0, 1]; 1.0 never appears with a REQUIRED gap listed or a non-zero
     `required_inputs_missing`; a score below 1.0 lists at least one gap; `required_inputs_missing` equals the number of gaps listed as required;
     `can_issue_a_study` is true exactly when no required gap is listed.
  2. ACROSS requests for the same hall with figures withheld one at a time or in groups: a response that lists a strict subset of another's gap
     paths never scores lower; two responses that list the same gaps score the same.
  3. ASSUMING: declaring more of the same figures assumed never raises completeness (the product documents that it leaves it unchanged, because
     a declared figure was supplied, and says so in `assumed_count_note`; the oracle asserts only the direction a product could not defend
     breaking), and never removes a listed gap or adds one.
  4. ACROSS ROUTES: /v1/screen and /v1/compare report one completeness for one intake.
Judges: `assumed_fields.completeness_violations` and `completeness_family_violations`; unit proofs in tests/unit/test_assumed_fields.py. Not xfail'd.
"""

from __future__ import annotations

from typing import Any

import pytest

from gridforge_stress.assumed_fields import (
    completeness_family_violations,
    completeness_violations,
    gap_paths,
    intake_blocks,
)

from ..oracles_engine_defects._common import reference_doc
from ._common import HALL, declare, provided, qualify_rows, subsets
from ._intake import rows_for, without

pytestmark = [pytest.mark.integration, pytest.mark.product]

#: figures withheld from the reference intake, cumulatively (v0 gives all, each next one removes one more)
WITHHELD = [
    "lv.tapoff_max_A",
    "thermal.plant.design_supply_C",
    "grid.current_it_load_MW",
    "hall.floor_loading_kPa",
    "hall.net_white_space_m2",
    "grid.power_factor",
    "lv.voltage_V",
    "site.design_drybulb_C",
    "thermal.plant.chilled_water_capacity_kW",
    "grid.current_site_peak_MW",
]


def only_block(result: dict[str, Any]) -> dict[str, Any]:
    blocks = intake_blocks(result)
    assert len(blocks) == 1, [sorted(b) for b in blocks]
    return blocks[0]


@pytest.fixture(scope="module")
def qualify():
    """Every declaration over several subsets of the figures sent: {(sent, declared): block}."""
    bodies: dict[str, dict[str, Any]] = {}
    sent_sets = [tuple(HALL)] + [
        tuple(k for k in HALL if k not in drop)
        for drop in (
            ("plant_supply_C",),
            ("positions_available", "tapoff_max_A"),
            ("busway_ampacity_A", "current_it_load_MW", "plant_supply_C"),
        )
    ]
    for sent in sent_sets:
        base = {k: HALL[k] for k in sent}
        for d in subsets(provided(base)):
            bodies[f"{'+'.join(sent)}#{'+'.join(d)}"] = declare(base, d)
    rows = qualify_rows(bodies)
    for key, row in rows.items():
        assert row["status"] == "ok", (key, row)
    return {k: (bodies[k], only_block(r["result"])) for k, r in rows.items()}


def test_every_qualify_response_agrees_with_its_own_lists(qualify):
    problems = [
        f"{k}: {p}" for k, (_, block) in qualify.items() for p in completeness_violations(block)
    ]
    assert not problems, "; ".join(problems[:6])


def test_withholding_figures_never_raises_the_score_on_the_qualifier(qualify):
    by_sent: dict[str, dict[str, dict[str, Any]]] = {}
    for key, (_, block) in qualify.items():
        if key.endswith("#"):  # the undeclared request of each sent-set
            by_sent[key.split("#")[0]] = block
    # one block per sent-set, compared as a family
    family = {sent: block for sent, block in by_sent.items()}
    assert len(family) >= 3
    assert not completeness_family_violations(family)


def test_declaring_more_assumed_never_raises_completeness_or_changes_the_gaps(qualify):
    groups: dict[str, dict[str, dict[str, Any]]] = {}
    for key, (_, block) in qualify.items():
        sent, declared = key.split("#")
        groups.setdefault(sent, {})[declared] = block
    problems: list[str] = []
    for sent, blocks in groups.items():
        plain = blocks[""]
        counts = {d: (len(d.split("+")) if d else 0) for d in blocks}
        problems += [f"{sent}: {p}" for p in completeness_family_violations(blocks, counts)]
        for declared, block in blocks.items():
            if block["completeness"] > plain["completeness"]:
                problems.append(
                    f"{sent}#{declared}: declaring raised completeness {plain['completeness']} -> {block['completeness']}"
                )
            if gap_paths(block) != gap_paths(plain):
                problems.append(f"{sent}#{declared}: declaring changed the listed gaps")
            if declared and block.get("assumed_count") != counts[declared]:
                problems.append(f"{sent}#{declared}: assumed_count {block.get('assumed_count')!r}")
    assert not problems, "; ".join(problems[:6])


@pytest.fixture(scope="module")
def screens():
    doc = reference_doc()
    cases = {}
    for n in range(len(WITHHELD) + 1):
        d = doc
        for path in WITHHELD[:n]:
            d = without(d, path)
        cases[f"screen|v{n}"] = ("screen", {"intake": d})
        cases[f"compare|v{n}"] = ("compare", {"intake": d})
    return rows_for(cases)


def test_every_screen_response_agrees_with_its_own_lists(screens):
    problems = []
    for key, row in screens.items():
        if not key.startswith("screen"):
            continue
        assert row["status"] == "ok", (key, row)
        problems += [f"{key}: {p}" for p in completeness_violations(only_block(row["result"]))]
    assert not problems, "; ".join(problems[:6])


def test_withholding_figures_never_raises_the_score_on_the_screen(screens):
    family = {k: only_block(r["result"]) for k, r in screens.items() if k.startswith("screen")}
    assert not completeness_family_violations(family)
    scores = [family[f"screen|v{n}"]["completeness"] for n in range(len(WITHHELD) + 1)]
    assert scores == sorted(scores, reverse=True), scores


def test_withholding_figures_actually_lowers_the_score(screens):
    """Control arm: the family is not constant, so 'always report 1.0' or 'always the same number' could not pass."""
    first, last = screens["screen|v0"], screens[f"screen|v{len(WITHHELD)}"]
    assert only_block(last["result"])["completeness"] < only_block(first["result"])["completeness"]


def test_the_two_routes_report_one_completeness_for_one_intake(screens):
    problems = []
    for n in range(len(WITHHELD) + 1):
        s = only_block(screens[f"screen|v{n}"]["result"])["completeness"]
        c = screens[f"compare|v{n}"]["result"].get("intake_completeness")
        if s != c:
            problems.append(f"v{n}: screen {s!r}, compare {c!r}")
    assert not problems, "; ".join(problems)
