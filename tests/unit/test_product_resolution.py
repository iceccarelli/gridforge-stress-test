"""GRIDFORGE_PRODUCT_DIR is authoritative: a bad override must never fall back to another checkout."""

from __future__ import annotations

from pathlib import Path

import pytest

from gridforge_stress import product_adapter as PA
from gridforge_stress.doctor import PRODUCT_ENV, product_checkout, run_doctor

pytestmark = pytest.mark.unit


def make_checkout(root: Path) -> Path:
    marker = root / "gridforge" / "envelope"
    marker.mkdir(parents=True)
    (marker / "time_to_power.py").write_text("# stand-in marker file\n")
    return root


def test_explicit_override_wins_over_the_default_search(tmp_path):
    mine = make_checkout(tmp_path / "mine")
    assert product_checkout({PRODUCT_ENV: str(mine)}) == mine


def test_invalid_override_is_none_even_when_a_default_checkout_exists(tmp_path):
    # In this and most dev setups ../GridForge-AI exists; the override must still not fall through.
    default = product_checkout({})
    bad = tmp_path / "not-a-checkout"
    bad.mkdir()
    assert product_checkout({PRODUCT_ENV: str(bad)}) is None
    assert product_checkout({PRODUCT_ENV: str(tmp_path / "missing")}) is None
    if default is not None:
        assert product_checkout({PRODUCT_ENV: str(bad)}) != default


def test_blank_override_means_unset(tmp_path):
    assert product_checkout({PRODUCT_ENV: "  "}) == product_checkout({})


def test_adapter_says_which_override_is_wrong(monkeypatch, tmp_path):
    monkeypatch.setenv(PRODUCT_ENV, str(tmp_path / "missing"))
    assert PA.product_required() is True
    with pytest.raises(PA.ProductUnavailable, match="is not a GridForge-AI checkout") as exc:
        PA.product_dir()
    assert str(tmp_path / "missing") in str(exc.value)
    monkeypatch.delenv(PRODUCT_ENV)
    assert PA.product_required() is False


def test_doctor_fails_loudly_on_a_bad_override_instead_of_reporting_another_checkout(tmp_path):
    rep = run_doctor({PRODUCT_ENV: str(tmp_path / "missing")})
    check = next(c for c in rep["checks"] if c["check"] == "product_checkout")
    assert check["status"] == "fail" and "not falling back" in check["detail"]
    assert rep["healthy"] is False
