"""Hostile-input runner. Executed INSIDE the product's interpreter (`python -I _fuzz_runner.py`), never imported by the harness.

Reads one JSON plan document on stdin, drives the product's REAL request handlers in a single process (one subprocess per call would take
hours), and writes one JSON report on stdout. Standard library only. It does not judge the product's answers; it classifies each call:

  ok        the handler answered
  clean     the handler refused with an exception the HTTP layer turns into a 4xx (ApiError, IntakeError, ValueError, TypeError, KeyError,
            ArithmeticError), which is the intended way to refuse
  non_clean any other exception: the HTTP layer would answer 500 "engine error" and print a traceback for what is a caller's mistake
  slow      a call that took longer than slow_s seconds (recorded in addition to its class)

The hostile values are built here, not sent over the wire, because some of them (a structure nested thousands deep, a 10**400 integer) cannot
be serialised by the harness.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import random
import sys
import time
import traceback
from typing import Any

MAX_WHERE_PER_FINDING = 6


def _deep_list(n: int) -> Any:
    x: Any = 1
    for _ in range(n):
        x = [x]
    return x


def _deep_dict(n: int) -> Any:
    x: Any = 1
    for _ in range(n):
        x = {"a": x}
    return x


def hostile_values() -> list[tuple[str, Any]]:
    """Name and value, in a fixed order so a seed reproduces a run."""
    return [
        ("None", None),
        ("True", True),
        ("False", False),
        ("empty_str", ""),
        ("x", "x"),
        ("space", " "),
        ("str_1e999", "1e999"),
        ("str_NaN", "NaN"),
        ("str_-1", "-1"),
        ("zero", 0),
        ("neg_zero", -0.0),
        ("neg_one", -1),
        ("one", 1),
        ("2**63", 2**63),
        ("2**64", 2**64),
        ("-2**63", -(2**63)),
        ("10**400", 10**400),
        ("1e308", 1e308),
        ("-1e308", -1e308),
        ("5e-324", 5e-324),
        ("nan", float("nan")),
        ("inf", float("inf")),
        ("-inf", float("-inf")),
        ("empty_list", []),
        ("empty_dict", {}),
        ("list_of_empty_list", [[]]),
        ("list_of_None", [None]),
        ("value_None", {"value": None}),
        ("value_x", {"value": "x"}),
        ("value_1e308_E0", {"value": 1e308, "evidence": "E0"}),
        ("evidence_int", {"value": 1, "evidence": 5}),
        ("evidence_None", {"value": 1, "evidence": None}),
        ("nul_char", "\u0000"),
        ("unicode_50", "é" * 50),
        ("deep_list_3000", _deep_list(3000)),
        ("deep_dict_3000", _deep_dict(3000)),
        ("deep_list_600", _deep_list(600)),
        ("range_2000", list(range(2000))),
    ]


def leaf_paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Every key and index in a JSON-like document, each as a path from the root."""
    out: list[tuple[Any, ...]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append((*prefix, k))
            out.extend(leaf_paths(v, (*prefix, k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.append((*prefix, i))
            out.extend(leaf_paths(v, (*prefix, i)))
    return out


def set_at(doc: Any, path: tuple[Any, ...], value: Any) -> None:
    cur = doc
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


def intake_roots(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Paths of intake documents anywhere in a request body, found by shape (a dict with both `project` and `grid`), not by where a route puts them."""
    out: list[tuple[Any, ...]] = []
    if isinstance(node, dict):
        if "project" in node and "grid" in node:
            out.append(prefix)
        for k, v in node.items():
            out.extend(intake_roots(v, (*prefix, k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(intake_roots(v, (*prefix, i)))
    return out


#: Replacing a whole intake block tests type confusion, so one value per type class is enough; the full catalogue still goes to the loader plan
#: and to the sampled deeper paths. (The 22 shapes behind the /v1/deck faults were None, bool, str, int, float, NaN/inf, list, nested list.)
BLOCK_VALUE_NAMES = (
    "None", "True", "x", "empty_str", "one", "neg_one", "10**400", "1e308", "nan", "inf",
    "empty_list", "empty_dict", "list_of_None",
)  # fmt: skip


def split_paths(
    base: Any, intake_keys: list[str], sample: int, rng: random.Random
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """(paths that get the full hostile catalogue, intake blocks that get one value per type class).

    The full set: everything outside the intake documents plus a seeded sample of their deeper paths. The blocks: every direct child of every
    intake document, always. Without them a sample of a dozen from ~110 paths drew about one of an intake's twelve top-level blocks per route,
    so a hostile `intake.project` (the cause of 29 faults on /v1/deck) was never reached."""
    roots = set(intake_roots(base))
    outside: list[tuple[Any, ...]] = []
    inside: list[tuple[Any, ...]] = []
    blocks: list[tuple[Any, ...]] = []
    for path in leaf_paths(base):
        # replacing a whole top-level key (e.g. "intake") is cheap and always worth trying; only the paths inside it are sampled
        if len(path) > 1 and path[0] in intake_keys:
            (blocks if path[:-1] in roots else inside).append(path)
        else:
            outside.append(path)
    if sample < len(inside):
        inside = sorted(rng.sample(inside, sample), key=lambda p: [str(x) for x in p])
    return outside + inside, blocks


def choose_paths(
    base: Any, intake_keys: list[str], sample: int, rng: random.Random
) -> list[tuple[Any, ...]]:
    full, blocks = split_paths(base, intake_keys, sample, rng)
    return full + blocks


class Classifier:
    def __init__(self, server: Any, loader: Any, tier_enum: Any, slow_s: float) -> None:
        self.server = server
        self.loader = loader
        self.tier_enum = tier_enum
        self.slow_s = slow_s
        self.clean_types: tuple[type[BaseException], ...] = (
            server.ApiError,
            loader.IntakeError,
            ValueError,
            TypeError,
            KeyError,
            ArithmeticError,
        )
        self.counts = {"calls": 0, "ok": 0, "clean": 0, "non_clean": 0, "slow": 0}
        self.findings: dict[str, dict[str, Any]] = {}
        self.slow: list[dict[str, Any]] = []

    def call(self, plan: dict[str, Any], body: Any, where: str) -> None:
        self.counts["calls"] += 1
        started = time.monotonic()
        try:
            if plan["route"] == "loader":
                self.loader.load_document(body)
            else:
                handler = self.server.ROUTES[plan["route"]][0]
                self.server._invoke(plan["route"], handler, body, self.tier_enum(plan["tier"]))
            self.counts["ok"] += 1
        except self.clean_types:
            self.counts["clean"] += 1
        except BaseException as exc:  # classifying every escape is the point
            self.counts["non_clean"] += 1
            frames = [f for f in traceback.extract_tb(exc.__traceback__)]
            top = frames[-1] if frames else None
            site = f"{top.filename.split('gridforge/')[-1]}:{top.lineno} {top.name}" if top else "?"
            key = f"{plan['name']}|{type(exc).__name__}|{str(exc)[:70]}|{site}"
            entry = self.findings.setdefault(
                key,
                {
                    "plan": plan["name"],
                    "exception": type(exc).__name__,
                    "message": str(exc)[:200],
                    "raised_in": site,
                    "count": 0,
                    "where": [],
                },
            )
            entry["count"] += 1
            if len(entry["where"]) < MAX_WHERE_PER_FINDING:
                entry["where"].append(where)
        finally:
            elapsed = time.monotonic() - started
            if elapsed > self.slow_s:
                self.counts["slow"] += 1
                self.slow.append(
                    {"plan": plan["name"], "seconds": round(elapsed, 2), "where": where}
                )


def run(spec: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, spec["product_dir"])
    os.environ["GRIDFORGE_REPORT_DATE"] = spec.get("report_date", "2026-10-27")
    server = importlib.import_module("gridforge.api.server")
    tiers = importlib.import_module("gridforge.api.tiers")
    loader = importlib.import_module("gridforge.intake.loader")
    cls = Classifier(server, loader, tiers.Tier, float(spec.get("slow_s", 3.0)))
    rng = random.Random(int(spec.get("seed", 7)))  # noqa: S311 - a seeded sample must be reproducible; nothing here is security-sensitive
    hostile = hostile_values()
    per_plan: dict[str, int] = {}
    for plan in spec["plans"]:
        before = cls.counts["calls"]
        base = plan["base"]
        full, blocks = split_paths(
            base, plan.get("intake_keys", []), int(plan.get("sample_intake_paths", 40)), rng
        )
        block_values = [(n, v) for n, v in hostile if n in BLOCK_VALUE_NAMES]
        for path, values in [(p, hostile) for p in full] + [(p, block_values) for p in blocks]:
            for name, value in values:
                body = copy.deepcopy(base)
                set_at(body, path, value)
                cls.call(plan, body, f"{plan['name']} {'.'.join(str(p) for p in path)}={name}")
        per_plan[plan["name"]] = cls.counts["calls"] - before
    return {
        "counts": cls.counts,
        "per_plan": per_plan,
        "non_clean": sorted(
            cls.findings.values(), key=lambda f: (f["plan"], f["exception"], f["raised_in"])
        ),
        "slow": sorted(cls.slow, key=lambda s: -s["seconds"])[:20],
    }


if __name__ == "__main__":
    report = run(json.load(sys.stdin))
    json.dump(report, sys.stdout)
