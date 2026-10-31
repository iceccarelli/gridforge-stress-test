"""Differential test: 120 seeded random intakes through the real /v1/screen, each judged by the independent headroom oracle.

Hand-picked variants (test_product_constraint_oracle.py) find the cases someone thought of; random draws over redundancy, unit counts, ratings,
derating, power factor, MVA, contracted MW, site peak and IT load find the ones nobody did. Deterministic: the seed is fixed, so a failure reproduces."""

from __future__ import annotations

import copy
import random

import pytest

from gridforge_stress.fuzz import reference_intake
from gridforge_stress.oracles import check_screen, expected_headroom_kw
from gridforge_stress.product_adapter import call_batch

from ._product_helpers import require_product

pytestmark = [pytest.mark.integration, pytest.mark.product]

SEED = 20261031
DRAWS = 120
REDUNDANCY = ["N", "N+1", "N+2", "2N"]


def draw(rng, base):
    d = copy.deepcopy(base)
    t, u, g = d["transformers"][0], d["ups"][0], d["grid"]
    t.update(
        units=rng.randint(1, 6),
        redundancy=rng.choice(REDUNDANCY),
        unit_rating_MVA=round(rng.uniform(1, 20), 1),
        derating_factor=round(rng.uniform(0.5, 1.0), 2),
    )
    u.update(
        units=rng.randint(1, 10),
        redundancy=rng.choice(REDUNDANCY),
        unit_rating_kW=rng.choice([250, 500, 800, 1000, 1200, 1600]),
    )
    g.update(
        power_factor=round(rng.uniform(0.7, 1.0), 2),
        firm_capacity_MVA=round(rng.uniform(5, 40), 1),
        contracted_MW=round(rng.uniform(5, 40), 1),
        current_site_peak_MW=round(rng.uniform(1, 30), 1),
        current_it_load_MW=round(rng.uniform(0.5, 15), 1),
    )
    return d


@pytest.fixture(scope="module")
def run_all():
    require_product()
    rng = random.Random(SEED)
    base = reference_intake()
    intakes = [draw(rng, base) for _ in range(DRAWS)]
    cases = [
        {
            "id": str(i),
            "route": "/v1/screen",
            "tier": "client",
            "body": {"intake": d},
            "want_result": True,
        }
        for i, d in enumerate(intakes)
    ]
    return intakes, call_batch(cases)


def test_the_draws_cover_the_space(run_all):
    """Non-vacuity: every redundancy mode on both blocks, headroom of both signs on all three constraints, and many distinct rack counts."""
    intakes, out = run_all
    assert {d["transformers"][0]["redundancy"] for d in intakes} == set(REDUNDANCY)
    assert {d["ups"][0]["redundancy"] for d in intakes} == set(REDUNDANCY)
    for name in ("transformer_capacity", "ups_capacity", "grid_firm_capacity"):
        signs = {expected_headroom_kw(d)[name] > 0 for d in intakes}
        assert signs == {True, False}, (
            f"{name}: the draws never produced both surplus and shortfall"
        )
    racks = {
        c["racks_permitted"]
        for v in out.values()
        for s in v["result"]["scenarios"]
        for c in s["constraints"]
        if c["id"] == "transformer_capacity"
    }
    assert len(racks) >= 15, racks


def test_every_draw_is_answered(run_all):
    _, out = run_all
    assert {v["status"] for v in out.values()} == {"ok"}, {
        k: v for k, v in out.items() if v["status"] != "ok"
    }


def test_the_product_agrees_with_the_independent_headroom_on_every_draw(run_all):
    intakes, out = run_all
    bad = {i: check_screen(d, out[str(i)]["result"]) for i, d in enumerate(intakes)}
    bad = {i: p for i, p in bad.items() if p}
    assert not bad, (
        f"{len(bad)} of {DRAWS} draws disagree, e.g. draw {next(iter(bad))}: {next(iter(bad.values()))[:3]}"
    )


def test_the_harness_catches_a_corrupted_real_response(run_all):
    """Mutation on real-shaped data: nudge one rack count and one headroom in a genuine response; both must be flagged."""
    intakes, out = run_all
    i = next(
        i for i, d in enumerate(intakes) if expected_headroom_kw(d)["grid_firm_capacity"] > 3000
    )
    good = out[str(i)]["result"]
    assert check_screen(intakes[i], good) == []
    racks = copy.deepcopy(good)
    next(c for c in racks["scenarios"][0]["constraints"] if c["id"] == "grid_firm_capacity")[
        "racks_permitted"
    ] += 1
    assert check_screen(intakes[i], racks)
    kw = copy.deepcopy(good)
    c = next(c for c in kw["scenarios"][0]["constraints"] if c["id"] == "grid_firm_capacity")
    c["basis"] = c["basis"].replace(c["basis"].split(" ")[0], "1,000", 1)
    assert check_screen(intakes[i], kw)
