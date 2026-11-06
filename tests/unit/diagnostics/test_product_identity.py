"""The product identity is exact or it is nothing: unknown is None, never "clean" and never a guess."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from gridforge_stress import doctor as D
from gridforge_stress import workflow as W
from gridforge_stress.fuzz import FuzzReport, format_report
from gridforge_stress.product_adapter import ProductRefused, ProductUnavailable

pytestmark = pytest.mark.unit

GIT_BIN = shutil.which("git") or "git"
GIT = [
    GIT_BIN,
    "-c",
    "user.email=t@example.invalid",
    "-c",
    "user.name=t",
    "-c",
    "commit.gpgsign=false",
]


def git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, throwaway tmp repo
        [*GIT, "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-q", "-m", "first")
    return root


def test_clean_repo_is_dirty_false_with_a_40_char_lowercase_sha(repo):
    ident = D.product_identity(repo)
    assert re.fullmatch(r"[0-9a-f]{40}", ident["sha"])
    assert ident == {"sha": git(repo, "rev-parse", "HEAD"), "branch": "main", "dirty": False}


def test_an_untracked_file_makes_it_dirty(repo):
    (repo / "new.txt").write_text("x")
    assert D.product_identity(repo)["dirty"] is True


def test_a_modified_tracked_file_makes_it_dirty(repo):
    (repo / "a.txt").write_text("changed\n")
    assert D.product_identity(repo)["dirty"] is True


def test_detached_head_keeps_the_sha_with_no_branch(repo):
    git(repo, "checkout", "-q", "--detach")
    ident = D.product_identity(repo)
    assert ident["branch"] is None and ident["dirty"] is False and ident["sha"]


def test_a_directory_without_git_has_no_identity(tmp_path):
    (tmp_path / "export").mkdir()
    assert D.product_identity(tmp_path / "export") == {"sha": None, "branch": None, "dirty": None}


def test_a_git_archive_export_has_no_identity(repo, tmp_path):
    out = tmp_path / "exported"
    out.mkdir()
    archive = tmp_path / "head.tar"
    git(repo, "archive", "-o", str(archive), "HEAD")
    with tarfile.open(archive) as tf:
        tf.extractall(out, filter="data")
    assert (out / "a.txt").is_file()
    assert D.product_identity(out) == {"sha": None, "branch": None, "dirty": None}


def test_an_export_nested_inside_another_repo_does_not_borrow_that_repos_identity(repo):
    nested = repo / "vendor" / "GridForge-AI"
    nested.mkdir(parents=True)
    (nested / "f.txt").write_text("x")
    assert D.product_identity(repo)["sha"]  # the enclosing repo does have one
    assert D.product_identity(nested) == {"sha": None, "branch": None, "dirty": None}


@pytest.mark.parametrize("bad", ["garbage", "", "ABCDEF" * 7, "a" * 39, "a" * 41, "g" * 40])
def test_malformed_rev_parse_output_is_rejected(monkeypatch, repo, bad):
    real = D._git

    def fake(path, *args):
        return bad if args[:2] == ("rev-parse", "HEAD") else real(path, *args)

    monkeypatch.setattr(D, "_git", fake)
    assert D.product_identity(repo) == {"sha": None, "branch": None, "dirty": None}


@pytest.mark.parametrize("failing", [("status", "--porcelain"), ("branch", "--show-current")])
def test_any_single_git_failure_voids_the_whole_identity(monkeypatch, repo, failing):
    real = D._git
    monkeypatch.setattr(D, "_git", lambda p, *a: None if a[:2] == failing else real(p, *a))
    assert D.product_identity(repo) == {"sha": None, "branch": None, "dirty": None}


def test_git_missing_from_the_machine_is_unknown_not_clean(monkeypatch, repo):
    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(D.subprocess, "run", boom)
    assert D.product_identity(repo) == {"sha": None, "branch": None, "dirty": None}


def test_ambient_git_dir_variables_cannot_redirect_the_identity(monkeypatch, repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    assert D.product_identity(other) == {"sha": None, "branch": None, "dirty": None}


def test_format_identity_says_unknown_plainly():
    text = D.format_identity({"sha": None, "branch": None, "dirty": None}, "/p")
    assert "sha=UNKNOWN" in text and "dirty=UNKNOWN" in text and "dirty=False" not in text


# --- where the identity is printed ---------------------------------------------------------------------------------------------------


def test_doctor_reports_identity_and_runs_the_import_check(repo):
    # a dirty tree with the marker and a trivially importable server
    from tests.unit.diagnostics.test_unavailable_causes import make_fake_product

    make_fake_product(repo, server_prefix="")
    rep = D.run_doctor({D.PRODUCT_ENV: str(repo)})
    by = {c["check"]: c for c in rep["checks"]}
    assert by["product_checkout"]["status"] == "pass"
    assert f"sha={git(repo, 'rev-parse', 'HEAD')}" in by["product_checkout"]["detail"]
    assert "dirty=True" in by["product_checkout"]["detail"]
    assert by["product_import"]["status"] == "pass"
    assert rep["product_identity"]["dirty"] is True


def test_the_workflow_record_carries_the_identity_additively(monkeypatch, repo):
    monkeypatch.setenv(D.PRODUCT_ENV, str(repo))
    (repo / "gridforge" / "envelope").mkdir(parents=True)
    (repo / "gridforge" / "envelope" / "time_to_power.py").write_text("")
    rec = W.run({"x": 1}, api=lambda *a, **k: {"ok": False, "message": "stop"})
    doc = json.loads(json.dumps(rec.to_json()))
    assert doc["schema"] == "gridforge-stress/workflow-record/1"
    ident = doc["product_identity"]
    assert ident["root"] == str(repo) and ident["dirty"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", ident["sha"])
    assert (
        W.Record("a", "d").to_json()["product_identity"] == {}
    )  # a hand-built record claims nothing


def test_the_fuzz_report_header_names_the_product():
    rep = FuzzReport(
        calls=1,
        ok=1,
        clean=0,
        non_clean=(),
        slow=(),
        product={"root": "/p", "sha": "a" * 40, "branch": "main", "dirty": False},
    )
    first = format_report(rep)[0]
    assert first == f"product: /p sha={'a' * 40} branch=main dirty=False"
    bare = format_report(FuzzReport(calls=1, ok=1, clean=0, non_clean=(), slow=()))
    assert bare[0] == "product: identity not recorded"


def test_mcp_product_call_output_carries_the_identity(monkeypatch, repo):
    from gridforge_stress import mcp_server as M
    from gridforge_stress import product_adapter as PA

    monkeypatch.setenv(D.PRODUCT_ENV, str(repo))
    (repo / "gridforge" / "envelope").mkdir(parents=True)
    (repo / "gridforge" / "envelope" / "time_to_power.py").write_text("")
    monkeypatch.setattr(PA, "call", lambda *a, **k: {"ok": True, "result": {}})
    out = M.tool_product_call({"endpoint": "compare", "body": {}})
    assert out["product_identity"]["root"] == str(repo)
    assert out["product_identity"]["dirty"] is True and out["product_identity"]["sha"]


def test_pytest_header_names_the_checkout(monkeypatch, repo):
    from tests.conftest import pytest_report_header

    monkeypatch.setenv(D.PRODUCT_ENV, str(repo))
    (repo / "gridforge" / "envelope").mkdir(parents=True)
    (repo / "gridforge" / "envelope" / "time_to_power.py").write_text("")
    [line] = pytest_report_header(None)  # type: ignore[arg-type]
    assert line.startswith("gridforge product: ") and "dirty=True" in line
    monkeypatch.setenv(D.PRODUCT_ENV, str(repo / "nope"))
    [line] = pytest_report_header(None)  # type: ignore[arg-type]
    assert "not found" in line


# --- refusal is not unavailability -----------------------------------------------------------------------------------------------------


def test_product_refused_is_not_a_product_unavailable():
    assert issubclass(ProductRefused, Exception)
    assert not issubclass(ProductRefused, ProductUnavailable)
    assert not isinstance(ProductRefused("no"), ProductUnavailable)
    assert not issubclass(ProductUnavailable, ProductRefused)


WHATIF_INTAKE = {
    "grid": {"firm_capacity_MVA": 30.0, "contracted_MW": 27.0},
    "transformers": [{"units": 1, "replacement_lead_time_weeks": 100}],
}
NO_CHANGE = {"material": False, "racks_delta": 0, "changed_inputs": []}


def refusing(route_refused: str, *, after: int = 0):
    """An api stub that answers the no-change diff and then refuses `route_refused` once `after` calls have been answered."""
    seen = {"n": 0}

    def api(route, body, report_date="2026-10-27"):
        seen["n"] += 1
        if route == route_refused and seen["n"] > after:
            return {"ok": False, "message": "no capacity data"}
        return {"ok": True, "result": NO_CHANGE}

    return api


def test_a_refused_diff_is_a_fail_on_its_own_id_and_the_rest_are_unchecked():
    res = W.evaluate_whatifs(WHATIF_INTAKE, {}, refusing("diff"))
    assert [(c.id, c.outcome) for c in res] == [
        ("whatif.identity", W.FAIL),
        ("whatif.relief", W.UNCHECKED),
        ("whatif.starved", W.UNCHECKED),
        ("whatif.determinism", W.UNCHECKED),
        ("whatif.missing_lead_time", W.UNCHECKED),
    ]
    assert "product refused: diff refused: no capacity data" in res[0].detail
    assert "whatif.identity was refused" in res[1].detail


def test_a_refused_compare_of_a_whatif_is_a_fail_on_that_whatif():
    res = W.evaluate_whatifs(WHATIF_INTAKE, {}, refusing("compare"))
    got = {c.id: c.outcome for c in res}
    assert got["whatif.identity"] == W.PASS and got["whatif.relief"] == W.FAIL
    assert got["whatif.starved"] == got["whatif.determinism"] == W.UNCHECKED
    assert "compare refused a what-if" in next(c for c in res if c.id == "whatif.relief").detail
    assert "product.available" not in got


def test_a_whatif_refusal_is_a_failed_record_never_blocked_and_never_a_pass():
    rec = W.Record("x", "d")
    rec.checks += W.evaluate_whatifs(WHATIF_INTAKE, {}, refusing("diff"))
    assert rec.exit_code == 1 and rec.counts()[W.BLOCKED] == 0
    assert not any(c.id == "product.available" for c in rec.checks)


def test_an_unreachable_product_is_still_blocked_product_available():
    def api(*_a, **_k):
        raise ProductUnavailable("no checkout")

    rec = W.run(WHATIF_INTAKE, api=api)
    assert [(c.id, c.outcome) for c in rec.checks] == [("product.available", W.BLOCKED)]
    assert rec.exit_code == 3
