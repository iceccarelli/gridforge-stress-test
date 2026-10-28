"""MCP -> real GridForge-AI handlers. Opt in with `pytest -m product`."""

from __future__ import annotations

import json

import pytest

from gridforge_stress import mcp_server as M
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import ProductUnavailable, product_dir

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module")
def intake():
    try:
        product_dir()
    except ProductUnavailable as exc:
        pytest.skip(str(exc))
    return json.loads((fixtures_dir() / "product" / "reference_hall_intake.json").read_text())


def call(args):
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
            "params": {"name": "product_call", "arguments": args},
        }
    )
    return r["result"], json.loads(r["result"]["content"][0]["text"])


def test_compare_through_mcp_returns_the_real_product_output_and_passes_the_claim_gate(intake):
    result, out = call(
        {"endpoint": "compare", "body": {"intake": intake}, "evidence_level": "context_only"}
    )
    assert result["isError"] is False
    res = out["product_result"]
    assert res["ok"] and res["result"]["grade"] in ("indicative", "undated")
    ttp = next(s for s in res["result"]["scenarios"] if s["id"] == "full_dlc_btm")["time_to_power"]
    assert (
        ttp["weeks"] == intake["transformers"][0]["replacement_lead_time_weeks"]
        and ttp["date_basis"] == "assumed"
    )
    assert out["claim_violations"] == []


def test_public_tier_screen_through_mcp_has_no_schedule_numbers(intake):
    _, out = call({"endpoint": "screen", "body": {"intake": intake}, "tier": "public"})
    assert (
        out["product_result"]["ok"]
        and "weeks" not in json.dumps(out["product_result"]["result"]).lower()
    )


def test_product_refusals_are_returned_as_data_not_crashes(intake):
    bad = json.loads(json.dumps(intake))
    bad["grid"]["power_factor"] = 1.5
    result, out = call({"endpoint": "compare", "body": {"intake": bad}})
    assert result["isError"] is False and out["product_result"]["ok"] is False
    assert out["product_result"]["error"] == "IntakeError"
