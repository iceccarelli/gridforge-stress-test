"""Locate bundled configs/schemas whether running from a checkout or an installed wheel."""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent


def data_root() -> Path:
    checkout = _HERE.parents[1]
    if (checkout / "configs" / "sources.json").is_file():
        return checkout
    return _HERE / "_data"


def configs_dir() -> Path:
    root = data_root()
    return root / "configs"


def schemas_dir() -> Path:
    return data_root() / "schemas"


def fixtures_dir() -> Path:
    return data_root() / "data" / "fixtures"
