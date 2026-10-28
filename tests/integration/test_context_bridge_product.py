"""Bridge against the REAL GridForge-AI screen handler. Opt in with `pytest -m product`."""

from __future__ import annotations

import copy
import json

import pytest

from gridforge_stress import context_bridge as B
from gridforge_stress import mcp_server as M
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import ProductUnavailable, call, claim_violations, product_dir

pytestmark = [pytest.mark.integration, pytest.mark.product]
W = 365.25 / 7


@pytest.fixture(scope="module")
def intake():
    try:
        product_dir()
    except ProductUnavailable as exc:
        pytest.skip(str(exc))
    return json.loads((fixtures_dir() / "product" / "reference_hall_intake.json").read_text())


@pytest.fixture(scope="module")
def screened(intake):
    out = call("screen", {"intake": intake}, tier="client")
    assert out["ok"], out
    return out["result"]


def test_the_product_assumption_is_read_from_the_real_screen_payload(screened):
    a = B.product_grid_assumption(screened)
    assert (a["weeks"], a["band_low_weeks"], a["band_high_weeks"]) == (260.0, 150.0, 520.0)
    assert a["evidence_class"] == "E0_ASSUMPTION" and a["label"] == "grid reinforcement lead time"
    assert "decision horizon" in a["product_risk_note"]
    assert a["years"] == pytest.approx(260 / W, abs=1e-3)


def test_end_to_end_bridge_for_a_german_site_is_scale_reference_only(intake, screened):
    out = B.bridge(screened, intake)
    assert out["comparison"]["position_of_assumption"] == "between_median_and_p75"
    assert (
        out["applicability"]["level"] == "scale_reference_only"
        and out["applicability"]["site_country"] == "DE"
    )
    assert (
        out["product_evidence_class_unchanged"] == "E0_ASSUMPTION"
        and out["evidence"]["level"] == "context_only"
    )


def test_same_intake_in_the_us_becomes_same_country_context_without_changing_the_product_numbers(
    intake,
):
    us = copy.deepcopy(intake)
    us["site"]["country"] = "US"
    res = call("screen", {"intake": us}, tier="client")
    assert res["ok"]
    out = B.bridge(res["result"], us, region="PJM")
    assert out["applicability"]["level"] == "same_country_context"
    assert out["product_assumption"]["weeks"] == 260.0
    assert (
        out["comparison"]["position_of_assumption"] == "between_median_and_p75"
    )  # PJM median 4.893y < 260wk=4.98y < p75 6.45y


def test_public_tier_cannot_be_bridged_so_nothing_is_guessed(intake):
    pub = call("screen", {"intake": intake}, tier="public")
    assert pub["ok"]
    with pytest.raises(B.BridgeError, match="public tier"):
        B.bridge(pub["result"], intake)


def test_the_bridge_leaves_the_products_compare_claims_within_what_context_evidence_supports(
    intake,
):
    out = call("compare", {"intake": intake})["result"]
    assert (
        claim_violations(
            out, B.bridge(call("screen", {"intake": intake})["result"], intake)["evidence"]["level"]
        )
        == []
    )


def test_mcp_context_bridge_tool_runs_the_real_product_and_returns_the_bridge(intake):
    s = M.Server()
    s.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    r = s.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "context_bridge", "arguments": {"intake": intake, "region": "West"}},
        }
    )
    assert r["result"]["isError"] is False
    out = json.loads(r["result"]["content"][0]["text"])
    assert out["schema"] == B.SCHEMA and out["observed_context"]["name"].startswith("region West")
    bad = json.loads(json.dumps(intake))
    bad["grid"]["power_factor"] = 1.5
    r = s.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "context_bridge", "arguments": {"intake": bad}},
        }
    )
    assert (
        r["result"]["isError"] is True
        and "refused the intake" in json.loads(r["result"]["content"][0]["text"])["error"]
    )
