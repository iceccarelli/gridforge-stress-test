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

from .doctor import product_checkout

_RUNNER = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["GRIDFORGE_REPORT_DATE"] = sys.argv[2]
from gridforge.api.server import _invoke, handle_compare
from gridforge.api.tiers import Tier
req = json.load(sys.stdin)
try:
    out = _invoke("/v1/compare", handle_compare, req, Tier.CLIENT)
    json.dump({"ok": True, "result": out}, sys.stdout, default=str)
except Exception as exc:  # report the product's refusal verbatim, do not mask it
    json.dump({"ok": False, "error": type(exc).__name__, "message": str(exc)}, sys.stdout)
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


def product_dir() -> Path:
    p = product_checkout()
    if p is None:
        raise ProductUnavailable("GridForge-AI checkout not found; set GRIDFORGE_PRODUCT_DIR")
    return p


def compare(
    intake: dict[str, Any],
    *,
    report_date: str = "2026-10-27",
    scenarios: list[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Run the product's compare endpoint handler. Returns {'ok': bool, 'result'|'error'...}."""
    root = root or product_dir()
    body: dict[str, Any] = {"intake": intake}
    if scenarios:
        body["scenarios"] = scenarios
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", _RUNNER, str(root), report_date],
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
