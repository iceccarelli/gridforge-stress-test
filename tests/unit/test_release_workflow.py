"""Publishing gridforge-stress stays a deliberate, tokenless act, and never claims a licence the owner has not chosen."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text()


def test_publishing_is_a_manual_dispatch_only():
    head = WORKFLOW.split("jobs:")[0]
    assert re.search(r"^on:\s*\n\s+workflow_dispatch:", head, re.M)
    assert "push:" not in head and "pull_request" not in head and "schedule" not in head


def test_publishing_uses_trusted_publishing_and_no_stored_credential():
    assert "id-token: write" in WORKFLOW and "pypa/gh-action-pypi-publish" in WORKFLOW
    assert re.search(r"environment:\s*pypi", WORKFLOW)
    for forbidden in ("PYPI_API_TOKEN", "TWINE_PASSWORD", "password:", "pypi-Ag", "secrets."):
        assert forbidden not in WORKFLOW, (
            f"credential reference in the publish workflow: {forbidden}"
        )


def test_publishing_refuses_without_a_licence_and_a_typed_version_and_a_new_version():
    assert "LICENSE" in WORKFLOW and "No LICENSE file" in WORKFLOW
    assert "CONFIRM" in WORKFLOW and "${{ inputs.confirm }}" in WORKFLOW
    assert "404" in WORKFLOW, "the run must check that the version is not already on PyPI"
    # the operator's input reaches the shell only through the environment, never interpolated into a script
    run_blocks = re.findall(r"run: \|\n((?:\s{10,}.*\n)+)", WORKFLOW)
    assert not any("inputs.confirm" in block for block in run_blocks)


def test_no_licence_is_claimed_without_a_licence_file():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    claims = "license" in project or any(
        c.startswith("License ::") for c in project.get("classifiers", [])
    )
    has_file = any((ROOT / f).exists() for f in ("LICENSE", "LICENSE.txt", "LICENSE.md"))
    assert not claims or has_file


def test_ci_runs_the_installed_wheel_not_only_the_source_tree():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "twine check --strict" in ci, "the built metadata is checked the way PyPI will read it"
    assert "site-packages" in ci and 'gridforge-stress" validate-fixtures' in ci
