"""The behind-the-meter deploy bids route (/v1/power/deploy/bids) must not rank offers in different currencies as if comparable.

Same rule as /v1/bids (GridForge-AI#106): the price field is capex_eur and the engine holds no exchange rate."""

from __future__ import annotations

import copy

import pytest

from gridforge_stress.fuzz import default_plans, reference_intake

from ._product_helpers import require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


BASE = next(p for p in default_plans(reference_intake()) if p["name"] == "power_deploy_bids")[
    "base"
]


def bid(supplier, capex, *, currency="EUR", lead=44):
    d = copy.deepcopy(BASE["responses"][0])
    d.update(supplier=supplier, currency=currency)
    d["values"].update(capex_eur=capex, lead_time_weeks=lead)
    return d


def ranked(*bids):
    out = run("/v1/power/deploy/bids", {**BASE, "responses": list(bids)})
    return {r["supplier"]: r for r in out["ranked"]}, out


def test_the_same_price_in_euros_ranks_by_price():
    by, _ = ranked(bid("A-dear", 25_000_000), bid("B-cheap", 5_000_000))
    assert by["B-cheap"]["rank"] < by["A-dear"]["rank"]


def test_an_offer_in_another_currency_is_not_ranked_first_on_a_raw_number():
    """USD 5,000,000 is not comparable with EUR 25,000,000 without an exchange rate. It must not take rank 1 on its face value."""
    by, _ = ranked(bid("A-eur", 25_000_000), bid("B-usd", 5_000_000, currency="USD"))
    flagged = by["B-usd"]["disqualified"] or any(
        "currency" in n.lower() for n in by["B-usd"]["notes"]
    )
    assert flagged or by["B-usd"]["rank"] > by["A-eur"]["rank"], (
        "a USD figure outranked EUR on its face value, with no note"
    )


def test_comparable_offers_still_rank_when_one_offer_is_in_another_currency():
    by, _ = ranked(
        bid("A-eur-dear", 25_000_000),
        bid("B-usd", 5_000_000, currency="USD"),
        bid("C-eur-cheap", 20_000_000),
        bid("D-jpy", 800_000, currency="JPY"),
    )
    assert by["C-eur-cheap"]["rank"] < by["A-eur-dear"]["rank"]
    assert not by["C-eur-cheap"]["disqualified"]
