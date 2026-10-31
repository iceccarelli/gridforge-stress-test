"""A customer must be able to reproduce a result: the same request gives the same answer in two separate interpreters (different string-hash
seeds, so set/dict-ordering leaks show), and JSON key order carries no meaning. Covers every route that returns an assessment or a deliverable,
not only /v1/compare."""

from __future__ import annotations

import copy

import pytest

from gridforge_stress.fuzz import default_plans, reference_intake
from gridforge_stress.product_adapter import call_batch

from ._product_helpers import require_product

pytestmark = [pytest.mark.integration, pytest.mark.product]

#: Plans whose base body the product answers; bids needs a compliant response and is covered by test_product_bids_and_dates.
ROUTES = (
    "screen",
    "compare",
    "study",
    "diff",
    "deck",
    "proposal",
    "spec",
    "spec_list",
    "portfolio",
    "power_assess",
    "estimate",
)


def reversed_keys(node):
    """Same document, every object's keys in the opposite order (list order is meaningful and kept)."""
    if isinstance(node, dict):
        return {k: reversed_keys(node[k]) for k in reversed(list(node))}
    if isinstance(node, list):
        return [reversed_keys(x) for x in node]
    return node


def cases(transform):
    out = []
    for p in default_plans(reference_intake()):
        if p["name"] in ROUTES:
            out.append(
                {
                    "id": p["name"],
                    "route": p["route"],
                    "tier": p["tier"],
                    "body": transform(copy.deepcopy(p["base"])),
                    "want_result": True,
                }
            )
    return out


@pytest.fixture(scope="module")
def runs():
    require_product()
    return {
        "first": call_batch(
            cases(lambda b: b)
        ),  # three separate interpreters: three string-hash seeds
        "second": call_batch(cases(lambda b: b)),
        "reversed": call_batch(cases(reversed_keys)),
    }


def first_difference(a, b, path=""):
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                return f"{path}.{k}: present in only one"
            if d := first_difference(a[k], b[k], f"{path}.{k}"):
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            if d := first_difference(x, y, f"{path}[{i}]"):
                return d
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


def test_the_checker_itself_sees_a_difference():
    """Mutation: a comparison that cannot fail proves nothing."""
    assert first_difference({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}) == ".a[1].b: 2 vs 3"
    assert first_difference({"a": 1}, {"a": 1, "b": 2}) == ".b: present in only one"
    assert first_difference([1], [1, 2]) == ": length 1 vs 2"
    assert first_difference({"a": 1}, {"a": 1}) is None


@pytest.mark.parametrize("route", ROUTES)
def test_two_separate_interpreters_give_the_same_answer(runs, route):
    a, b = runs["first"][route], runs["second"][route]
    assert a["status"] == b["status"] == "ok", (a, b)
    assert first_difference(a["result"], b["result"]) is None, first_difference(
        a["result"], b["result"]
    )


@pytest.mark.parametrize("route", ROUTES)
def test_key_order_carries_no_meaning(runs, route):
    a, r = runs["first"][route], runs["reversed"][route]
    assert a["status"] == r["status"] == "ok", (a["status"], r["status"])
    assert first_difference(a["result"], r["result"]) is None, first_difference(
        a["result"], r["result"]
    )
