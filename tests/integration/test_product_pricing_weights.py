"""The unit weights the company publishes must be the ones its engine meters, and its plans must make commercial sense against pay-as-you-go.

PUBLISHED below is an OBSERVATION of the company's own public page, not a fixture: https://timetopower.ai/llms.txt, HTTP 200, 12,364 bytes,
retrieved 2026-10-31T18:20:41Z, sha256 prefix 91a1a8d49024b42f ("Operations the deployed engine serves today (units are the metered weight)" and
the three API plan lines). Only the numbers are recorded. The page is not fetched by this test (the laboratory fetches registered sources only), so a
change to the SITE is not detected here; a change to the ENGINE's weights is, and then the page must be re-read and these constants updated.
"""

from __future__ import annotations

import pytest

from gridforge_stress.fuzz import reference_intake

from ._product_helpers import require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]

#: endpoint -> units per call as published (portfolio: per hall)
PUBLISHED = {
    "/v1/screen": 1,
    "/v1/compare": 1,
    "/v1/study": 5,
    "/v1/portfolio": 1,
    "/v1/diff": 2,
    "/v1/spec": 3,
    "/v1/bids": 2,
    "/v1/power/assess": 5,
    "/v1/power/deploy/assess": 5,
    "/v1/power/deploy/spec": 3,
    "/v1/power/deploy/bids": 2,
    "/v1/proposal": 2,
}
PER_HALL = {"/v1/portfolio"}
#: plan name -> (EUR per month, units per month) as published
PLANS = {"Triage": (900, 600), "Scale": (2900, 2500), "Platform": (7500, 10000)}


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


def estimate(endpoint, halls=1):
    body = {"endpoint": endpoint, "body": {"intake": reference_intake()}, "hall_count": halls}
    return run("/v1/estimate", body, tier="public")


@pytest.mark.parametrize("endpoint", sorted(PUBLISHED))
def test_the_engine_meters_what_the_company_publishes(endpoint):
    assert estimate(endpoint)["units"] == PUBLISHED[endpoint]


@pytest.mark.parametrize("halls", [2, 5, 20])
def test_portfolio_is_metered_per_hall_and_everything_else_is_not(halls):
    for endpoint, units in PUBLISHED.items():
        want = units * halls if endpoint in PER_HALL else units
        assert estimate(endpoint, halls)["units"] == want, (endpoint, halls)


def test_the_estimate_prices_a_unit_consistently():
    prices = {(e["unit_price_cents"], e["currency"]) for e in (estimate(ep) for ep in PUBLISHED)}
    assert len(prices) == 1, prices
    cents, currency = next(iter(prices))
    assert currency == "eur" and cents > 0
    for ep in PUBLISHED:
        e = estimate(ep)
        assert e["estimated_cents"] == e["units"] * cents, ep


def test_every_published_plan_is_cheaper_per_unit_than_pay_as_you_go():
    cents = estimate("/v1/screen")["unit_price_cents"]
    previous = float("inf")
    for name, (eur, units) in PLANS.items():
        per_unit = eur * 100 / units
        assert per_unit < cents, (
            f"{name}: {per_unit:.0f} cents/unit is not below pay-as-you-go {cents}"
        )
        assert per_unit < previous, f"{name}: a bigger plan must cost less per unit"
        previous = per_unit
