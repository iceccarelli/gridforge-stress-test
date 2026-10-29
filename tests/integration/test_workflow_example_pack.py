"""The customer journey on the real product: intake -> compare -> screen -> what-ifs, judged independently.

Runs the product's real handlers (nothing stubbed). The example pack is a synthetic hall that hosts racks under a
hybrid-DLC architecture today while full DLC needs facility-water relief, so the journey has a real story to tell.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gridforge_stress import workflow as W
from gridforge_stress.cli import main
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import ProductUnavailable, product_dir, product_required

pytestmark = [pytest.mark.integration, pytest.mark.product]

PACK = fixtures_dir() / "product" / "nordic_ai_ready_intake.json"


@pytest.fixture(scope="module")
def product() -> Path:
    try:
        return product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))
        raise


@pytest.fixture(scope="module")
def pack_record(product):
    return W.run(json.loads(PACK.read_text()))


def test_the_example_pack_passes_every_check_on_the_real_product(pack_record):
    failing = [(c.id, c.detail) for c in pack_record.checks if c.outcome != W.PASS]
    assert not failing, failing
    assert len(pack_record.checks) >= 13 and pack_record.exit_code == 0
    assert pack_record.product["code_sha256"], "the record names the engine build it judged"


def test_the_pack_tells_a_real_story_not_a_zero(product):
    from gridforge_stress.product_adapter import call

    out = call("compare", {"intake": json.loads(PACK.read_text())})["result"]
    by = {s["id"]: s for s in out["scenarios"]}
    assert by["retained_air"]["after_ladder"]["racks"] == 0, (
        "an air-cooled hall cannot host this platform"
    )
    assert by["hybrid_dlc"]["as_found"]["racks"] > 0, (
        "hybrid DLC hosts racks as found on installed CDUs"
    )
    assert by["full_dlc"]["as_found"]["racks"] == 0, (
        "full DLC is blocked by facility-water temperature as found"
    )
    assert by["full_dlc_btm"]["after_ladder"]["racks"] > by["full_dlc"]["after_ladder"]["racks"]
    assert out["grade"] == "indicative" and out["caveats"], (
        "E0 lead times never become a screening-grade claim"
    )


@pytest.mark.parametrize("name", ["dublin_hall", "london_hall", "nordic_hall", "reference_hall"])
def test_every_example_hall_the_product_ships_passes_the_oracle(product, name):
    intake = json.loads((product / "examples" / "intake" / f"{name}.json").read_text())
    rec = W.run(intake)
    assert [(c.id, c.detail) for c in rec.checks if c.outcome != W.PASS] == []


def test_the_cli_writes_a_machine_readable_decision_record(product, tmp_path, capsys):
    out = tmp_path / "record.json"
    assert main(["workflow", str(PACK), "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["schema"] == "gridforge-stress/workflow-record/1"
    assert doc["summary"]["FAIL"] == doc["summary"]["BLOCKED"] == doc["summary"]["UNCHECKED"] == 0
    assert doc["limitations"] and doc["open_questions"]
    assert "PASS" in capsys.readouterr().out


def test_omitting_the_date_setting_lead_time_is_never_silent(product):
    """Regression for a defect this oracle found: the omitted transformer lead time silently became a 160-week
    placeholder that sets the date, with no data gap and no caveat. Fixed in the product's PR."""
    intake = json.loads(PACK.read_text())
    del intake["transformers"][0]["replacement_lead_time_weeks"]
    from gridforge_stress.product_adapter import call

    out = call("compare", {"intake": intake})["result"]
    assert any("not supplied" in c and "transformer" in c.lower() for c in out["caveats"])
