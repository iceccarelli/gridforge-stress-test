"""Adapter to the REAL GridForge-AI engine: POST /v1/compare handler, called in a clean subprocess.

Nothing here re-implements the product's calculation. The harness only (a) hands an intake document to the
product's own `handle_compare` and (b) checks the product's answer against independent expectations and against
what the supplied evidence can support.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .doctor import (
    PRODUCT_ENV,
    describe_product,
    product_checkout,
    product_child_env,
    product_override,
    product_python_argv,
    stderr_digest,
)

_RUNNER = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["GRIDFORGE_REPORT_DATE"] = sys.argv[2]
from gridforge.api import server
from gridforge.api.tiers import Tier
req = json.load(sys.stdin)
try:
    out = server._invoke(sys.argv[3], getattr(server, sys.argv[4]), req, Tier(sys.argv[5]))
    json.dump({"ok": True, "result": out}, sys.stdout, default=str)
except Exception as exc:  # report the product's refusal verbatim, do not mask it
    json.dump({"ok": False, "error": type(exc).__name__, "message": str(exc)}, sys.stdout)
"""

#: Drives many (route, body) cases through the product's real handlers in ONE interpreter and classifies each outcome with the same
#: definition the fuzz runner uses: a refusal the HTTP layer turns into a 4xx is "clean"; any other exception would be a 500 "engine error".
_BATCH_RUNNER = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["GRIDFORGE_REPORT_DATE"] = sys.argv[2]
from gridforge.api import server
from gridforge.api.tiers import Tier
from gridforge.intake import loader
clean = (server.ApiError, loader.IntakeError, ValueError, TypeError, KeyError, ArithmeticError)
out = []
for case in json.load(sys.stdin):
    try:
        handler = server.ROUTES[case["route"]][0]
        result = server._invoke(case["route"], handler, case["body"], Tier(case["tier"]))
        row = {"id": case["id"], "status": "ok"}
        if case.get("want_result"):
            row["result"] = result
        out.append(row)
    except clean as exc:
        out.append({"id": case["id"], "status": "clean", "error": type(exc).__name__, "message": str(exc)[:200]})
    except BaseException as exc:
        out.append({"id": case["id"], "status": "fault", "error": type(exc).__name__, "message": str(exc)[:200]})
json.dump(out, sys.stdout, default=str)
"""

#: Drives requests through the product's REAL HTTP handler (`gridforge.api.server.Handler`), served on a loopback port in this child interpreter
#: only, so a status code is the status code a caller would see (a 500 is a 500, not "an exception the batch runner classified"), and the time of
#: each request includes parsing, routing and serialising. The product's own per-IP rate limit is switched off for this private server (as its own
#: tests do); nothing is metered and no other host is contacted. Each request is {id, method, path, json? | raw?}: `raw` is the exact body text.
_HTTP_RUNNER = r"""
import json, os, sys, time, threading, http.client
sys.path.insert(0, sys.argv[1])
os.environ["GRIDFORGE_REPORT_DATE"] = sys.argv[2]
os.environ["GRIDFORGE_RATE_LIMIT"] = "0"
os.environ["GRIDFORGE_DISCOVERY_RATE_LIMIT"] = "0"
from gridforge.api import server
server.Handler.limiter.per_minute = 0
httpd = server.make_server("127.0.0.1", 0)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]
out = []
for req in json.load(sys.stdin):
    body = None
    if "raw" in req:
        body = req["raw"].encode("utf-8", "surrogatepass")
    elif "json" in req:
        body = json.dumps(req["json"]).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
    started = time.monotonic()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        conn.request(req["method"], req["path"], body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read(4_000_000)
        row = {"id": req["id"], "status": resp.status, "content_type": resp.getheader("Content-Type") or "",
               "body": data.decode("utf-8", "replace")}
        conn.close()
    except Exception as exc:
        row = {"id": req["id"], "status": -1, "content_type": "", "body": f"{type(exc).__name__}: {exc}"[:300]}
    row["elapsed_s"] = round(time.monotonic() - started, 4)
    out.append(row)
httpd.shutdown()
json.dump(out, sys.stdout)
"""

GRADE_ORDER = ("undated", "indicative", "screening")
#: Highest product grade the harness lets a claim carry at each evidence level (see evidence.assess).
MAX_GRADE = {
    "none": "undated",
    "context_only": "indicative",
    "indicative": "indicative",
    "site_supported": "screening",
}


class ProductUnavailable(RuntimeError):
    """The real product could not be run at all (missing, unimportable, hung, or its runner spoke nonsense)."""


class ProductRefused(Exception):
    """The product ran and REFUSED a request. Deliberately not a ProductUnavailable: a refusal is a judgement about the product's
    behaviour, never "the product could not be reached"."""

    def __init__(self, message: str, *, check_id: str | None = None) -> None:
        super().__init__(message)
        self.check_id = check_id


def product_required() -> bool:
    """True when the operator named a checkout explicitly: its absence is a failure, never a skip."""
    return bool(product_override())


def product_dir() -> Path:
    p = product_checkout()
    if p is None:
        explicit = product_override()
        head = (
            f"{PRODUCT_ENV}={explicit} is not a GridForge-AI checkout"
            if explicit
            else f"GridForge-AI checkout not found; set {PRODUCT_ENV}"
        )
        raise ProductUnavailable(f"{head}\n{describe_product()}")
    return p


def run_product_json(
    argv_tail: list[str],
    *,
    stdin: str,
    root: Path,
    timeout: float,
    what: str = "product runner",
    timeout_note: str = "",
) -> Any:
    """Run `python -I -B <argv_tail>` as the product child and return its stdout parsed as JSON.

    Every way this can go wrong is a ProductUnavailable that names the interpreter, the root, the return code and the first and last
    stderr lines: a non-zero exit, a timeout, an OS error starting the interpreter, or stdout that is not JSON (stdout head included).
    -B: the product tree is never written to (no .pyc); -X pycache_prefix=<fresh empty dir>: no bytecode is ever READ from it either,
    so a stale or planted .pyc cannot stand in for the source that the recorded identity describes.
    """
    ctx = f"interpreter={sys.executable} root={root}"
    try:
        with tempfile.TemporaryDirectory(prefix="gfs-pycache-") as prefix:
            proc = subprocess.run(  # noqa: S603
                [*product_python_argv(prefix), *argv_tail],
                input=stdin,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                env=product_child_env(),
            )
    except subprocess.TimeoutExpired as exc:
        raise ProductUnavailable(
            f"{what} timed out after {timeout:.0f}s{' (' + timeout_note + ')' if timeout_note else ''}; {ctx}; {stderr_digest(exc.stderr)}"
        ) from exc
    except OSError as exc:
        raise ProductUnavailable(
            f"{what} could not be started: {type(exc).__name__}: {exc}; {ctx}"
        ) from exc
    if proc.returncode != 0:
        raise ProductUnavailable(
            f"{what} failed: return code {proc.returncode}; {ctx}; {stderr_digest(proc.stderr)}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProductUnavailable(
            f"{what} wrote stdout that is not JSON ({exc.msg} at char {exc.pos}); {ctx}; "
            f"return code {proc.returncode}; stdout head: {proc.stdout[:200]!r}; {stderr_digest(proc.stderr)}"
        ) from exc


ENDPOINTS = {
    "compare": ("/v1/compare", "handle_compare"),
    "screen": ("/v1/screen", "handle_screen"),
    "diff": ("/v1/diff", "handle_diff"),
}


def call(
    endpoint: str,
    body: dict[str, Any],
    *,
    tier: str = "client",
    report_date: str = "2026-10-27",
    root: Path | None = None,
) -> dict[str, Any]:
    """Run a real product endpoint handler. Returns {'ok': bool, 'result'|'error'|'message'}."""
    root = root or product_dir()
    route, handler = ENDPOINTS[endpoint]
    parsed: dict[str, Any] = run_product_json(
        ["-c", _RUNNER, str(root), report_date, route, handler, tier],
        stdin=json.dumps(body),
        root=root,
        timeout=120,
    )
    return parsed


def call_batch(
    cases: list[dict[str, Any]],
    *,
    report_date: str = "2026-10-27",
    root: Path | None = None,
    timeout: int = 600,
) -> dict[str, dict[str, Any]]:
    """Run `cases` ({id, route, tier, body, want_result?}) through the real handlers; returns {id: {status: ok|clean|fault, error?, message?,
    result? (only when the case sets want_result)}}.

    Unlike `call`, which names only the three journey endpoints, this reaches any registered route so a defect found by the fuzz can be
    reproduced on the route where it escaped. Bodies travel as JSON (NaN/Infinity included, as Python's JSON parser accepts them).
    """
    root = root or product_dir()
    rows: list[dict[str, Any]] = run_product_json(
        ["-c", _BATCH_RUNNER, str(root), report_date],
        stdin=json.dumps(cases),
        root=root,
        timeout=timeout,
    )
    return {r["id"]: r for r in rows}


def http_batch(
    requests: list[dict[str, Any]],
    *,
    report_date: str = "2026-10-27",
    root: Path | None = None,
    timeout: int = 600,
) -> dict[str, dict[str, Any]]:
    """Send `requests` ({id, method, path, json? | raw?}) to the product's real HTTP handler, in order, on a private loopback server.

    Returns {id: {status, content_type, body (text), elapsed_s}}; status -1 means the connection itself failed (body says how). A body
    is the exact bytes a caller would read, not parsed, so a test can judge the status line, the type and the text separately.
    """
    root = root or product_dir()
    rows: list[dict[str, Any]] = run_product_json(
        ["-c", _HTTP_RUNNER, str(root), report_date],
        stdin=json.dumps(requests),
        root=root,
        timeout=timeout,
    )
    return {r["id"]: r for r in rows}


def compare(
    intake: dict[str, Any],
    *,
    report_date: str = "2026-10-27",
    scenarios: list[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Run the product's /v1/compare handler on an intake."""
    body: dict[str, Any] = {"intake": intake}
    if scenarios:
        body["scenarios"] = scenarios
    return call("compare", body, report_date=report_date, root=root)


def claim_violations(output: dict[str, Any], evidence_level: str) -> list[str]:
    """Where the product's answer asserts more than `evidence_level` supports. Empty means no overclaim found."""
    v: list[str] = []
    cap = GRADE_ORDER.index(MAX_GRADE[evidence_level])
    overall = output.get("grade")
    if overall not in GRADE_ORDER:
        v.append(f"unrecognised overall grade {overall!r}")
    elif GRADE_ORDER.index(overall) > cap:
        v.append(
            f"overall grade {overall!r} exceeds what evidence level {evidence_level!r} supports"
        )
    for s in output.get("scenarios", []):
        ttp = s.get("time_to_power", {})
        if ttp.get("status") == "dated":
            if ttp.get("date_basis") == "assumed" and "not a date" not in str(
                ttp.get("date_label")
            ):
                v.append(f"{s['id']}: assumed date not labelled as an assumption")
            if ttp.get("date_basis") == "supported" and evidence_level != "site_supported":
                v.append(f"{s['id']}: date presented as supported without site-specific evidence")
        elif ttp.get("weeks") is not None:
            v.append(f"{s['id']}: weeks given although status is {ttp.get('status')!r}")
    return v
