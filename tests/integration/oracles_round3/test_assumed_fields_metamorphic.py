"""Declaring a qualify figure ASSUMED may only lower what is claimed for it, never change the engineering. Opt in with `pytest -m product`.

METAMORPHIC oracle. POST /v1/qualify accepts `assumed_fields`: a list naming the figures the caller sent but did not measure (the product's
published contract, lib/openapi-discovery.json `capabilities`). The same hall is sent twice, with and without the list, and the two answers are
compared; neither is compared to any number computed here.

Invariant, for EVERY subset of the figures a request sends (all 2^n declarations of each base hall below):
  1. the two answers are identical in every number and every label once the evidence classes and the keys a declaration adds
     (`inputs_declared_assumed`, `intake.assumed_count`, `intake.assumed_count_note`) are set aside: racks, kW and the binding limit do not move;
  2. no evidence class stated anywhere in the answer is higher than in the plain answer (class tags such as '(E5)' in the prose, `evidence`
     fields), and exactly the same places state a class;
  3. each declared figure is named in `inputs_declared_assumed` (once, in the qualifier's order) with the number the request sent and class E0;
     `intake.assumed_count` equals the number declared and says what it counts;
  4. an empty, null or absent list is answered byte-for-byte like the plain request (declaring nothing is not a declaration).
Control arm: declaring the tap-off rating really does lower the class in the binding limit's own text ('(E5)' to '(E0)'), so "never raise" is
not satisfied by "never change anything". The judge is `assumed_fields.annotation_violations`; tests/unit/test_assumed_fields.py proves it fails
on a wrong answer. No product code or constant is used. Not xfail'd.
"""

from __future__ import annotations

import re

import pytest

from gridforge_stress.assumed_fields import annotation_violations, first_difference
from gridforge_stress.product_adapter import call_batch

from ._common import HALL, declare, provided, qualify_bases, qualify_rows, subsets

pytestmark = [pytest.mark.integration, pytest.mark.product]

BASES = qualify_bases()


@pytest.mark.parametrize("base_name", list(BASES))
def test_declaring_any_subset_of_the_figures_only_lowers_classes(base_name):
    base = BASES[base_name]
    names = provided(base)
    declarations = list(subsets(names))
    rows = qualify_rows({",".join(d): declare(base, d) for d in declarations})
    plain = rows[""]
    assert plain["status"] == "ok", plain
    problems: list[str] = []
    for d in declarations:
        row = rows[",".join(d)]
        if row["status"] != "ok":
            problems.append(f"{list(d)}: {row['status']}: {row.get('message')}")
            continue
        problems += [
            f"{list(d)}: {p}"
            for p in annotation_violations(declare(base, d), plain["result"], row["result"])
        ]
    assert not problems, f"{base_name}: " + "; ".join(problems[:6])


def test_declaring_the_tapoff_rating_lowers_the_class_in_the_binding_text():
    """Control arm: the oracle's 'never raise' is not satisfied by a response that never changes anything."""
    rows = qualify_rows({"plain": dict(HALL), "marked": declare(HALL, ("tapoff_max_A",))})
    plain, marked = rows["plain"]["result"]["as_found"], rows["marked"]["result"]["as_found"]
    assert re.search(r"tap-offs rated 63 A \(E5\)", plain["basis"]), plain["basis"]
    assert re.search(r"tap-offs rated 63 A \(E0\)", marked["basis"]), marked["basis"]
    assert plain["racks"] == marked["racks"]


@pytest.mark.parametrize("empty", [None, []])
def test_an_empty_or_null_declaration_is_the_plain_request(empty):
    rows = qualify_rows({"plain": dict(HALL), "empty": {**HALL, "assumed_fields": empty}})
    assert first_difference(rows["plain"]["result"], rows["empty"]["result"]) is None


EXTRA_SPELLINGS = [
    {"evidence": "E7"},
    {"assumed_class": "E5"},
    {"tapoff_max_A_evidence": "E7"},
    {"declared_evidence": {"tapoff_max_A": "E7"}},
    {"assumed_fields_class": "E5"},
    {"measured_fields": ["tapoff_max_A", "contracted_MW"]},
]


@pytest.mark.parametrize("extra", EXTRA_SPELLINGS, ids=lambda e: next(iter(e)))
def test_no_other_spelling_of_the_request_can_raise_or_change_a_class(extra):
    """The only declaration is `assumed_fields`. A request that says anything else about a figure's class is answered like the plain one."""
    rows = qualify_rows(
        {
            "plain": dict(HALL),
            "extra": {**HALL, **extra},
            "extra+declared": {**HALL, **extra, "assumed_fields": ["tapoff_max_A"]},
            "declared": declare(HALL, ("tapoff_max_A",)),
        }
    )
    assert first_difference(rows["plain"]["result"], rows["extra"]["result"]) is None
    assert first_difference(rows["declared"]["result"], rows["extra+declared"]["result"]) is None


@pytest.mark.parametrize("missing", ["plant_supply_C", "positions_available"])
def test_a_figure_the_request_does_not_send_cannot_be_declared_assumed(missing):
    """A declaration about a number nobody sent would be a claim about nothing: it is refused, not answered."""
    body = {k: v for k, v in HALL.items() if k != missing}
    out = call_batch(
        [
            {
                "id": "x",
                "route": "/v1/qualify",
                "tier": "public",
                "body": {**body, "assumed_fields": [missing]},
            }
        ]
    )["x"]
    assert out["status"] == "clean", out
    assert missing in out["message"], out
