"""Environment/health report. Never prints secret values; absent things are reported absent."""

from __future__ import annotations

import importlib.metadata as md
import os
import platform
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import __version__
from .fixtures import ok, validate_fixtures
from .paths import configs_dir, fixtures_dir, schemas_dir
from .registry import RegistryError, default_sources, load_scenarios, load_sources

PRODUCT_ENV = "GRIDFORGE_PRODUCT_DIR"


def product_checkout(env: Mapping[str, str] | None = None) -> Path | None:
    env_map = os.environ if env is None else env
    candidates = [
        env_map.get(PRODUCT_ENV, ""),
        str(Path(__file__).resolve().parents[3] / "GridForge-AI"),
        "/workspaces/GridForge-AI",
    ]
    for c in candidates:
        if c and (Path(c) / "gridforge" / "envelope" / "time_to_power.py").is_file():
            return Path(c)
    return None


def _git(path: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def run_doctor(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env_map = os.environ if env is None else env
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    add(
        "python",
        "pass" if sys.version_info >= (3, 11) else "fail",
        f"{platform.python_version()} at {sys.executable} (supported: >=3.11,<3.14)",
    )
    add("package", "pass", f"gridforge-stress {__version__}")
    for dep in ("jsonschema",):
        try:
            add(f"dependency:{dep}", "pass", md.version(dep))
        except md.PackageNotFoundError:
            add(f"dependency:{dep}", "fail", "not installed")
    for label, path in (("configs", configs_dir()), ("schemas", schemas_dir())):
        add(f"dir:{label}", "pass" if path.is_dir() else "fail", str(path))
    try:
        n = len(load_sources())
        s = len(load_scenarios())
        add("registry", "pass", f"{n} sources, {s} scenarios schema-valid")
    except (RegistryError, OSError) as exc:
        add("registry", "fail", str(exc))
    fx = fixtures_dir()
    if fx.is_dir():
        found = validate_fixtures(fx)
        add("fixtures", "pass" if ok(found) else "fail", f"{len(found)} finding(s)")
    else:
        add("fixtures", "fail", f"missing {fx}")
    prod = product_checkout(env_map)
    if prod is None:
        add("product_checkout", "absent", f"not found; set {PRODUCT_ENV}")
    else:
        add(
            "product_checkout",
            "pass",
            f"{prod} branch={_git(prod, 'branch', '--show-current')} "
            f"commit={(_git(prod, 'rev-parse', 'HEAD') or '?')[:12]}",
        )
    cred_state = {}
    try:
        for src in default_sources().values():
            for name in src.credential_env:
                cred_state[name] = "set" if env_map.get(name) else "missing"
    except RegistryError:
        pass
    for name, state in sorted(cred_state.items()):
        add(
            f"credential:{name}",
            "pass" if state == "set" else "absent",
            state + " (value never shown)",
        )
    add(
        "live_sources",
        "info",
        "none verified: no publisher page has been read by this harness; live fetch is disabled for all sources",
    )
    healthy = all(c["status"] != "fail" for c in checks)
    return {"healthy": healthy, "checks": checks}
