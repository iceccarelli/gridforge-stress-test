"""ProductUnavailable must explain itself: one test per diagnosed cause, each asserting the raw value, the marker path and the hint.

Offline: tmp_path stand-in checkouts and monkeypatch only; no network and no real product.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from gridforge_stress import doctor as D
from gridforge_stress import product_adapter as PA

pytestmark = pytest.mark.unit

MARKER = Path("gridforge") / "envelope" / "time_to_power.py"


def make_marker(root: Path) -> Path:
    (root / MARKER).parent.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("# stand-in marker\n")
    return root


def fail_message(monkeypatch: pytest.MonkeyPatch, value: str) -> str:
    monkeypatch.setenv(D.PRODUCT_ENV, value)
    with pytest.raises(PA.ProductUnavailable) as exc:
        PA.product_dir()
    return str(exc.value)


def test_unexpanded_tilde_is_named(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    value = "~/GridForge-AI"
    msg = fail_message(monkeypatch, value)
    assert repr(value) in msg
    assert str(tmp_path / "~" / "GridForge-AI" / MARKER) in msg  # looked up literally under the cwd
    assert "-> missing" in msg
    assert "starts with '~'" in msg and "does not expand" in msg


def test_unexpanded_dollar_variable_is_named(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    value = "$HOME/GridForge-AI"
    msg = fail_message(monkeypatch, value)
    assert repr(value) in msg
    assert str(tmp_path / "$HOME" / "GridForge-AI" / MARKER) in msg
    assert "contains '$'" in msg and "not expanded" in msg


def test_quoted_value_is_named_even_when_the_inner_path_is_a_checkout(monkeypatch, tmp_path):
    real = make_marker(tmp_path / "real")
    value = f'"{real}"'  # what cmd.exe `set X="..."` stores
    assert (
        D.product_checkout({D.PRODUCT_ENV: value}) is None
    )  # behaviour unchanged: no quote-stripping
    msg = fail_message(monkeypatch, value)
    assert repr(value) in msg
    assert "-> missing" in msg and "quote characters" in msg


def test_relative_path_is_resolved_against_the_current_directory(monkeypatch, tmp_path):
    make_marker(tmp_path / "work" / "GridForge-AI")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    msg = fail_message(monkeypatch, "GridForge-AI")
    assert "'GridForge-AI'" in msg
    assert f"current directory: {elsewhere}" in msg
    assert str(elsewhere / "GridForge-AI" / MARKER) in msg
    assert "relative path" in msg
    monkeypatch.chdir(tmp_path / "work")  # the same value is fine from the right directory
    monkeypatch.setenv(D.PRODUCT_ENV, "GridForge-AI")
    assert PA.product_dir() == Path("GridForge-AI")


def test_windows_path_on_a_posix_host_is_named(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("only meaningful off Windows")
    value = "C:\\Users\\me\\GridForge-AI"
    msg = fail_message(monkeypatch, value)
    assert repr(value) in msg and "Windows/UNC path" in msg
    unc = fail_message(monkeypatch, "\\\\server\\share\\GridForge-AI")
    assert "Windows/UNC path" in unc


def test_a_value_one_level_above_a_real_checkout_names_the_child(monkeypatch, tmp_path):
    child = make_marker(tmp_path / "ws" / "GridForge-AI")
    msg = fail_message(monkeypatch, str(tmp_path / "ws"))
    assert repr(str(tmp_path / "ws")) in msg and "-> missing" in msg
    assert f"a real checkout exists at {child}" in msg


def test_a_value_inside_a_real_checkout_names_its_root(monkeypatch, tmp_path):
    root = make_marker(tmp_path / "GridForge-AI")
    inner = root / "gridforge" / "envelope"
    msg = fail_message(monkeypatch, str(inner))
    assert f"inside a real checkout whose root is {root}" in msg


def test_marker_that_is_a_directory_is_reported_as_such(monkeypatch, tmp_path):
    (tmp_path / MARKER).mkdir(parents=True)
    msg = fail_message(monkeypatch, str(tmp_path))
    assert f"{tmp_path / MARKER} -> is a directory" in msg


def test_unset_override_lists_the_default_candidates_and_the_interpreter(monkeypatch):
    monkeypatch.delenv(D.PRODUCT_ENV, raising=False)
    monkeypatch.setattr(D, "default_product_candidates", lambda: ["/nope/a", "/nope/b"])
    monkeypatch.setattr(D, "marker_status", lambda root: "missing")
    text = D.describe_product({})
    assert f"{D.PRODUCT_ENV}: unset" in text
    assert "/nope/a -> marker missing; /nope/b -> marker missing" in text
    assert f"python: {sys.executable}" in text


def test_a_symlinked_harness_is_named_when_defaults_would_move(monkeypatch, tmp_path):
    real = tmp_path / "realws" / "h" / "src" / "gridforge_stress"
    real.mkdir(parents=True)
    (real / "doctor.py").write_text("")
    link = tmp_path / "lnk"
    try:
        link.symlink_to(tmp_path / "realws" / "h", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(D, "__file__", str(link / "src" / "gridforge_stress" / "doctor.py"))
    text = D.describe_product({})
    assert "reached through a symlink" in text and "RESOLVED path" in text


def test_the_doctor_check_carries_the_same_explanation(tmp_path):
    rep = D.run_doctor({D.PRODUCT_ENV: "~/missing"})
    check = next(c for c in rep["checks"] if c["check"] == "product_checkout")
    assert check["status"] == "fail" and "not falling back" in check["detail"]
    assert "'~/missing'" in check["detail"] and "-> missing" in check["detail"]
    assert "starts with '~'" in check["detail"]
    assert not any(c["check"] == "product_import" for c in rep["checks"])


# --- a checkout that is present but cannot be run ------------------------------------------------------------------------------------


def test_partial_checkout_is_a_failed_import_check_naming_the_missing_module(tmp_path):
    root = make_marker(tmp_path / "p")  # marker present, gridforge/api/server.py absent
    (root / "gridforge" / "__init__.py").write_text("")
    status, detail = D.product_import_check(root)
    assert status == "fail"
    assert "rc=1" in detail and "stderr first:" in detail and "last:" in detail
    assert "partial checkout" in detail and "gridforge/api/server.py" in detail
    rep = D.run_doctor({D.PRODUCT_ENV: str(root)})
    chk = next(c for c in rep["checks"] if c["check"] == "product_import")
    assert chk["status"] == "fail" and rep["healthy"] is False


def test_a_working_import_passes_the_import_check(tmp_path):
    root = make_fake_product(tmp_path / "ok", server_prefix="")
    assert D.product_import_check(root)[0] == "pass"


def make_fake_product(root: Path, *, server_prefix: str) -> Path:
    make_marker(root)
    for pkg in ("gridforge", "gridforge/api"):
        (root / pkg).mkdir(parents=True, exist_ok=True)
        (root / pkg / "__init__.py").write_text("")
    (root / "gridforge" / "api" / "tiers.py").write_text(
        "import enum\nclass Tier(str, enum.Enum):\n    CLIENT = 'client'\n"
    )
    (root / "gridforge" / "api" / "server.py").write_text(
        server_prefix
        + "def _invoke(route, handler, body, tier):\n    return {'echo': body}\n"
        + "def handle_compare(body, tier):\n    return {}\n"
    )
    return root


def test_stdout_noise_on_import_is_wrapped_with_its_head(tmp_path):
    root = make_fake_product(tmp_path / "noisy", server_prefix="print('hello from import')\n")
    with pytest.raises(PA.ProductUnavailable) as exc:
        PA.call("compare", {"x": 1}, root=root)
    msg = str(exc.value)
    assert "not JSON" in msg and "stdout head: 'hello from import" in msg
    assert (
        f"root={root}" in msg and f"interpreter={sys.executable}" in msg and "return code 0" in msg
    )


def test_a_clean_fake_product_still_works_end_to_end(tmp_path):
    root = make_fake_product(tmp_path / "clean", server_prefix="")
    assert PA.call("compare", {"x": 1}, root=root) == {"ok": True, "result": {"echo": {"x": 1}}}


def test_the_stdout_head_is_capped_at_200_characters(monkeypatch, tmp_path):
    done = subprocess.CompletedProcess([], 0, stdout="x" * 5000, stderr="")
    monkeypatch.setattr(PA.subprocess, "run", lambda *a, **k: done)
    with pytest.raises(PA.ProductUnavailable) as exc:
        PA.call("compare", {}, root=tmp_path)
    assert "x" * 200 in str(exc.value) and "x" * 201 not in str(exc.value)


def test_nonzero_exit_reports_interpreter_root_code_and_first_and_last_stderr(
    monkeypatch, tmp_path
):
    err = (
        "Traceback (most recent call last):\n  File x\nImportError: no module named gridforge.api\n"
    )
    done = subprocess.CompletedProcess([], 7, stdout="", stderr=err)
    monkeypatch.setattr(PA.subprocess, "run", lambda *a, **k: done)
    with pytest.raises(PA.ProductUnavailable) as exc:
        PA.call("compare", {}, root=tmp_path)
    msg = str(exc.value)
    assert msg.startswith("product runner failed")
    assert "return code 7" in msg and f"root={tmp_path}" in msg and sys.executable in msg
    assert "Traceback (most recent call last):" in msg and "ImportError: no module named" in msg


def test_timeout_is_wrapped(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("cmd", 120, stderr=b"warming up\nstill going\n")

    monkeypatch.setattr(PA.subprocess, "run", boom)
    with pytest.raises(PA.ProductUnavailable) as exc:
        PA.call("compare", {}, root=tmp_path)
    msg = str(exc.value)
    assert "timed out after 120s" in msg and f"root={tmp_path}" in msg
    assert "warming up" in msg and "still going" in msg


def test_oserror_is_wrapped(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file", "/no/python")

    monkeypatch.setattr(PA.subprocess, "run", boom)
    with pytest.raises(PA.ProductUnavailable) as exc:
        PA.call_batch([], root=tmp_path)
    assert "could not be started" in str(exc.value) and "FileNotFoundError" in str(exc.value)
    assert f"root={tmp_path}" in str(exc.value)


# --- child environment -----------------------------------------------------------------------------------------------------------------

HOST_ENV = {
    "PATH": "/bin",
    "HOME": "/h",
    "LANG": "C",
    "SYSTEMROOT": "C:\\Windows",
    "TEMP": "C:\\t",
    "TMP": "C:\\t",
    "GRIDFORGE_PRODUCT_DIR": "/p",
    "GRIDFORGE_API_KEY": "secret",
    "AWS_SECRET_ACCESS_KEY": "secret",
}


def test_child_env_has_no_windows_variables_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert D.product_child_env(HOST_ENV) == {"PATH": "/bin", "HOME": "/h", "LANG": "C"}


def test_child_env_adds_systemroot_temp_tmp_on_windows_only_and_never_credentials(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    env = D.product_child_env(HOST_ENV)
    assert set(env) == {"PATH", "HOME", "LANG", "SYSTEMROOT", "TEMP", "TMP"}
    assert not any(k.startswith("GRIDFORGE_") or "SECRET" in k for k in env)
