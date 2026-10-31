from __future__ import annotations

import pytest

from gridforge_stress.invariants import quantities, range_violations

pytestmark = pytest.mark.unit


def q(value, low, high, label="x"):
    return {"value": value, "low": low, "high": high, "unit": "u", "label": label}


TREE = {
    "a": q(5, 4, 6),
    "b": [{"c": q(1, 1, 1)}, {"d": {"e": q(10, 8, 12)}}],
    "ignored": {"value": 1},
    "text": "value low high",
}


def test_finds_every_quantity_at_any_depth_and_ignores_lookalikes():
    paths = [p for p, _ in quantities(TREE)]
    assert paths == [".a", ".b[0].c", ".b[1].d.e"]


def test_a_coherent_tree_has_no_violation():
    assert range_violations(TREE) == []


@pytest.mark.parametrize(
    "name,bad",
    [
        ("value_above_high", q(300, 80, 210)),
        ("value_below_low", q(36, 80, 210)),
        ("inverted_range", q(5, 6, 4)),
        ("nan_value", q(float("nan"), 0, 1)),
        ("inf_high", q(1, 0, float("inf"))),
    ],
)
def test_each_incoherent_quantity_is_caught_wherever_it_sits(name, bad):
    assert range_violations({"deep": {"list": [{"q": bad}]}}), name
    assert range_violations([bad]), name


def test_boundaries_are_inside_the_range():
    assert range_violations([q(80, 80, 210), q(210, 80, 210)]) == []


def test_bools_and_strings_are_not_quantities():
    assert list(quantities({"value": True, "low": 0, "high": 1})) == []
    assert list(quantities({"value": "5", "low": 0, "high": 9})) == []
