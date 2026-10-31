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


#: sha256 of the official Apache License 2.0 text from https://www.apache.org/licenses/LICENSE-2.0.txt (11,358 bytes).
APACHE_2_0_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def test_the_licence_is_the_official_apache_2_0_text_and_the_metadata_says_so():
    import hashlib

    assert hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest() == APACHE_2_0_SHA256, (
        "LICENSE differs from the official Apache-2.0 text"
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"] == "Apache-2.0" and project["license-files"] == ["LICENSE"]
    assert not any(c.startswith("License ::") for c in project.get("classifiers", [])), (
        "PEP 639: no License classifier with an expression"
    )


def test_ci_runs_the_installed_wheel_not_only_the_source_tree():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "twine check --strict" in ci, "the built metadata is checked the way PyPI will read it"
    assert "site-packages" in ci and 'gridforge-stress" validate-fixtures' in ci


def test_product_integration_accepts_only_a_full_commit_sha_and_checks_what_it_got():
    wf = (ROOT / ".github" / "workflows" / "product-integration.yml").read_text()
    assert "[0-9a-f]{40}" in wf, "a branch or tag is not a product identity"
    assert 'test "$(git -C GridForge-AI rev-parse HEAD)" = "$PRODUCT_REF"' in wf
    uses = [ln.strip() for ln in wf.splitlines() if "${{ inputs." in ln]
    assert all(ln.startswith(("PRODUCT_REF:", "ref:")) for ln in uses), (
        uses
    )  # never interpolated into a shell script


def test_ci_asserts_the_problem_register_is_in_the_wheel():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "_data/configs/problems.json" in ci and "_data/schemas/problem.schema.json" in ci
