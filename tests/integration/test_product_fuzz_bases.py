"""Every fuzz plan's valid base body must be ANSWERED by the product.

A hostile value is only informative if the handler gets past its first validation step. Two plans (`qualify`, the free public route, and `bids`) once
carried bases the product refused ("too little to say anything useful"; compliance code 'YES'), so every mutation was refused at the door and the
"0 server faults" figure said nothing about those routes. This test fails the moment any plan loses its coverage that way."""

from __future__ import annotations

import pytest

from gridforge_stress.fuzz import default_plans, reference_intake
from gridforge_stress.product_adapter import call_batch

from ._product_helpers import require_product

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module")
def answers():
    require_product()
    plans = [p for p in default_plans(reference_intake()) if p["name"] != "loader"]
    out = call_batch(
        [
            {"id": p["name"], "route": p["route"], "tier": p["tier"], "body": p["base"]}
            for p in plans
        ]
    )
    return plans, out


def test_every_plans_base_body_is_answered_not_refused(answers):
    plans, out = answers
    refused = {p["name"]: out[p["name"]] for p in plans if out[p["name"]]["status"] != "ok"}
    assert not refused, (
        f"these plans are refused before any logic runs, so their fuzz proves nothing: {refused}"
    )


def test_every_route_the_product_serves_for_posts_has_a_plan(answers):
    plans, _ = answers
    routes = {p["route"] for p in plans}
    # /v1/revoke is internal-tier and mutates a ledger, so it is deliberately not fuzzed here (see fuzz.py)
    assert routes >= {
        "/v1/qualify",
        "/v1/estimate",
        "/v1/screen",
        "/v1/compare",
        "/v1/study",
        "/v1/portfolio",
        "/v1/proposal",
        "/v1/deck",
        "/v1/diff",
        "/v1/spec",
        "/v1/bids",
        "/v1/power/assess",
        "/v1/power/deploy/assess",
        "/v1/power/deploy/spec",
        "/v1/power/deploy/bids",
    }, routes
