"""`dirty=False` must mean the files the product child can run are exactly HEAD's. Every hiding place found by review is exercised.

Each tamper test first proves the hiding place works (plain `git status --porcelain` is silent), so the test cannot pass vacuously.
"""

from __future__ import annotations

import os
import py_compile
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gridforge_stress import doctor as D
from gridforge_stress import fuzz
from gridforge_stress import product_adapter as PA
from gridforge_stress import workflow as W
from tests.unit.diagnostics.conftest import git
from tests.unit.diagnostics.test_unavailable_causes import make_fake_product
from tests.unit.test_fuzz import CLEAN, make_product

pytestmark = pytest.mark.unit

UNKNOWN = {"sha": None, "branch": None, "dirty": None}


@pytest.fixture
def product(tmp_path) -> Path:
    """A committed stand-in product (importable server) in its own git repo; clean to begin with."""
    root = make_fake_product(tmp_path / "prod", server_prefix="")
    (root / ".gitignore").write_text("__pycache__/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "product")
    assert D.product_identity(root)["dirty"] is False
    return root


def silent(root: Path) -> None:
    assert git(root, "status", "--porcelain") == "", (
        "the hiding place did not hide anything from plain git status"
    )


def tamper_server(root: Path) -> None:
    with (root / "gridforge" / "api" / "server.py").open("a") as fh:
        fh.write("\nTAMPERED = True\n")


def test_skip_worktree_hides_a_modified_tracked_file_from_status_but_not_from_the_identity(product):
    tamper_server(product)
    git(product, "update-index", "--skip-worktree", "gridforge/api/server.py")
    silent(product)
    assert D.product_identity(product)["dirty"] is True


def test_assume_unchanged_hides_a_modified_tracked_file_from_status_but_not_from_the_identity(
    product,
):
    tamper_server(product)
    git(product, "update-index", "--assume-unchanged", "gridforge/api/server.py")
    silent(product)
    assert D.product_identity(product)["dirty"] is True


def test_repo_config_cannot_hide_untracked_files(product):
    git(product, "config", "status.showUntrackedFiles", "no")
    (product / "gridforge" / "api" / "evil.py").write_text("X = 1\n")
    silent(product)
    assert D.product_identity(product)["dirty"] is True


def test_a_users_global_gitconfig_cannot_hide_untracked_files(product, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[status]\n\tshowUntrackedFiles = no\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    (product / "gridforge" / "api" / "evil.py").write_text("X = 1\n")
    assert (
        git(product, "status", "--porcelain") == ""
    )  # the user's config really hides it from plain git
    assert D.product_identity(product)["dirty"] is True


def test_core_ignorestat_cannot_hide_a_modification(product):
    git(product, "config", "core.ignoreStat", "true")
    git(product, "update-index", "--refresh")
    tamper_server(product)
    assert D.product_identity(product)["dirty"] is True


def test_an_importable_file_hidden_by_info_exclude_is_dirty(product):
    (product / ".git" / "info" / "exclude").write_text("shadow.py\n")
    (product / "shadow.py").write_text("print('shadows the standard library')\n")
    silent(product)
    assert D.product_identity(product)["dirty"] is True


def test_a_gitignored_file_under_the_package_directory_is_dirty(product):
    (product / ".gitignore").write_text("__pycache__/\n*.json\n")
    git(product, "commit", "-q", "-am", "ignore json")
    (product / "gridforge" / "api" / "table.json").write_text("{}")
    silent(product)
    assert D.product_identity(product)["dirty"] is True


def test_a_sourceless_pyc_beside_the_sources_is_dirty(product):
    legacy = product / "gridforge" / "api" / "legacy.pyc"
    (product / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    git(product, "commit", "-q", "-am", "ignore pyc")
    src = product / "legacy_src.py"
    src.write_text("V = 1\n")
    py_compile.compile(str(src), cfile=str(legacy))
    src.unlink()
    silent(product)
    assert D.product_identity(product)["dirty"] is True


def test_unrelated_ignored_files_do_not_make_the_identity_dirty(product):
    (product / ".gitignore").write_text("__pycache__/\nnode_modules/\n")
    git(product, "commit", "-q", "-am", "ignore node_modules")
    vendored = product / "node_modules" / "flatted" / "python"
    vendored.mkdir(parents=True)
    (vendored / "flatted.py").write_text("X = 1\n")
    pyc = product / "gridforge" / "api" / "__pycache__"
    pyc.mkdir()
    (pyc / "server.cpython-313.pyc").write_bytes(b"\0")
    assert D.product_identity(product)["dirty"] is False


def test_a_stale_unchecked_hash_pyc_in_a_gitignored_pycache_is_never_read_by_the_child(product):
    """The reviewer's worst case: status is silent, the .pyc is ignored, and a default interpreter would run it instead of the source."""
    server = product / "gridforge" / "api" / "server.py"
    stale_src = product / "stale_source.py"
    stale_src.write_text(
        "def _invoke(route, handler, body, tier):\n    return {'ran': 'STALE-PYC'}\n"
    )
    cfile = (
        server.parent
        / "__pycache__"
        / f"server.cpython-{sys.version_info[0]}{sys.version_info[1]}.pyc"
    )
    cfile.parent.mkdir(exist_ok=True)
    py_compile.compile(
        str(stale_src),
        cfile=str(cfile),
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    stale_src.unlink()
    silent(product)
    # proof the hiding place is real: an interpreter WITHOUT the harness's pycache_prefix executes the stale bytecode
    code = "import sys; sys.path.insert(0, sys.argv[1]); from gridforge.api import server; print(server._invoke('r', None, {}, 't'))"
    plain = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-B", "-c", code, str(product)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "STALE-PYC" in plain.stdout
    # the harness's child reads the source that HEAD describes
    assert PA.call("compare", {"x": 1}, root=product) == {"ok": True, "result": {"echo": {"x": 1}}}
    assert D.product_identity(product)["dirty"] is False


# --- the interpreter command lines -------------------------------------------------------------------------------------------------


def capture_run(monkeypatch, module, stdout="{}"):
    seen: list[list[str]] = []

    def fake(argv, **kw):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake)
    return seen


def assert_isolated_no_bytecode_argv(argv: list[str]) -> None:
    assert argv[0] == sys.executable
    assert "-I" in argv and "-B" in argv
    i = argv.index("-X")
    assert argv[i + 1].startswith("pycache_prefix=") and len(argv[i + 1]) > len("pycache_prefix=")


def test_every_product_child_runs_isolated_without_bytecode_and_with_a_private_pycache(
    monkeypatch, tmp_path
):
    seen = capture_run(monkeypatch, PA)
    PA.call("compare", {}, root=tmp_path)
    PA.call_batch([], root=tmp_path)
    assert len(seen) == 2
    for argv in seen:
        assert_isolated_no_bytecode_argv(argv)
    prefixes = {a[a.index("-X") + 1] for a in seen}
    assert len(prefixes) == 2, "each child gets its own fresh prefix"
    assert not any(Path(p.split("=", 1)[1]).exists() for p in prefixes), (
        "the prefix is removed afterwards"
    )


def test_the_fuzz_child_gets_the_same_command_line(monkeypatch, tmp_path):
    counts = {"calls": 0, "ok": 0, "clean": 0}
    import json

    seen = capture_run(
        monkeypatch,
        PA,
        stdout=json.dumps({"counts": counts, "non_clean": [], "slow": [], "per_plan": {}}),
    )
    fuzz.run_fuzz([], root=tmp_path)
    assert len(seen) == 1
    assert_isolated_no_bytecode_argv(seen[0])


def test_the_doctor_import_check_gets_the_same_command_line(monkeypatch, tmp_path):
    seen = capture_run(monkeypatch, D)
    assert D.product_import_check(tmp_path)[0] == "pass"
    assert len(seen) == 1
    assert_isolated_no_bytecode_argv(seen[0])


# --- the git subprocess ------------------------------------------------------------------------------------------------------------


def test_git_gets_a_minimal_environment_and_no_user_or_system_config(monkeypatch):
    for k, v in {
        "OPENEI_API_KEY": "s3cret",
        "GRIDFORGE_API_KEY": "s3cret",
        "AWS_SECRET_ACCESS_KEY": "s3cret",
        "GIT_DIR": "/elsewhere",
        "GIT_WORK_TREE": "/elsewhere",
        "HOME": "/home/someone",
    }.items():
        monkeypatch.setenv(k, v)
    env = D.git_env()
    assert not [k for k in env if k.startswith("GRIDFORGE_") or "KEY" in k or "OPENEI" in k]
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env and "HOME" not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script")
def test_a_hostile_core_fsmonitor_config_does_not_execute_and_leaks_nothing(
    product, tmp_path, monkeypatch
):
    leak = tmp_path / "leak.txt"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\nenv > '{leak}'\nprintf '\\0'\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    git(product, "config", "core.fsmonitor", str(hook))
    git(product, "config", "core.hooksPath", str(tmp_path))
    monkeypatch.setenv("OPENEI_API_KEY", "s3cret-openei")
    ident = D.product_identity(product)
    assert ident["dirty"] is False and ident["sha"]
    assert not leak.exists(), leak.read_text() if leak.exists() else ""


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX filesystem")
def test_a_non_utf8_branch_name_is_reported_not_a_traceback(product, tmp_path):
    name = os.fsdecode(b"caf\xe9")
    try:
        git(product, "checkout", "-q", "-b", name)
    except subprocess.CalledProcessError:
        pytest.skip("this filesystem or git refuses a non-UTF-8 branch name")
    ident = D.product_identity(product)
    assert ident["sha"] and ident["dirty"] is False and "caf" in ident["branch"]
    rep = D.run_doctor({D.PRODUCT_ENV: str(product)})  # no traceback
    assert next(c for c in rep["checks"] if c["check"] == "product_checkout")["status"] == "pass"
    rec = W.run({"x": 1}, api=lambda *a, **k: {"ok": False, "message": "stop"})
    assert rec.checks[0].outcome == W.FAIL


def test_ambient_git_dir_and_work_tree_cannot_redirect_the_identity(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "elsewhere")
    (other / "o.txt").write_text("o")
    git(other, "add", "o.txt")
    git(other, "commit", "-q", "-m", "other")
    mine, theirs = (
        git(repo, "rev-parse", "HEAD"),
        git(other, "rev-parse", "HEAD"),
    )  # read BEFORE the ambient variables are set
    assert mine != theirs
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert (repo / ".git").exists()
    assert D.product_identity(repo) == {"sha": mine, "branch": "main", "dirty": False}


def test_a_directory_with_an_invalid_dot_git_does_not_borrow_its_parent_repos_identity(repo):
    inner = repo / "vendor" / "GridForge-AI"
    (inner / ".git").mkdir(
        parents=True
    )  # exists, but git does not accept it: it would walk up to `repo`
    assert D._git(inner, "rev-parse", "--show-toplevel") == str(repo.resolve())
    assert D.product_identity(inner) == UNKNOWN


# --- a path that cannot be examined -----------------------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX name-length limit")
def test_a_5000_character_product_dir_is_reported_as_the_cause_not_a_crash(monkeypatch):
    value = "a" * 5000
    env = {D.PRODUCT_ENV: value}
    assert D.product_checkout(env) is None
    assert "File name too long" in D.describe_product(env)
    rep = D.run_doctor(env)
    chk = next(c for c in rep["checks"] if c["check"] == "product_checkout")
    assert chk["status"] == "fail" and "File name too long" in chk["detail"]
    monkeypatch.setenv(D.PRODUCT_ENV, value)
    with pytest.raises(PA.ProductUnavailable, match="File name too long"):
        PA.product_dir()
    assert D.product_identity(value) == UNKNOWN


# --- the fuzz report names the product --------------------------------------------------------------------------------------------


def test_a_real_fuzz_run_puts_the_product_identity_in_its_header(tmp_path):
    root = make_product(tmp_path / "p", CLEAN)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "p")
    plan = {
        "name": "p",
        "route": "/v1/t",
        "tier": "client",
        "base": {"x": "a"},
        "intake_keys": [],
        "sample_intake_paths": 0,
    }
    report = fuzz.run_fuzz([plan], root=root)
    sha = git(root, "rev-parse", "HEAD")
    assert report.product == {"root": str(root), "sha": sha, "branch": "main", "dirty": False}
    assert fuzz.format_report(report)[0] == f"product: {root} sha={sha} branch=main dirty=False"


# --- a result with anything unchecked is not a pass -------------------------------------------------------------------------------


def rec(*outcomes: str) -> W.Record:
    return W.Record("x", "d", checks=[W.Check(f"c{i}", o, "claim") for i, o in enumerate(outcomes)])


def test_an_unchecked_or_blocked_check_never_makes_the_record_pass():
    assert rec(W.PASS, W.PASS).exit_code == 0
    assert rec(W.PASS, W.UNCHECKED).exit_code == 3
    assert rec(W.UNCHECKED).exit_code == 3
    assert rec(W.PASS, W.BLOCKED).exit_code == 3
    assert rec().exit_code == 3
    assert rec(W.PASS, W.UNCHECKED, W.FAIL).exit_code == 1
