"""The company's published pricing claims, parsed strictly. The page text below is SYNTHETIC, written in the real page's format."""

from __future__ import annotations

import copy
import json

import pytest

from gridforge_stress.paths import fixtures_dir
from gridforge_stress.site import (
    SitePricingError,
    build_record,
    load_pricing_record,
    parse_llms_txt,
)

pytestmark = pytest.mark.unit

PAGE = """# Synthetic page in the real format

- `gridforge_qualify` — POST https://engine.example/v1/qualify — free
- `gridforge_screen` — POST https://engine.example/v1/screen — 1 unit
- `gridforge_study` — POST https://engine.example/v1/study — 5 units
- `gridforge_portfolio` — POST https://engine.example/v1/portfolio — 1 unit per hall
- `gridforge_power_deploy_assess` — POST https://engine.example/v1/power/deploy/assess — 5 units

Some other prose that mentions units and €1,000 but is not a plan line.
- API — Small — €900/month, 600 units
- API — Big — €7,500/month, 10,000 units
"""


def swap(old, new, text=PAGE):
    assert old in text
    return text.replace(old, new, 1)


def test_a_page_in_the_real_format_parses_exactly():
    r = parse_llms_txt(PAGE)
    assert r["weights"]["/v1/qualify"] == {"units": 0, "per_hall": False, "free": True}
    assert r["weights"]["/v1/screen"] == {"units": 1, "per_hall": False, "free": False}
    assert r["weights"]["/v1/portfolio"] == {"units": 1, "per_hall": True, "free": False}
    assert r["weights"]["/v1/power/deploy/assess"]["units"] == 5
    assert r["plans"] == {
        "Small": {"eur_per_month": 900, "units_per_month": 600},
        "Big": {"eur_per_month": 7500, "units_per_month": 10000},
    }


def test_prose_that_only_looks_like_a_price_is_ignored():
    assert "Some other prose" not in json.dumps(parse_llms_txt(PAGE))


BAD_PAGES = {
    "duplicate_endpoint": PAGE
    + "- `gridforge_screen` — POST https://engine.example/v1/screen — 2 units\n",
    "tool_does_not_match_path": swap("gridforge_study", "gridforge_survey"),
    "fractional_units": swap("5 units", "2.5 units"),
    "missing_dash_shape": swap("— 1 unit\n", "- 1 unit\n"),
    "word_instead_of_number": swap("5 units", "five units"),
    "plan_not_parseable": swap("€900/month", "900 EUR/month"),
    "plan_zero_units": swap("600 units", "0 units"),
    "duplicate_plan": PAGE + "- API — Small — €100/month, 10 units\n",
    "no_endpoints": "\n".join(ln for ln in PAGE.splitlines() if "gridforge_" not in ln),
    "no_plans": "\n".join(ln for ln in PAGE.splitlines() if "- API" not in ln),
    "priced_endpoint_with_zero_units": swap("— 1 unit\n", "— 0 units\n"),
}


@pytest.mark.parametrize("name", sorted(BAD_PAGES))
def test_a_changed_page_is_refused_not_guessed_at(name):
    with pytest.raises(SitePricingError):
        parse_llms_txt(BAD_PAGES[name])


def test_the_record_is_tied_to_the_exact_bytes():
    raw = PAGE.encode()
    rec = build_record(raw, url="https://x.example/llms.txt", retrieved_at="2026-10-31T00:00:00Z")
    assert rec["provenance"]["bytes"] == len(raw) and len(rec["provenance"]["sha256"]) == 64
    assert rec["evidence_kind"] == "company_claim"
    assert (
        build_record(raw + b" ", url="u", retrieved_at="2026-10-31T00:00:00Z")["provenance"][
            "sha256"
        ]
        != rec["provenance"]["sha256"]
    )


def shipped():
    return json.loads((fixtures_dir() / "site_llms_pricing.json").read_text(encoding="utf-8"))


def test_the_shipped_fixture_loads_and_says_what_was_observed():
    rec = load_pricing_record(fixtures_dir() / "site_llms_pricing.json")
    assert rec["provenance"]["retrieved_at"] == "2026-10-31T18:20:41Z"
    assert (
        rec["weights"]["/v1/study"]["units"] == 5
        and rec["weights"]["/v1/portfolio"]["per_hall"] is True
    )
    assert len(rec["weights"]) == 14 and list(rec["plans"]) == ["Triage", "Scale", "Platform"]


def edit(fn):
    d = copy.deepcopy(shipped())
    fn(d)
    return d


CORRUPT = {
    "wrong_schema": lambda d: d.update(schema="x/1"),
    "claim_promoted_to_measurement": lambda d: d.update(evidence_kind="measurement"),
    "wrong_source_id": lambda d: d.update(source_id="eia-epm"),
    "sha_not_hex": lambda d: d["provenance"].update(sha256="zz"),
    "bytes_zero": lambda d: d["provenance"].update(bytes=0),
    "bytes_bool": lambda d: d["provenance"].update(bytes=True),
    "retrieved_at_not_utc": lambda d: d["provenance"].update(retrieved_at="2026-10-31"),
    "weights_empty": lambda d: d.update(weights={}),
    "free_but_priced": lambda d: d["weights"]["/v1/qualify"].update(units=3),
    "priced_but_flagged_free": lambda d: d["weights"]["/v1/screen"].update(free=True),
    "negative_units": lambda d: d["weights"]["/v1/screen"].update(units=-1),
    "weight_extra_key": lambda d: d["weights"]["/v1/screen"].update(note="x"),
    "bigger_plan_not_cheaper_per_unit": lambda d: d["plans"]["Platform"].update(
        eur_per_month=90000
    ),
    "plan_zero": lambda d: d["plans"]["Scale"].update(units_per_month=0),
    "plan_missing_key": lambda d: d["plans"]["Scale"].pop("eur_per_month"),
}


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_each_corruption_of_the_record_is_refused(tmp_path, name):
    p = tmp_path / "x.json"
    p.write_text(json.dumps(edit(CORRUPT[name])))
    with pytest.raises(SitePricingError):
        load_pricing_record(p)
