"""The unit weights the company publishes must be the ones its engine meters, and its plans must make commercial sense against pay-as-you-go.

The published figures come from the checksummed record data/fixtures/site_llms_pricing.json (gridforge_stress.site): an observation of the company's own
https://timetopower.ai/llms.txt, HTTP 200, 12,364 bytes, retrieved 2026-10-31T18:20:41Z, a CLAIM and not evidence about the world. The test does not
fetch the site, so a change to the SITE is seen only when the page is read again (`gridforge-stress probe timetopower-site-llms` on a runner with
network); a change to the ENGINE's weights is seen here at once.
"""

from __future__ import annotations

import pytest

from gridforge_stress.fuzz import reference_intake
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.site import load_pricing_record

from ._product_helpers import require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]

RECORD = load_pricing_record(fixtures_dir() / "site_llms_pricing.json")
WEIGHTS = RECORD["weights"]
PUBLISHED = {ep: w["units"] for ep, w in WEIGHTS.items() if not w["free"]}
FREE = sorted(ep for ep, w in WEIGHTS.items() if w["free"])
PER_HALL = {ep for ep, w in WEIGHTS.items() if w["per_hall"]}
#: plan name -> (EUR per month, units per month)
PLANS = {name: (p["eur_per_month"], p["units_per_month"]) for name, p in RECORD["plans"].items()}


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


def estimate(endpoint, halls=1):
    body = {"endpoint": endpoint, "body": {"intake": reference_intake()}, "hall_count": halls}
    return run("/v1/estimate", body, tier="public")


@pytest.mark.parametrize("endpoint", sorted(PUBLISHED))
def test_the_engine_meters_what_the_company_publishes(endpoint):
    assert estimate(endpoint)["units"] == PUBLISHED[endpoint]


@pytest.mark.parametrize("endpoint", FREE)
def test_the_endpoints_the_company_calls_free_are_not_billed(endpoint):
    e = estimate(endpoint)
    assert (e["units"], e["estimated_cents"], e["billable"], e["free"]) == (0, 0, False, True)


def test_the_record_covers_what_it_should():
    """Guards against a vacuous parametrisation: a changed fixture that lost endpoints would otherwise pass by testing fewer."""
    assert (
        len(PUBLISHED) >= 12
        and len(FREE) >= 2
        and {"/v1/portfolio"} == PER_HALL
        and len(PLANS) == 3
    )


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
