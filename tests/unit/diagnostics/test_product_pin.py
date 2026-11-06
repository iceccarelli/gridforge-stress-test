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


def test_the_pin_step_validates_the_raw_line_and_later_steps_depend_on_it():
    wf = (ROOT / ".github" / "workflows" / "product-integration.yml").read_text()
    assert "grep -Ex '[0-9a-f]{40}'" in wf, "raw-line validation of the whole line"
    assert "tr -d" not in wf, "no whitespace stripping: a whitespace-split SHA must be rejected"
    assert wf.count("persist-credentials: false") == 2, (
        "harness and product checkouts both drop the token"
    )
    assert wf.count("steps.sync.outcome == 'success'") >= 4, (
        "post-test steps run only after the pin and sync succeeded"
    )
    assert "!cancelled()}}" not in wf.replace(" ", ""), (
        "no step may run after a failed pin without the outcome guard"
    )
    assert "identity-after.json" in wf and "changed during the run" in wf
