"""The Portfolio Screen (/v1/portfolio) against screening each hall on its own.

Independent expectations come from /v1/screen for each intake (the recommended scenario), never from the portfolio's own rows. The one open
defect (the same hall listed twice is counted twice) is a strict xfail tied to GridForge-AI#112."""

from __future__ import annotations

import copy
import itertools

import pytest

from ._product_helpers import mutated, require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


def hall(tag, edit):
    def e(d):
        d["hall"]["id"] = tag
        edit(d)

    return mutated(e)


A = hall("DH-02", lambda d: None)
B = hall("DH-03", lambda d: d["grid"].update(contracted_MW=10.0))
C = hall("DH-04", lambda d: d["grid"].update(current_site_peak_MW=8.5))


def alone(intake):
    s = run("/v1/screen", {"intake": intake})
    rec = next(x for x in s["scenarios"] if x["id"] == s["recommended"])
    return (
        rec["after_ladder"]["racks"],
        rec["time_to_power"].get("weeks_to_full"),
        rec["time_to_power"].get("date_basis"),
    )


@pytest.fixture(scope="module")
def expected():
    return {i["hall"]["id"]: alone(i) for i in (A, B, C)}


def rows(out):
    return {h["hall"]: h for h in out["halls"]}


def test_each_row_equals_that_hall_screened_alone(expected):
    out = run("/v1/portfolio", {"intakes": [A, B, C]})
    got = rows(out)
    assert set(got) == set(expected)
    for hid, (racks, weeks, basis) in expected.items():
        assert (got[hid]["racks"], got[hid]["weeks_to_full"], got[hid]["date_basis"]) == (
            racks,
            weeks,
            basis,
        ), hid
    assert len({r for r, _, _ in expected.values()}) == 3, (
        "premise: the three halls differ, so the comparison means something"
    )


def test_the_total_is_the_sum_of_the_rows(expected):
    out = run("/v1/portfolio", {"intakes": [A, B, C]})
    assert (
        out["total_racks"]
        == sum(h["racks"] for h in out["halls"])
        == sum(r for r, _, _ in expected.values())
    )


def test_ranks_are_a_permutation_ordered_by_racks():
    out = run("/v1/portfolio", {"intakes": [A, B, C]})
    ordered = sorted(out["halls"], key=lambda h: h["rank"])
    assert [h["rank"] for h in ordered] == [1, 2, 3]
    assert [h["racks"] for h in ordered] == sorted((h["racks"] for h in ordered), reverse=True)


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_input_order_changes_nothing(order):
    base = run("/v1/portfolio", {"intakes": [A, B, C]})
    inputs = [copy.deepcopy(x) for x in (A, B, C)]
    out = run("/v1/portfolio", {"intakes": [inputs[i] for i in order]})
    assert {h["hall"]: (h["rank"], h["racks"]) for h in out["halls"]} == {
        h["hall"]: (h["rank"], h["racks"]) for h in base["halls"]
    }
    assert out["total_racks"] == base["total_racks"]


def test_a_single_hall_portfolio_equals_the_screen(expected):
    assert run("/v1/portfolio", {"intakes": [A]})["total_racks"] == expected["DH-02"][0]


@pytest.mark.xfail(
    strict=True,
    reason="GridForge-AI#112: the same hall listed twice is counted twice, with no warning",
)
def test_the_same_hall_listed_twice_is_not_counted_twice(expected):
    from gridforge_stress.product_adapter import call_batch

    r = call_batch(
        [
            {
                "id": "x",
                "route": "/v1/portfolio",
                "tier": "client",
                "body": {"intakes": [A, copy.deepcopy(A)]},
                "want_result": True,
            }
        ]
    )["x"]
    if r["status"] == "clean":
        return  # refused with a message: an acceptable answer
    out = r["result"]
    one = expected["DH-02"][0]
    warned = any(
        "duplicate" in str(v).lower() or "twice" in str(v).lower()
        for k, v in out.items()
        if k not in ("halls",)
    )
    assert out["total_racks"] <= one or warned, (
        f"total {out['total_racks']} for one hall of {one} racks, with no warning"
    )
