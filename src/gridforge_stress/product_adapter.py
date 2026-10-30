"""Adapter to the REAL GridForge-AI engine: POST /v1/compare handler, called in a clean subprocess.

Nothing here re-implements the product's calculation. The harness only (a) hands an intake document to the
product's own `handle_compare` and (b) checks the product's answer against independent expectations and against
what the supplied evidence can support.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .doctor import product_checkout, product_override

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
        server._invoke(case["route"], handler, case["body"], Tier(case["tier"]))
        out.append({"id": case["id"], "status": "ok"})
    except clean as exc:
        out.append({"id": case["id"], "status": "clean", "error": type(exc).__name__, "message": str(exc)[:200]})
    except BaseException as exc:
        out.append({"id": case["id"], "status": "fault", "error": type(exc).__name__, "message": str(exc)[:200]})
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
    pass


def product_required() -> bool:
    """True when the operator named a checkout explicitly: its absence is a failure, never a skip."""
    return bool(product_override())


def product_dir() -> Path:
    p = product_checkout()
    if p is None:
        explicit = product_override()
        raise ProductUnavailable(
            f"GRIDFORGE_PRODUCT_DIR={explicit} is not a GridForge-AI checkout"
            if explicit
            else "GridForge-AI checkout not found; set GRIDFORGE_PRODUCT_DIR"
        )
    return p


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
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", _RUNNER, str(root), report_date, route, handler, tier],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")},
    )
    if proc.returncode != 0:
        raise ProductUnavailable(f"product runner failed: {proc.stderr[-400:]}")
    parsed: dict[str, Any] = json.loads(proc.stdout)
    return parsed


def call_batch(
    cases: list[dict[str, Any]],
    *,
    report_date: str = "2026-10-27",
    root: Path | None = None,
    timeout: int = 600,
) -> dict[str, dict[str, Any]]:
    """Run `cases` ({id, route, tier, body}) through the real handlers; returns {id: {status: ok|clean|fault, error?, message?}}.

    Unlike `call`, which names only the three journey endpoints, this reaches any registered route so a defect found by the fuzz can be
    reproduced on the route where it escaped. Bodies travel as JSON (NaN/Infinity included, as Python's JSON parser accepts them).
    """
    root = root or product_dir()
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", _BATCH_RUNNER, str(root), report_date],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")},
    )
    if proc.returncode != 0:
        raise ProductUnavailable(f"product runner failed: {proc.stderr[-400:]}")
    rows: list[dict[str, Any]] = json.loads(proc.stdout)
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
