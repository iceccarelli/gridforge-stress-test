"""A small, dependency-free MCP (Model Context Protocol) server over stdio for the gridforge-stress harness.

It exposes the harness's capabilities as tools so an agent can drive them: the source registry, the evidence
rubric, the queue parser, fixture validation, and the REAL GridForge-AI product handlers. Transport is newline-
delimited JSON-RPC 2.0 on stdin/stdout (stdout carries protocol messages only; diagnostics go to stderr).

Safety rules carried over from the harness (CLAUDE.md):
* there is NO tool that fetches an arbitrary URL; `probe_source` reads only a registered source's own locator and
  is OFF unless GRIDFORGE_STRESS_ALLOW_LIVE=1 is set by the operator;
* product calls go only to the allow-listed handlers via `product_adapter`, with a size cap;
* every tool's arguments are validated against its JSON schema before anything runs;
* tool failures are reported as MCP tool errors (isError), protocol faults as JSON-RPC errors; internals and
  tracebacks are never returned to the client.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from datetime import date
from typing import IO, Any

from jsonschema import Draft202012Validator

from . import __version__

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_LINE_BYTES = 6_000_000
MAX_OUTPUT_CHARS = 200_000
MAX_PRODUCT_BODY_CHARS = 1_000_000
MAX_QUEUE_ROWS = 20_000
LIVE_ENV = "GRIDFORGE_STRESS_ALLOW_LIVE"

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700,
    -32600,
    -32601,
    -32602,
    -32603,
)


class ToolError(Exception):
    """A failure the caller should see as a tool result with isError=true."""


Tool = Callable[[Mapping[str, Any]], Any]


def _sources() -> dict[str, Any]:
    from .registry import load_sources

    return load_sources()


def tool_list_sources(_: Mapping[str, Any]) -> Any:
    return [
        {
            "id": s.id,
            "access_status": s.access_status,
            "live_fetch": s.live_fetch,
            "verification": s.raw["verification"]["status"],
            "credential_env": list(s.credential_env),
            "locator": s.locator,
        }
        for s in _sources().values()
    ]


def tool_get_source(args: Mapping[str, Any]) -> Any:
    src = _sources().get(str(args["source_id"]))
    if src is None:
        raise ToolError(f"unknown source id {args['source_id']!r}; known: {sorted(_sources())}")
    return src.raw


def tool_list_scenarios(_: Mapping[str, Any]) -> Any:
    from .registry import load_scenarios

    return load_scenarios()


def tool_doctor(_: Mapping[str, Any]) -> Any:
    from .doctor import run_doctor

    return run_doctor()


def tool_validate_fixtures(_: Mapping[str, Any]) -> Any:
    from .fixtures import ok, validate_fixtures
    from .paths import fixtures_dir

    found = validate_fixtures(fixtures_dir())
    return {
        "ok": ok(found),
        "findings": [
            {"code": f.code, "severity": f.severity, "where": f.where, "message": f.message}
            for f in found
        ],
    }


def tool_assess_evidence(args: Mapping[str, Any]) -> Any:
    from .evidence import EvidenceItem, assess

    items = [
        EvidenceItem(i["source_id"], i["kind"], i["retrieval"], i.get("note", ""))
        for i in args["items"]
    ]
    return assess(items)


def tool_parse_queue_rows(args: Mapping[str, Any]) -> Any:
    from .fixtures import QUEUE_MAP
    from .queue import (
        LBNL_2026_MAP,
        capacity_summary,
        cohort_outcomes,
        duration_summary,
        parse_rows,
    )

    rows = args["rows"]
    if len(rows) > MAX_QUEUE_ROWS:
        raise ToolError(f"at most {MAX_QUEUE_ROWS} rows per call (got {len(rows)})")
    cmap = {"lbnl_2026": LBNL_2026_MAP, "synthetic_csv": QUEUE_MAP}[args["mapping"]]
    recs, found = parse_rows(rows, cmap)
    by_code: dict[str, dict[str, int]] = {}
    for f in found:
        by_code.setdefault(f.code, {}).setdefault(f.severity, 0)
        by_code[f.code][f.severity] += 1
    out: dict[str, Any] = {
        "rows_in": len(rows),
        "kept": len(recs),
        "rejected": sum(1 for f in found if f.severity == "error"),
        "findings_by_code": by_code,
        "example_findings": [
            {"code": f.code, "severity": f.severity, "where": f.where, "message": f.message}
            for f in found[:20]
        ],
        "flag_counts": _count_flags(recs),
    }
    if args.get("entry_year") is not None:
        try:
            as_of = date.fromisoformat(args["as_of"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ToolError("as_of (YYYY-MM-DD) is required with entry_year") from exc
        out["cohort_outcomes"] = cohort_outcomes(recs, entry_year=args["entry_year"], as_of=as_of)
    if args.get("capacity"):
        out["capacity_summary"] = capacity_summary(recs)
    if args.get("durations"):
        out["duration_summary"] = duration_summary(recs, group_by=args.get("group_by"))
    return out


def _count_flags(recs: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in recs:
        for fl in r.flags:
            counts[fl] = counts.get(fl, 0) + 1
    return counts


def tool_product_call(args: Mapping[str, Any]) -> Any:
    from .product_adapter import ENDPOINTS, ProductUnavailable, call, claim_violations

    endpoint = args["endpoint"]
    if endpoint not in ENDPOINTS:
        raise ToolError(f"endpoint must be one of {sorted(ENDPOINTS)}")
    body = args["body"]
    if len(json.dumps(body)) > MAX_PRODUCT_BODY_CHARS:
        raise ToolError(f"body larger than {MAX_PRODUCT_BODY_CHARS} characters")
    try:
        result = call(endpoint, body, tier=args.get("tier", "client"))
    except ProductUnavailable as exc:
        raise ToolError(f"GridForge-AI product unavailable: {exc}") from exc
    out: dict[str, Any] = {"endpoint": endpoint, "product_result": result}
    level = args.get("evidence_level")
    if level and endpoint == "compare" and result.get("ok"):
        out["claim_violations"] = claim_violations(result["result"], level)
        out["evidence_level"] = level
    return out


def tool_context_bridge(args: Mapping[str, Any]) -> Any:
    from .context_bridge import BridgeError, bridge
    from .product_adapter import ProductUnavailable, call

    intake = args["intake"]
    if len(json.dumps(intake)) > MAX_PRODUCT_BODY_CHARS:
        raise ToolError(f"intake larger than {MAX_PRODUCT_BODY_CHARS} characters")
    try:
        screened = call("screen", {"intake": intake}, tier="client")
    except ProductUnavailable as exc:
        raise ToolError(f"GridForge-AI product unavailable: {exc}") from exc
    if not screened.get("ok"):
        raise ToolError(
            f"the product refused the intake: {screened.get('error')}: {screened.get('message')}"
        )
    try:
        return bridge(
            screened["result"],
            intake,
            context_key=args.get("context", "cod_2018_2025"),
            region=args.get("region"),
        )
    except BridgeError as exc:
        raise ToolError(str(exc)) from exc


def tool_probe_source(args: Mapping[str, Any]) -> Any:
    if os.environ.get(LIVE_ENV) != "1":
        raise ToolError(
            f"live reads are disabled; the operator must set {LIVE_ENV}=1 to enable probe_source"
        )
    from .fetch import FetchError, probe_source
    from .probe import summarize

    try:
        body, man, headers = probe_source(args["source_id"], url=args.get("url"))
    except FetchError as exc:
        raise ToolError(str(exc)) from exc
    return summarize(man, headers, body)


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


_NONE = _schema({})
_EVIDENCE_KINDS = [
    "utility_load_letter",
    "load_interconnection_study",
    "allocation_document",
    "balancing_authority_observation",
    "generation_queue_statistic",
    "candidate_poi_listing",
    "generic_tariff",
    "weather",
    "modeled_scenario",
]

TOOLS: dict[str, dict[str, Any]] = {
    "list_sources": {
        "description": "List the registered data sources with access status, verification status and whether live fetch is enabled.",
        "schema": _NONE,
        "fn": tool_list_sources,
    },
    "get_source": {
        "description": "Full registry record for one source: owner, locator, licence/attribution, units, time convention, limitations and what was actually verified.",
        "schema": _schema({"source_id": {"type": "string"}}, ["source_id"]),
        "fn": tool_get_source,
    },
    "list_scenarios": {
        "description": "The scenario catalogue (GF-001..GF-008): scope and what each must not claim.",
        "schema": _NONE,
        "fn": tool_list_scenarios,
    },
    "doctor": {
        "description": "Environment health: interpreter, registry/schemas, fixtures, product checkout, credential presence (never values).",
        "schema": _NONE,
        "fn": tool_doctor,
    },
    "validate_fixtures": {
        "description": "Validate fixture manifests, checksums, synthetic/source-derived labelling and content.",
        "schema": _NONE,
        "fn": tool_validate_fixtures,
    },
    "assess_evidence": {
        "description": "Evidence-sufficiency rubric: how much a time-to-power claim about a specific site may assert given the evidence on hand. Region/queue/tariff/modeled evidence never reaches site level; stale lowers; failed is excluded.",
        "schema": _schema(
            {
                "items": {
                    "type": "array",
                    "maxItems": 100,
                    "items": _schema(
                        {
                            "source_id": {"type": "string"},
                            "kind": {"enum": _EVIDENCE_KINDS},
                            "retrieval": {"enum": ["fresh", "stale", "failed", "synthetic"]},
                            "note": {"type": "string"},
                        },
                        ["source_id", "kind", "retrieval"],
                    ),
                }
            },
            ["items"],
        ),
        "fn": tool_assess_evidence,
    },
    "parse_queue_rows": {
        "description": "Parse interconnection-queue rows (as published, strings ok) with the harness parser: flags status/date conflicts, repeated keys and split capacity instead of dropping rows; optional cohort outcomes and duration summary. Queue statistics are NOT load-connection probabilities.",
        "schema": _schema(
            {
                "rows": {"type": "array", "maxItems": MAX_QUEUE_ROWS, "items": {"type": "object"}},
                "mapping": {"enum": ["lbnl_2026", "synthetic_csv"]},
                "entry_year": {"type": "integer", "minimum": 1980, "maximum": 2100},
                "as_of": {"type": "string", "description": "YYYY-MM-DD; required with entry_year"},
                "durations": {"type": "boolean"},
                "capacity": {
                    "type": "boolean",
                    "description": "capacity summed once per (entity, id) request",
                },
                "group_by": {"type": "string"},
            },
            ["rows", "mapping"],
        ),
        "fn": tool_parse_queue_rows,
    },
    "product_call": {
        "description": "Call the REAL GridForge-AI engine handler (compare/screen/diff) with an intake (or before/after for diff). Returns the product's actual output; for compare with evidence_level, also lists any claim stronger than that evidence supports.",
        "schema": _schema(
            {
                "endpoint": {"enum": ["compare", "screen", "diff"]},
                "body": {"type": "object"},
                "tier": {"enum": ["public", "client"]},
                "evidence_level": {
                    "enum": ["none", "context_only", "indicative", "site_supported"]
                },
            },
            ["endpoint", "body"],
        ),
        "fn": tool_product_call,
    },
    "context_bridge": {
        "description": "Run the REAL GridForge-AI screen on an intake, read the product's own grid-reinforcement lead-time assumption from it, and set it beside LBNL's observed US generation-queue request-to-in-service durations (attributed, source-derived). CONTEXT ONLY: it never changes the product's evidence class, is not a load-connection time, and is labelled 'scale reference only' unless the site is in the US.",
        "schema": _schema(
            {
                "intake": {"type": "object"},
                "context": {"enum": ["all_cod_years", "cod_2018_2025"]},
                "region": {
                    "type": "string",
                    "description": "LBNL region, e.g. PJM, ERCOT, CAISO, West",
                },
            },
            ["intake"],
        ),
        "fn": tool_context_bridge,
    },
    "probe_source": {
        "description": "OPERATOR-ENABLED ONLY (GRIDFORGE_STRESS_ALLOW_LIVE=1). Read-only look at a registered source's own page: status, content type, title, licence/credential/time-convention wording. No arbitrary URLs; ERCOT stays blocked.",
        "schema": _schema(
            {"source_id": {"type": "string"}, "url": {"type": "string"}}, ["source_id"]
        ),
        "fn": tool_probe_source,
    },
}
_VALIDATORS = {n: Draft202012Validator(t["schema"]) for n, t in TOOLS.items()}


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated {len(text) - MAX_OUTPUT_CHARS} characters]"


class Server:
    def __init__(self) -> None:
        self.initialized = False
        self.protocol = SUPPORTED_PROTOCOLS[0]

    def handle(self, msg: Any) -> dict[str, Any] | None:
        """One decoded JSON-RPC message in, response (or None for notifications) out."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(
                msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "invalid request"
            )
        method, mid = msg["method"], msg.get("id")
        is_notification = "id" not in msg
        if not isinstance(method, str):
            return _error(mid, INVALID_REQUEST, "method must be a string")
        if is_notification:
            if method == "notifications/initialized":
                self.initialized = True
            return None
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return _error(mid, INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            return self._initialize(mid, params)
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            tools = [
                {
                    "name": n,
                    "description": t["description"],
                    "inputSchema": t["schema"],
                    "annotations": {"readOnlyHint": True, "openWorldHint": n == "probe_source"},
                }
                for n, t in TOOLS.items()
            ]
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
        if method == "tools/call":
            return self._call(mid, params)
        return _error(mid, METHOD_NOT_FOUND, f"method not found: {method}")

    def _initialize(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        wanted = params.get("protocolVersion")
        self.protocol = wanted if wanted in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": self.protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "gridforge-stress", "version": __version__},
                "instructions": (
                    "Independent source-integrity and acceptance harness for GridForge Time-to-Power. "
                    "Outputs keep their labels: queue statistics are not load-connection probabilities, "
                    "balancing-authority data is not site headroom, Cambium is modeled, tariffs are generic, "
                    "a POI listing is not an allocation. Unverified registry fields say UNVERIFIED."
                ),
            },
        }

    def _call(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOLS:
            return _error(mid, INVALID_PARAMS, f"unknown tool {name!r}")
        args = params.get("arguments") or {}
        errors = sorted(_VALIDATORS[name].iter_errors(args), key=lambda e: list(e.path))
        if errors:
            detail = "; ".join(
                f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5]
            )
            return self._tool_result(mid, {"error": f"invalid arguments: {detail}"}, is_error=True)
        try:
            result = TOOLS[name]["fn"](args)
        except ToolError as exc:
            return self._tool_result(mid, {"error": str(exc)}, is_error=True)
        except Exception as exc:  # never leak internals to the client
            print(
                f"[gridforge-stress mcp] tool {name} failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return self._tool_result(
                mid, {"error": f"internal error in tool {name}"}, is_error=True
            )
        return self._tool_result(mid, result, is_error=False)

    def _tool_result(self, mid: Any, payload: Any, *, is_error: bool) -> dict[str, Any]:
        text = _truncate(json.dumps(payload, indent=1, ensure_ascii=False, default=str))
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
        if (
            self.protocol >= "2025-06-18"
            and isinstance(payload, dict)
            and len(text) <= MAX_OUTPUT_CHARS
        ):
            result["structuredContent"] = payload
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(inp: IO[str], out: IO[str]) -> None:
    server = Server()
    for line in inp:
        line = line.strip()
        if not line:
            continue
        if len(line.encode("utf-8", "ignore")) > MAX_LINE_BYTES:
            resp: dict[str, Any] | None = _error(None, INVALID_REQUEST, "message too large")
        else:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                resp = _error(None, PARSE_ERROR, "parse error")
            else:
                try:
                    resp = server.handle(msg)
                except Exception as exc:  # protocol-level safety net
                    print(f"[gridforge-stress mcp] {type(exc).__name__}: {exc}", file=sys.stderr)
                    resp = _error(
                        msg.get("id") if isinstance(msg, dict) else None,
                        INTERNAL_ERROR,
                        "internal error",
                    )
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False, separators=(",", ":")) + "\n")
            out.flush()


def main() -> int:
    serve(sys.stdin, sys.stdout)
    return 0
