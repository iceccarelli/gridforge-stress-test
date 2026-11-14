"""The LBNL evidence copied into GridForge's engine and website must stay byte-identical to the harness source.

This is a product integration contract, not a general fixture test. It prevents the engine, public website and independent
acceptance harness from silently publishing different cohorts, source dates, workbook hashes or survivorship limits.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import ProductUnavailable, product_dir, product_required

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))


def test_lbnl_queue_context_is_the_same_source_artifact_in_harness_engine_and_website():
    harness = fixtures_dir() / "lbnl_ir_to_cod_context.json"
    product = product_dir()
    engine = product / "gridforge" / "data" / "queue_context.json"
    website = product / "lib" / "queue-evidence.json"

    source_bytes = harness.read_bytes()
    assert engine.read_bytes() == source_bytes, (
        "engine queue context drifted from the stress harness fixture"
    )
    assert website.read_bytes() == source_bytes, (
        "website queue evidence drifted from the stress harness fixture"
    )

    context = json.loads(source_bytes)
    assert context["source_id"] == "lbnl-queued-up"
    assert context["schema"] == "gridforge-stress/queue-context/1"
    assert context["geography"] == "US"
    assert context["population"] == "operational"
    assert context["unit"] == "years"
    assert "NOT a time-to-power for a load" in context["definition"]
    assert any("Survivorship" in limit for limit in context["limits"])

    workbook_sha = context["provenance"]["workbook_sha256"]
    assert workbook_sha == "794582d3281c6a305e9615fcfec3fae9dc85be2165216d33760b677e976a08b6"


def test_lbnl_fixture_manifest_and_website_capacity_snapshot_share_the_same_publisher_workbook():
    product = product_dir()
    fixture = fixtures_dir() / "lbnl_ir_to_cod_context.json"
    manifest = json.loads(
        (fixtures_dir() / "lbnl_ir_to_cod_context.json.manifest.json").read_text()
    )
    context = json.loads(fixture.read_text())
    capacity = json.loads((product / "lib" / "queue-active-capacity.json").read_text())

    fixture_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert manifest["sha256"] == fixture_sha
    assert manifest["byte_count"] == fixture.stat().st_size
    assert manifest["source_id"] == context["source_id"]

    workbook_sha = context["provenance"]["workbook_sha256"]
    assert capacity["provenance"]["workbook_sha256"] == workbook_sha
    assert capacity["as_of"] == "2025-12-31"
    assert capacity["unit"] == "GW"
    assert "not available capacity" in capacity["what_it_is"]
    assert "not a data-centre load connection" in capacity["what_it_is"]
