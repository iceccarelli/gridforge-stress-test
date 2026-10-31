"""The free, public, unauthenticated routes (/v1/qualify, /v1/estimate): what the company promises a stranger, checked.

A public caller reaches ONLY these two routes. (Every other route needs a key; that gate is the HTTP layer's and is outside what this harness
reaches, so "a public caller is refused at client routes" stays UNCHECKED here.) The promises tested: the free read carries no schedule, cost or
money; it carries its own disclaimer; its physical claims respect bounds derived from the request alone; it is reproducible."""

from __future__ import annotations

import copy
import random
import re

import pytest

from gridforge_stress.fuzz import default_plans, reference_intake
from gridforge_stress.oracles import qualifier_violations
from gridforge_stress.product_adapter import call_batch

from ._product_helpers import require_product

pytestmark = [pytest.mark.integration, pytest.mark.product]

BASE = next(p for p in default_plans(reference_intake()) if p["name"] == "qualify")["base"]
KEY = re.compile(
    r"capex|lead_time|weeks|cost|price|econom|\beur\b|euro|ladder|annual|schedule", re.I
)
TEXT = re.compile(
    r"(€\s?\d|\d[\d,.]*\s?(weeks?|wks|months?)\b|\bEUR\s?\d|\d[\d,.]*\s?(EUR|euros?)\b)", re.I
)


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


def qualify(body):
    return call_batch(
        [{"id": "x", "route": "/v1/qualify", "tier": "public", "body": body, "want_result": True}]
    )["x"]


def leaks(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if KEY.search(str(k)):
                yield f"key {path}.{k}"
            yield from leaks(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from leaks(v, f"{path}[{i}]")
    elif isinstance(node, str) and (m := TEXT.search(node)):
        yield f"text {path}: {node[max(0, m.start() - 40) : m.end() + 15]!r}"


def test_the_scan_itself_can_see_a_leak():
    """Mutation: a scan that cannot fail proves nothing."""
    assert (
        list(leaks({"a": {"capex_eur": 1}}))
        and list(leaks({"b": ["lead time 160 weeks"]}))
        and list(leaks({"c": "EUR 4,500"}))
    )
    assert not list(leaks({"racks": 28, "basis": "platform needs 205 A per rack"}))


def draws(n=120, seed=20261031):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        d = copy.deepcopy(BASE)
        d.update(
            contracted_MW=round(rng.uniform(2, 40), 1),
            current_site_peak_MW=round(rng.uniform(0.5, 35), 1),
            current_it_load_MW=round(rng.uniform(0.5, 15), 1),
            busway_ampacity_A=rng.choice([250, 400, 630, 800, 1000, 1600]),
            tapoff_max_A=rng.choice([32, 63, 125, 250, 400]),
            plant_supply_C=rng.choice([6, 12, 18, 24, 30]),
            positions_available=rng.choice([10, 40, 100, 180]),
        )
        out.append(d)
    return out


@pytest.fixture(scope="module")
def answered():
    ds = draws()
    out = call_batch(
        [
            {"id": str(i), "route": "/v1/qualify", "tier": "public", "body": d, "want_result": True}
            for i, d in enumerate(ds)
        ]
    )
    return ds, out


def test_every_random_request_is_answered_or_refused_cleanly(answered):
    _, out = answered
    assert {v["status"] for v in out.values()} <= {"ok", "clean"}
    assert sum(v["status"] == "ok" for v in out.values()) >= 100, (
        "premise: nearly every draw is answerable, so the checks below mean something"
    )


def test_the_free_read_carries_no_schedule_cost_or_money(answered):
    _, out = answered
    found = {i: list(leaks(v["result"])) for i, v in out.items() if v["status"] == "ok"}
    found = {i: h for i, h in found.items() if h}
    assert not found, f"{len(found)} responses leak, e.g. {next(iter(found.values()))[:3]}"


def test_physical_bounds_from_the_request_alone(answered):
    ds, out = answered
    bad = {
        i: qualifier_violations(ds[int(i)], v["result"])
        for i, v in out.items()
        if v["status"] == "ok"
    }
    bad = {i: p for i, p in bad.items() if p}
    assert not bad, f"{len(bad)} answers break a bound, e.g. {next(iter(bad.values()))[:2]}"
    counts = {v["result"]["after_relief"]["racks"] for v in out.values() if v["status"] == "ok"}
    assert len(counts) >= 8 and 0 in counts and max(counts) > 20, (
        f"the draws must reach both no-headroom and plenty: {sorted(counts)}"
    )


def test_the_free_read_states_that_it_is_not_a_bankable_number():
    r = qualify(BASE)["result"]
    assert (
        "not a bankable number" in r["notice"].lower() and "directional" in r["notice"].lower()
    ), r["notice"]
    assert (
        "0 reconciled observations" in r["calibration"]["statement"]
        or r["calibration"]["state"] == "uncalibrated"
    )


@pytest.mark.parametrize(
    "field,values,direction",
    [
        ("contracted_MW", (6, 9, 12, 20, 40), "up"),
        ("current_site_peak_MW", (2, 5, 7.4, 10, 14), "down"),
        ("positions_available", (4, 10, 20, 40), "up"),
    ],
)
def test_more_capacity_never_means_fewer_racks_and_more_load_never_means_more(
    field, values, direction
):
    got = [qualify({**BASE, field: v})["result"]["after_relief"]["racks"] for v in values]
    assert got == sorted(got, reverse=(direction == "down")), dict(zip(values, got, strict=True))
    assert len(set(got)) >= 3, f"the sweep must actually move: {got}"


def test_estimate_discloses_only_the_advertised_unit_price():
    e = call_batch(
        [
            {
                "id": "x",
                "route": "/v1/estimate",
                "tier": "public",
                "body": {
                    "endpoint": "/v1/screen",
                    "body": {"intake": reference_intake()},
                    "hall_count": 1,
                },
                "want_result": True,
            }
        ]
    )["x"]["result"]
    flagged = {h for h in leaks(e)}
    assert flagged == {"key .unit_price_cents"}, flagged


def test_the_free_routes_are_reproducible_across_interpreters():
    a, b = qualify(BASE), qualify(BASE)  # two separate interpreters, so two string-hash seeds
    assert a["status"] == b["status"] == "ok" and a["result"] == b["result"]
