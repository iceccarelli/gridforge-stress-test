"""The CI product pin is an exact SHA and the workflow actually uses it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def test_the_pin_is_one_full_lowercase_sha():
    text = (ROOT / ".product-pin").read_text()
    assert re.fullmatch(r"[0-9a-f]{40}\n?", text), text


def test_the_workflow_runs_on_push_and_nightly_against_the_pin_and_gates_on_identity():
    wf = (ROOT / ".github" / "workflows" / "product-integration.yml").read_text()
    for needle in (
        "push:",
        "schedule:",
        "workflow_dispatch:",
        ".product-pin",
        "ref: ${{ steps.pin.outputs.sha }}",
        "gridforge-stress workflow",
        "gridforge-stress fuzz",
        "upload-artifact",
        "dirty",
    ):
        assert needle in wf, needle
    assert "secrets." not in wf and "contents: write" not in wf
