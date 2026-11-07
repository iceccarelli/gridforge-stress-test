"""Shared helpers: a throwaway git repo standing in for a product checkout."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

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
