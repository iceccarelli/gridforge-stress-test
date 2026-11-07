"""Environment/health report. Never prints secret values; absent things are reported absent."""

from __future__ import annotations

import importlib.metadata as md
import os
import platform
import re
import subprocess
import sys
import tempfile
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


def _stat_kind(p: Path) -> str:
    """file / dir / missing, or `unreadable (<reason>)` for an OS error such as a path that is too long."""
    try:
        if p.is_file():
            return "file"
        if p.is_dir():
            return "dir"
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "strerror", None) or str(exc)
        return f"unreadable ({reason})"
    return "missing"


def marker_status(root: str | Path) -> str:
    """ok / missing / is a directory / unreadable (<OS reason>), for the marker file under `root`."""
    kind = _stat_kind(marker_path(root))
    return {"file": "ok", "dir": "is a directory"}.get(kind, kind)


def product_checkout(env: Mapping[str, str] | None = None) -> Path | None:
    """The GridForge-AI checkout to test against.

    An explicit GRIDFORGE_PRODUCT_DIR is authoritative: if it does not point at a checkout the result is
    None, never a different checkout found by default search (which would test something other than asked).
    An OS error while looking (path too long, embedded NUL) means "not a checkout"; `describe_product` reports the reason.
    """
    explicit = product_override(env)
    candidates = [explicit] if explicit else default_product_candidates()
    for c in candidates:
        if _stat_kind(marker_path(c)) == "file":
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
        if _stat_kind(marker_path(parent)) == "file":
            hints.append(
                f"the value is inside a real checkout whose root is {parent}; point {PRODUCT_ENV} at that root"
            )
            break
    try:
        children = sorted(c for c in path.iterdir() if c.is_dir())[:200]
    except (OSError, ValueError):
        children = []
    for child in children:
        if _stat_kind(marker_path(child)) == "file":
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
    if _stat_kind(marker_path(value)) != "file":
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


#: Overrides that make `git` ignore anything a (possibly hostile or merely odd) repo or user config could change. Command-line `-c`
#: wins over every config file. fsmonitor and hooks would execute programs; ignoreStat/untrackedCache/showUntrackedFiles hide changes.
_GIT_HARDENING = (
    "-c", "core.fsmonitor=false",
    "-c", f"core.hooksPath={os.devnull}",
    "-c", "core.untrackedCache=false",
    "-c", "core.ignoreStat=false",
    "-c", "core.splitIndex=false",
    "-c", "status.showUntrackedFiles=all",
)  # fmt: skip


def git_env() -> dict[str, str]:
    """Environment for the git queries: no credentials, no GRIDFORGE_*, no GIT_* from the caller, no user or system gitconfig."""
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG")}
    if sys.platform == "win32":
        base.update({k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "TEMP", "TMP")})
    base.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return base


def _git_bytes(path: Path, *args: str) -> bytes | None:
    """Raw stdout of a hardened, read-only `git -C path ...`; None on ANY failure (non-zero exit, timeout, OS error)."""
    try:
        r = subprocess.run(
            ["git", *_GIT_HARDENING, "-C", str(path), *args],
            capture_output=True,
            timeout=30,
            check=False,
            env=git_env(),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _git(path: Path, *args: str) -> str | None:
    """Stripped stdout of a git query as text (undecodable bytes replaced, never an exception); None on any failure."""
    out = _git_bytes(path, *args)
    return None if out is None else out.decode("utf-8", "replace").strip()


_IMPORTABLE_SUFFIXES = (".py", ".pyc", ".pyd", ".so", ".pth")
_PACKAGE_DIR = "gridforge/"


def _is_init(rel: str) -> bool:
    parts = rel.split("/")
    return (
        len(parts) == 2
        and parts[1].split(".")[0] == "__init__"
        and rel.endswith(_IMPORTABLE_SUFFIXES)
    )


def _ignored_file_matters(rel: str, package_tops: frozenset[str]) -> bool:
    """An ignored file the product child could import or read, given it runs with the checkout root first on sys.path.

    Counts: anything under `gridforge/`; an importable file (.py/.pyc/.pyd/.so/.pth) directly in the root (it can shadow the standard
    library); a top-level package's `__init__` (it can shadow too); an importable file inside any top-level regular package. Does not
    count: bytecode under `__pycache__` (the child's fresh `pycache_prefix` means it is never read) or unrelated files such as a
    vendored `node_modules/**/*.py` that no import path reaches. A sourceless `.pyc` beside the sources does count.
    """
    rel = rel.replace("\\", "/")
    if "/__pycache__/" in f"/{rel}":
        return False
    if rel.startswith(_PACKAGE_DIR):
        return True
    if not rel.endswith(_IMPORTABLE_SUFFIXES):
        return False
    return "/" not in rel or _is_init(rel) or rel.split("/", 1)[0] in package_tops


def worktree_dirty(path: Path) -> bool | None:
    """Whether the files the product child can run differ from HEAD. True/False, or None when any probe fails (never False by guess).

    Hidden-change routes that plain `git status --porcelain` misses are all covered:
    * assume-unchanged / skip-worktree index bits (`git ls-files -v` flag other than `H`);
    * untracked files hidden by config (`status.showUntrackedFiles=no`): untracked files are always listed;
    * ignored (.gitignore, .git/info/exclude) importable files, per `_ignored_file_matters`;
    * submodule changes (`--ignore-submodules=none`).
    """
    flags = _git_bytes(path, "ls-files", "-v", "-z")
    status = _git_bytes(
        path, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"
    )
    ignored = _git_bytes(path, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    if flags is None or status is None or ignored is None:
        return None
    if status.strip(b"\0"):
        return True
    tracked: list[str] = []
    for entry in flags.split(b"\0"):
        if entry and entry[:1] != b"H":
            return True
        if entry:
            tracked.append(entry[2:].decode("utf-8", "replace"))
    ignored_paths = [e.decode("utf-8", "replace") for e in ignored.split(b"\0") if e]
    package_tops = frozenset(
        rel.split("/")[0] for rel in (*tracked, *ignored_paths) if _is_init(rel)
    )
    return any(_ignored_file_matters(rel, package_tops) for rel in ignored_paths)


def product_identity(root: str | Path) -> dict[str, Any]:
    """Exact identity of the product checkout: {sha, branch, dirty}.

    sha is `git rev-parse HEAD` (validated as 40 lowercase hex); dirty is `worktree_dirty` (see there). ANY git failure, a malformed
    answer, or a tree that is not itself a git work tree (a `git archive` export, or a directory nested in some other repository)
    gives sha=None and dirty=None, never a guess and never "clean". branch is None when HEAD is detached.
    """
    unknown: dict[str, Any] = {"sha": None, "branch": None, "dirty": None}
    path = Path(root)
    try:
        if not (path / ".git").exists():
            return unknown
    except (OSError, ValueError):
        return unknown
    top = _git(path, "rev-parse", "--show-toplevel")
    if not top or os.path.realpath(top) != os.path.realpath(path):
        return unknown
    sha = _git(path, "rev-parse", "HEAD")
    branch = _git(path, "branch", "--show-current")
    dirty = worktree_dirty(path)
    if sha is None or branch is None or dirty is None or not _SHA_RE.fullmatch(sha):
        return unknown
    return {"sha": sha, "branch": branch or None, "dirty": dirty}


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


def product_python_argv(prefix: str) -> list[str]:
    """Interpreter command line for every product child: isolated (-I), no bytecode written (-B), and bytecode never READ from the
    product tree (a fresh, empty pycache_prefix), so a stale or planted unchecked-hash .pyc cannot replace the source."""
    return [sys.executable, "-I", "-B", "-X", f"pycache_prefix={prefix}"]


def product_import_check(root: Path) -> tuple[str, str]:
    """Import gridforge.api.server exactly as the runners do (`python -I -B`, fresh pycache_prefix, minimal env). Returns (pass|fail, detail)."""
    code = "import sys; sys.path.insert(0, sys.argv[1]); from gridforge.api import server"
    try:
        with tempfile.TemporaryDirectory(prefix="gfs-pycache-") as prefix:
            r = subprocess.run(
                [*product_python_argv(prefix), "-c", code, str(root)],
                capture_output=True,
                text=True,
                errors="replace",
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
        if _stat_kind(root / "gridforge" / "api" / "server.py") != "file"
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
