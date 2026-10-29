"""Hostile-input fuzzing of the product's REAL request handlers.

A request the product cannot handle must be refused (4xx: ApiError, IntakeError, ValueError, TypeError, KeyError, ArithmeticError). Anything
else escaping a handler becomes a 500 "engine error" with a printed traceback, which blames the engine for a caller's mistake; a very slow call
is a denial-of-service lever on the free public endpoints. This module drives every route with a fixed catalogue of hostile values (wrong types,
non-finite and enormous numbers, nested structures thousands deep) placed at every field and reports what escaped or was slow.

It does not re-implement any calculation and does not judge the product's answers: only whether a request ended in a refusal or an answer, or
in something the HTTP layer would call a server fault. The work runs in one subprocess inside the product's interpreter
(`_fuzz_runner.py`, standard library only), so thousands of calls take minutes, not hours. The base documents are the harness's own labelled
SYNTHETIC fixture, never real customer data.

What it cannot see: the HTTP dispatcher itself (authentication, metering, body parsing), and anything the fixed catalogue does not contain. A
clean report means those inputs were refused or answered, not that no input can fail.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import fixtures_dir
from .product_adapter import ProductUnavailable, product_dir

RUNNER = Path(__file__).with_name("_fuzz_runner.py")


@dataclass(frozen=True)
class Finding:
    plan: str
    exception: str
    message: str
    raised_in: str
    count: int
    where: tuple[str, ...]


@dataclass(frozen=True)
class SlowCall:
    plan: str
    seconds: float
    where: str


@dataclass(frozen=True)
class FuzzReport:
    calls: int
    ok: int
    clean: int
    non_clean: tuple[Finding, ...]
    slow: tuple[SlowCall, ...]
    per_plan: dict[str, int] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return not self.non_clean and not self.slow


def reference_intake() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(
        (fixtures_dir() / "product" / "reference_hall_intake.json").read_text()
    )
    return doc


def default_plans(intake: dict[str, Any], *, sample_intake_paths: int = 12) -> list[dict[str, Any]]:
    """One plan per route, each with a valid base body. `intake_keys` names the subtrees that are intake documents: the loader is fuzzed
    exhaustively on its own, so inside those subtrees only a seeded sample of paths is tried per route."""

    def plan(
        name: str, route: str, base: dict[str, Any], keys: list[str], tier: str = "client"
    ) -> dict[str, Any]:
        return {
            "name": name,
            "route": route,
            "tier": tier,
            "base": base,
            "intake_keys": keys,
            "sample_intake_paths": sample_intake_paths,
        }

    deploy = {
        "load_profile": {"flat_kW": 8000},
        "grid_firm_MW": 10,
        "target_MW": 35,
        "generation": [
            {
                "id": "GEN-A",
                "kind": "gas_engine",
                "nameplate_MW": 10,
                "capex_eur": 16000000,
                "lead_time_weeks": 44,
            }
        ],
        "bess": [
            {
                "id": "BESS-1",
                "power_MW": 10,
                "energy_MWh": 40,
                "capex_eur": 9000000,
                "lead_time_weeks": 30,
            }
        ],
        "ride_through_hours": 3,
        "redundancy": "N+1",
    }
    qualify = {
        "site_name": "Synthetic",
        "metro": "Frankfurt",
        "contracted_MW": 12,
        "current_site_peak_MW": 7.4,
        "firm_capacity_MVA": 14,
    }
    hall_response = {
        "supplier": "Synthetic Supplier A",
        "received_on": "2026-10-01",
        "valid_until": "2027-01-01",
        "currency": "EUR",
        "values": {
            "capex_eur": 1200000,
            "lead_time_weeks": 30,
            "install_weeks": 8,
            "outage_hours_per_unit": 6,
            "validity_days": 90,
        },
        "compliance": {"E-10": "YES", "E-11": "YES", "C-01": "YES"},
        "text": {"note": "synthetic"},
    }
    deploy_response = {
        "supplier": "Synthetic Supplier B",
        "received_on": "2026-10-01",
        "valid_until": "2027-01-01",
        "currency": "EUR",
        "values": {
            "capex_eur": 25000000,
            "lead_time_weeks": 44,
            "install_weeks": 12,
            "warranted_availability": 0.98,
            "validity_days": 90,
        },
        "compliance": {},
    }
    return [
        # every leaf of the intake, straight into the shared loader
        {
            "name": "loader",
            "route": "loader",
            "tier": "client",
            "base": intake,
            "intake_keys": [],
            "sample_intake_paths": 0,
        },
        plan("screen", "/v1/screen", {"intake": intake}, ["intake"]),
        plan("compare", "/v1/compare", {"intake": intake}, ["intake"]),
        plan("study", "/v1/study", {"intake": intake}, ["intake"]),
        plan("diff", "/v1/diff", {"before": intake, "after": intake}, ["before", "after"]),
        plan("deck", "/v1/deck", {"intake": intake}, ["intake"]),
        plan(
            "proposal",
            "/v1/proposal",
            {
                "intake": intake,
                "engagement": "density_screen",
                "valid_days": 30,
                "contact": "a@example.test",
                "format": "html",
            },
            ["intake"],
        ),
        plan("spec", "/v1/spec", {"intake": intake, "scenario": "full_dlc_btm"}, ["intake"]),
        plan("spec_list", "/v1/spec", {"intake": intake, "list": True}, ["intake"]),
        plan(
            "bids",
            "/v1/bids",
            {"intake": intake, "scenario": "full_dlc_btm", "responses": [hall_response]},
            ["intake"],
        ),
        plan(
            "power_deploy_spec",
            "/v1/power/deploy/spec",
            {**deploy, "project": "Synthetic", "format": "json"},
            [],
        ),
        plan(
            "power_deploy_bids",
            "/v1/power/deploy/bids",
            {**deploy, "responses": [deploy_response]},
            [],
        ),
        plan("portfolio", "/v1/portfolio", {"intakes": [intake, intake]}, ["intakes"]),
        plan(
            "power_assess", "/v1/power/assess", {"intake": intake, "target_racks": 40}, ["intake"]
        ),
        plan("power_deploy_assess", "/v1/power/deploy/assess", deploy, []),
        plan("qualify", "/v1/qualify", qualify, [], tier="public"),
        plan(
            "estimate",
            "/v1/estimate",
            {"endpoint": "/v1/screen", "body": {"intake": intake}, "hall_count": 2},
            ["body"],
            tier="public",
        ),
    ]


def run_fuzz(
    plans: list[dict[str, Any]],
    *,
    root: Path | None = None,
    seed: int = 7,
    slow_s: float = 3.0,
    timeout_s: float = 900.0,
    report_date: str = "2026-10-27",
) -> FuzzReport:
    """Run the plans against the product checkout. Raises ProductUnavailable if the runner itself fails (that is not a finding)."""
    product = root or product_dir()
    spec = {
        "product_dir": str(product),
        "seed": seed,
        "slow_s": slow_s,
        "report_date": report_date,
        "plans": plans,
    }
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-I", str(RUNNER)],
            input=json.dumps(spec),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")},
        )
    except subprocess.TimeoutExpired as exc:
        raise ProductUnavailable(
            f"fuzz runner exceeded {timeout_s:.0f}s (a hang is itself a finding: run with a smaller sample to locate it)"
        ) from exc
    if proc.returncode != 0:
        raise ProductUnavailable(f"fuzz runner failed: {proc.stderr[-600:]}")
    raw = json.loads(proc.stdout)
    counts = raw["counts"]
    return FuzzReport(
        calls=int(counts["calls"]),
        ok=int(counts["ok"]),
        clean=int(counts["clean"]),
        non_clean=tuple(
            Finding(
                plan=f["plan"],
                exception=f["exception"],
                message=f["message"],
                raised_in=f["raised_in"],
                count=int(f["count"]),
                where=tuple(f["where"]),
            )
            for f in raw["non_clean"]
        ),
        slow=tuple(
            SlowCall(plan=s["plan"], seconds=float(s["seconds"]), where=s["where"])
            for s in raw["slow"]
        ),
        per_plan={k: int(v) for k, v in raw["per_plan"].items()},
    )


def format_report(report: FuzzReport) -> list[str]:
    lines = [
        f"fuzz: {report.calls} calls: {report.ok} answered, {report.clean} refused cleanly, "
        f"{sum(f.count for f in report.non_clean)} escaped as a server fault, {len(report.slow)} slow"
    ]
    lines += [f"  {name}: {n} calls" for name, n in sorted(report.per_plan.items())]
    for f in report.non_clean:
        lines.append(
            f"NON-CLEAN [{f.plan}] x{f.count} {f.exception}: {f.message} (raised in {f.raised_in})"
        )
        lines += [f"    at {w}" for w in f.where]
    for s in report.slow:
        lines.append(f"SLOW {s.seconds}s [{s.plan}] {s.where}")
    lines.append("HEALTHY" if report.healthy else "UNHEALTHY")
    return lines
