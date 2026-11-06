"""Environment/health report. Never prints secret values; absent things are reported absent."""

from __future__ import annotations

import importlib.metadata as md
import os
import platform
import re
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
#: The only thing that marks a directory as a GridForge-AI checkout.
PRODUCT_MARKER = ("gridforge", "envelope", "time_to_power.py")
DEFAULT_WORKSPACE_CANDIDATE = "/workspaces/GridForge-AI"
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_WINDOWS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
#: Variables the product child may see. Never a GRIDFORGE_* or credential variable.
_CHILD_ENV_KEYS = ("PATH", "HOME", "LANG")
#: Windows cannot start Python (or resolve temp files) without these; added on win32 ONLY.
_WIN32_CHILD_ENV_KEYS = ("SYSTEMROOT", "TEMP", "TMP")


def product_override(env: Mapping[str, str] | None = None) -> str:
    """The operator's explicit GRIDFORGE_PRODUCT_DIR, or "" when unset."""
    return (os.environ if env is None else env).get(PRODUCT_ENV, "").strip()


def default_product_candidates() -> list[str]:
    """Where the checkout is looked for when GRIDFORGE_PRODUCT_DIR is unset (and only then)."""
    return [str(Path(__file__).resolve().parents[3] / "GridForge-AI"), DEFAULT_WORKSPACE_CANDIDATE]


def marker_path(root: str | Path) -> Path:
    return Path(root).joinpath(*PRODUCT_MARKER)


def marker_status(root: str | Path) -> str:
    """ok / missing / is a directory, for the marker file under `root`."""
    m = marker_path(root)
    if m.is_file():
        return "ok"
    if m.is_dir():
        return "is a directory"
    return "missing"


def product_checkout(env: Mapping[str, str] | None = None) -> Path | None:
    """The GridForge-AI checkout to test against.

    An explicit GRIDFORGE_PRODUCT_DIR is authoritative: if it does not point at a checkout the result is
    None, never a different checkout found by default search (which would test something other than asked).
    """
    explicit = product_override(env)
    candidates = [explicit] if explicit else default_product_candidates()
    for c in candidates:
        if marker_path(c).is_file():
            return Path(c)
    return None


def product_child_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The only environment the product subprocess gets. GRIDFORGE_*/credential variables are never passed."""
    src = os.environ if source is None else source
    keys = _CHILD_ENV_KEYS + (_WIN32_CHILD_ENV_KEYS if sys.platform == "win32" else ())
    return {k: v for k, v in src.items() if k in keys}


def stderr_digest(stderr: str | bytes | None, *, width: int = 300) -> str:
    """First and last non-empty stderr lines (each capped), so a traceback's cause and its first frame both survive."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    if not lines:
        return "stderr: <empty>"
    first, last = lines[0][:width], lines[-1][:width]
    return f"stderr first: {first!r}" + ("" if len(lines) == 1 else f"; last: {last!r}")


def _near_checkout(path: Path) -> list[str]:
    """Hints when `path` is not a checkout but a real one is its parent or a direct child."""
    hints: list[str] = []
    for parent in path.parents:
        if marker_path(parent).is_file():
            hints.append(
                f"the value is inside a real checkout whose root is {parent}; point {PRODUCT_ENV} at that root"
            )
            break
    try:
        children = sorted(c for c in path.iterdir() if c.is_dir())[:200]
    except OSError:
        children = []
    for child in children:
        if marker_path(child).is_file():
            hints.append(
                f"a real checkout exists at {child}, one level below the value; point {PRODUCT_ENV} there"
            )
            break
    return hints


def product_hints(raw: str, cwd: Path) -> list[str]:
    """Likely reasons an explicit GRIDFORGE_PRODUCT_DIR value does not find the marker. Advice only, no behaviour change."""
    value = raw.strip()
    hints: list[str] = []
    if not value:
        return hints
    if value.startswith("~"):
        hints.append(
            "the value starts with '~', which this harness does not expand (only an unquoted shell does), so it is looked up literally as a directory named '~'; use an absolute path"
        )
    if "$" in value or re.search(r"%[^%]+%", value):
        hints.append(
            "the value contains '$' or '%VAR%', which is not expanded here (it was passed literally); use an absolute path"
        )
    if '"' in value or "'" in value:
        hints.append(
            'the value contains quote characters, which are kept literally (cmd.exe `set X="..."` stores the quotes); remove them'
        )
    if _WINDOWS_PATH_RE.match(value) and sys.platform != "win32":
        hints.append("the value looks like a Windows/UNC path but this host is not Windows")
    if not os.path.isabs(value) and not _WINDOWS_PATH_RE.match(value):
        hints.append(
            f"relative path: resolved against the current directory {cwd} -> {os.path.abspath(value)}; "
            "a different working directory finds a different place"
        )
    if not marker_path(value).is_file():
        hints.extend(_near_checkout(Path(os.path.abspath(value))))
    return hints


def describe_product(env: Mapping[str, str] | None = None, *, cwd: Path | None = None) -> str:
    """Self-explaining account of how the product checkout was (not) found. Multi-line, no secrets."""
    env_map = os.environ if env is None else env
    here = Path.cwd() if cwd is None else cwd
    raw = env_map.get(PRODUCT_ENV)
    explicit = (raw or "").strip()
    lines = [f"{PRODUCT_ENV} raw value: {raw!r}" if raw is not None else f"{PRODUCT_ENV}: unset"]
    lines.append(f"current directory: {here}")
    if explicit:
        tried = os.path.abspath(explicit)
        lines.append(f"tried (authoritative, no fallback): {tried}")
        lines.append(f"marker: {marker_path(tried)} -> {marker_status(tried)}")
    else:
        lines.append(f"{PRODUCT_ENV} is unset or blank: only the default candidates are tried")
    cands = default_product_candidates()
    lines.append(
        "default candidates ("
        + ("not used because the override is set" if explicit else "used")
        + "): "
        + "; ".join(f"{c} -> marker {marker_status(c)}" for c in cands)
    )
    lines.append(f"python: {sys.executable} {platform.python_version()}")
    hints = product_hints(raw or "", here)
    here_file = Path(__file__)
    if (
        not explicit
        and here_file.resolve().parents[3] != Path(os.path.abspath(here_file)).parents[3]
    ):
        hints.append(
            "the harness was reached through a symlink: the default candidate is derived from the RESOLVED path "
            f"({cands[0]}), not the path it was started from; set {PRODUCT_ENV} explicitly"
        )
    lines += [f"hint: {h}" for h in hints]
    return "\n".join(lines)


def _git(path: Path, *args: str) -> str | None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def product_identity(root: str | Path) -> dict[str, Any]:
    """Exact identity of the product checkout: {sha, branch, dirty}.

    sha is `git rev-parse HEAD` (validated as 40 lowercase hex); dirty is whether `git status --porcelain` lists anything
    (untracked files included). ANY git failure, a malformed answer, or a tree that is not itself a git work tree (a `git
    archive` export, or a directory nested in some other repository) gives sha=None and dirty=None, never a guess and never
    "clean". branch is None when HEAD is detached.
    """
    unknown: dict[str, Any] = {"sha": None, "branch": None, "dirty": None}
    path = Path(root)
    if not (path / ".git").exists():
        return unknown
    top = _git(path, "rev-parse", "--show-toplevel")
    if top is None or os.path.realpath(top) != os.path.realpath(path):
        return unknown
    sha = _git(path, "rev-parse", "HEAD")
    porcelain = _git(path, "status", "--porcelain")
    branch = _git(path, "branch", "--show-current")
    if sha is None or porcelain is None or branch is None or not _SHA_RE.fullmatch(sha):
        return unknown
    return {"sha": sha, "branch": branch or None, "dirty": bool(porcelain)}


def current_product_identity(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """{root, sha, branch, dirty} for the checkout a run would use; root None (and the rest None) when there is none."""
    root = product_checkout(env)
    if root is None:
        return {"root": None, "sha": None, "branch": None, "dirty": None}
    return {"root": str(root), **product_identity(root)}


def format_identity(identity: Mapping[str, Any], root: str | Path | None = None) -> str:
    where = f"{root} " if root is not None else ""
    if identity.get("sha") is None:
        return f"{where}sha=UNKNOWN branch=UNKNOWN dirty=UNKNOWN (no usable git metadata: not a verified identity)"
    return f"{where}sha={identity['sha']} branch={identity.get('branch') or 'detached'} dirty={identity.get('dirty')}"


def product_import_check(root: Path) -> tuple[str, str]:
    """Import gridforge.api.server exactly as the runners do (`python -I -B`, minimal env). Returns (pass|fail, detail)."""
    code = "import sys; sys.path.insert(0, sys.argv[1]); from gridforge.api import server"
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(root)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=product_child_env(),
        )
    except subprocess.TimeoutExpired as exc:
        return "fail", f"import timed out after 60s; {stderr_digest(exc.stderr)}"
    except OSError as exc:
        return "fail", f"could not start {sys.executable}: {exc}"
    if r.returncode == 0:
        return "pass", f"import gridforge.api.server ok under {sys.executable} -I"
    partial = (
        "; partial checkout: marker present but gridforge/api/server.py missing"
        if not (root / "gridforge" / "api" / "server.py").is_file()
        else ""
    )
    return "fail", f"import failed rc={r.returncode}; {stderr_digest(r.stderr)}{partial}"


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
    identity: dict[str, Any] = {"sha": None, "branch": None, "dirty": None}
    if prod is None:
        explicit = product_override(env_map)
        if explicit:
            add(
                "product_checkout",
                "fail",
                f"{PRODUCT_ENV}={explicit} is not a GridForge-AI checkout "
                "(no gridforge/envelope/time_to_power.py); not falling back to another directory\n"
                + describe_product(env_map),
            )
        else:
            add(
                "product_checkout",
                "absent",
                f"not found; set {PRODUCT_ENV}\n" + describe_product(env_map),
            )
    else:
        identity = product_identity(prod)
        add("product_checkout", "pass", f"{format_identity(identity, prod)}")
        status, detail = product_import_check(prod)
        add("product_import", status, detail)
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
    try:
        srcs = default_sources().values()
        read = [x.id for x in srcs if x.raw["verification"]["status"] == "publisher_page_read"]
        enabled = [x.id for x in srcs if x.live_fetch == "enabled"]
        add(
            "live_sources",
            "info",
            f"publisher page read for {len(read)}/{len(srcs)} sources ({', '.join(read) or 'none'}); "
            f"live fetch enabled for {len(enabled)}; nothing has been parsed from a live download",
        )
    except RegistryError:
        pass
    healthy = all(c["status"] != "fail" for c in checks)
    return {"healthy": healthy, "checks": checks, "product_identity": identity}
