"""Procurement and schedule invariants on the real product, derived from the rules a buyer relies on, not from the product's code.

Bids: an offer past its validity must not win; prices in different currencies must not be ranked as if comparable. An offer's own figures are held to
the specification's numeric 'shall' minimum (the product says so in its answer), so the offers here answer that minimum, read from the specification
the supplier is sent, and the one test of that rule offers less and must lose.
Dates: a stated date must lie inside its own stated range (low <= weeks <= high)."""

from __future__ import annotations

import copy
import re

import pytest

from ._product_helpers import mutated, require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


_SPEC: dict = {}


def _spec():
    if not _SPEC:
        _SPEC.update(run("/v1/spec", {"intake": mutated(lambda d: None)}))
    return _SPEC


def specified_duty() -> int:
    """The continuous ampacity the specification's 'shall' asks for, read from the document the supplier is sent (its row: 'Busway continuous ampacity',
    'shall', '1,990 A (E0)'), not from the product's evaluation of a bid."""
    m = re.search(
        r"Busway continuous ampacity</td><td>shall</td><td>([\d,]+) A", _spec()["document"]
    )
    assert m, "the specification no longer states a numeric busway ampacity minimum in its table"
    return int(m.group(1).replace(",", ""))


@pytest.fixture(scope="module")
def template():
    return _spec()["response_template"]


def bid(template, supplier, capex, lead, *, valid_until="2026-12-31", currency="EUR", duty=None):
    d = copy.deepcopy(template)
    d.update(supplier=supplier, received_on="2026-10-20", valid_until=valid_until)
    if currency is None:
        d.pop("currency", None)
    else:
        d["currency"] = currency
    d["values"].update(
        capex_eur=capex,
        lead_time_weeks=lead,
        install_weeks=4,
        outage_hours_per_unit=8,
        duty_at_site_conditions=specified_duty() if duty is None else duty,
        validity_days=60,
    )
    d["compliance"] = {k: "C" for k in d["compliance"]}
    return d


def rank(template, bids, date="2026-10-27"):
    out = run("/v1/bids", {"intake": mutated(lambda d: None), "responses": bids}, report_date=date)
    return {r["supplier"]: r for r in out["ranked"]}, out["leading"]


def test_an_expired_offer_is_disqualified_and_cannot_lead(template):
    by, leading = rank(
        template,
        [
            bid(template, "A-valid", 400000, 20),
            bid(template, "B-expired", 100000, 20, valid_until="2026-10-26"),
        ],
    )
    assert by["B-expired"]["disqualified"] and not by["A-valid"]["disqualified"]
    assert leading == "A-valid"


def test_an_offer_below_the_specified_minimum_is_not_compliant_and_cannot_lead(template):
    """The product holds a bid's own figures to the specification's numeric 'shall': a duty one ampere under the minimum is not compliant, whatever
    code the bid declared, so the dearer offer that meets it leads. The control for the fixtures above, which offer exactly the minimum."""
    by, leading = rank(
        template,
        [
            bid(template, "A-short", 100000, 20, duty=specified_duty() - 1),
            bid(template, "B-meets", 400000, 20),
        ],
    )
    assert not by["A-short"]["compliant"] and by["B-meets"]["compliant"], by
    assert leading == "B-meets"


def test_validity_runs_through_the_report_date_inclusive(template):
    today = [
        bid(template, "C-today", 100000, 20, valid_until="2026-10-27"),
        bid(template, "A", 400000, 20),
    ]
    assert not rank(template, today, "2026-10-27")[0]["C-today"]["disqualified"]
    assert rank(template, today, "2026-10-28")[0]["C-today"]["disqualified"]


def test_an_offer_in_another_currency_is_not_ranked_as_if_comparable(template):
    """Regression for GridForge-AI#106. Baseline 618b728 FAILED this (USD 100,000 led EUR 400,000); candidate e739233 (PR #108) PASSES.

    EUR 400,000 and USD 100,000 are not comparable without an exchange rate, and the engine holds none (its own evidence path says so).
    The cheaper-looking USD number must not lead; the offer must be disqualified, or flagged not comparable."""
    by, leading = rank(
        template,
        [bid(template, "A-eur", 400000, 20), bid(template, "B-usd", 100000, 20, currency="USD")],
    )
    assert leading != "B-usd", "a USD figure was ranked against EUR as a raw number"
    assert by["B-usd"]["disqualified"] or any("currency" in n.lower() for n in by["B-usd"]["notes"])


def test_other_offers_still_rank_when_one_is_in_another_currency(template):
    """The fix must not simply refuse everything: the comparable offers keep their order and the leader is the best of them."""
    by, leading = rank(
        template,
        [
            bid(template, "A-eur-dear", 400000, 20),
            bid(template, "B-usd", 100000, 20, currency="USD"),
            bid(template, "C-eur-cheap", 300000, 20),
            bid(template, "D-jpy", 800000, 20, currency="JPY"),
        ],
    )
    assert leading == "C-eur-cheap"
    assert not by["C-eur-cheap"]["disqualified"] and not by["A-eur-dear"]["disqualified"]
    assert by["B-usd"]["disqualified"] and by["D-jpy"]["disqualified"]
    assert by["C-eur-cheap"]["rank"] < by["A-eur-dear"]["rank"]


def test_a_foreign_currency_offer_is_not_the_only_leader(template):
    _, leading = rank(template, [bid(template, "B-usd", 100000, 20, currency="USD")])
    assert leading != "B-usd"


@pytest.mark.parametrize("weeks", [36, 44, 160, 200, 300])
def test_a_supplied_transformer_lead_time_lies_inside_its_own_stated_range(weeks):
    d = mutated(lambda x: x["transformers"][0].update(replacement_lead_time_weeks=weeks))
    out = run("/v1/compare", {"intake": d})
    bad = []
    for s in out["scenarios"]:
        t = s["time_to_power"]
        if t["status"] == "dated" and not (t["low"] <= t["weeks"] <= t["high"]):
            bad.append(f"{s['id']}: {t['weeks']} weeks outside [{t['low']}, {t['high']}]")
    assert not bad, "\n".join(bad)
