from __future__ import annotations

import io
import json

import pytest

from gridforge_stress import mcp_server as M

pytestmark = pytest.mark.unit


def rpc(method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


@pytest.fixture
def server():
    s = M.Server()
    s.handle(
        rpc(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
        )
    )
    s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return s


def call(server, name, args=None, mid=2):
    r = server.handle(rpc("tools/call", {"name": name, "arguments": args or {}}, mid))
    assert r is not None and "result" in r, r
    return r["result"]


def payload(result):
    return json.loads(result["content"][0]["text"])


# --- protocol -----------------------------------------------------------------------------------------------------


def test_initialize_negotiates_known_versions_and_falls_back_to_latest():
    for want, got in (
        ("2024-11-05", "2024-11-05"),
        ("2025-03-26", "2025-03-26"),
        ("1999-01-01", M.SUPPORTED_PROTOCOLS[0]),
    ):
        r = M.Server().handle(rpc("initialize", {"protocolVersion": want}))
        assert r["result"]["protocolVersion"] == got
        assert r["result"]["serverInfo"]["name"] == "gridforge-stress"
        assert r["result"]["capabilities"] == {"tools": {"listChanged": False}}


def test_notifications_get_no_response_and_ping_works():
    s = M.Server()
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/whatever"}) is None
    assert s.handle(rpc("ping"))["result"] == {}


@pytest.mark.parametrize(
    "msg,code",
    [
        ([], M.INVALID_REQUEST),
        ("text", M.INVALID_REQUEST),
        ({"jsonrpc": "1.0", "id": 1, "method": "ping"}, M.INVALID_REQUEST),
        ({"jsonrpc": "2.0", "id": 1}, M.INVALID_REQUEST),
        ({"jsonrpc": "2.0", "id": 1, "method": 5}, M.INVALID_REQUEST),
        (rpc("no/such/method"), M.METHOD_NOT_FOUND),
        (rpc("tools/call", {"name": "nope"}), M.INVALID_PARAMS),
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1]}, M.INVALID_PARAMS),
    ],
)
def test_protocol_faults_are_jsonrpc_errors(msg, code):
    assert M.Server().handle(msg)["error"]["code"] == code


def test_stdio_loop_handles_garbage_blank_lines_and_keeps_stdout_clean():
    lines = "\n".join(
        [
            json.dumps(rpc("initialize", {"protocolVersion": "2025-06-18"}, 1)),
            "",
            "{not json",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(rpc("tools/list", None, 2)),
            json.dumps([rpc("ping")]),  # batch: rejected
        ]
    )
    out = io.StringIO()
    M.serve(io.StringIO(lines + "\n"), out)
    responses = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r.get("id") for r in responses] == [1, None, 2, None]
    assert (
        responses[1]["error"]["code"] == M.PARSE_ERROR
        and responses[3]["error"]["code"] == M.INVALID_REQUEST
    )
    assert all(r["jsonrpc"] == "2.0" for r in responses)


def test_oversized_message_is_refused():
    out = io.StringIO()
    M.serve(io.StringIO("x" * (M.MAX_LINE_BYTES + 10) + "\n"), out)
    assert json.loads(out.getvalue())["error"]["code"] == M.INVALID_REQUEST


# --- tools/list ---------------------------------------------------------------------------------------------------


def test_every_tool_is_listed_with_a_valid_strict_input_schema():
    from jsonschema import Draft202012Validator

    r = M.Server().handle(rpc("tools/list"))["result"]["tools"]
    assert {t["name"] for t in r} == set(M.TOOLS)
    for t in r:
        Draft202012Validator.check_schema(t["inputSchema"])
        assert t["inputSchema"]["additionalProperties"] is False and t["description"]
        assert t["annotations"]["readOnlyHint"] is True
    assert not any("url" in t["name"] and t["name"] != "probe_source" for t in r), (
        "no fetch-any-URL tool"
    )
    assert next(t for t in r if t["name"] == "probe_source")["annotations"]["openWorldHint"] is True


# --- tools --------------------------------------------------------------------------------------------------------


def test_registry_tools_report_what_was_verified_honestly(server):
    rows = payload(call(server, "list_sources"))
    by = {r["id"]: r for r in rows}
    assert len(rows) == 9 and by["ercot-data-portal"]["live_fetch"] == "blocked_until_authorized"
    assert all(r["live_fetch"] != "enabled" for r in rows)
    src = payload(call(server, "get_source", {"source_id": "lbnl-queued-up"}))
    assert (
        "CC BY 4.0" in src["license_attribution"]
        and src["verification"]["status"] == "publisher_page_read"
    )
    bad = call(server, "get_source", {"source_id": "nope"})
    assert bad["isError"] is True and "unknown source id" in payload(bad)["error"]


def test_scenarios_doctor_and_fixtures_tools(server):
    assert {s["id"] for s in payload(call(server, "list_scenarios"))} == {
        f"GF-00{i}" for i in range(1, 9)
    }
    assert payload(call(server, "doctor"))["healthy"] is True
    fx = payload(call(server, "validate_fixtures"))
    assert fx["ok"] is True and all(f["severity"] == "warning" for f in fx["findings"])


def item(kind, retrieval="fresh", sid="s"):
    return {"source_id": sid, "kind": kind, "retrieval": retrieval}


def test_assess_evidence_tool_enforces_the_invariants_through_the_protocol(server):
    ctx = payload(
        call(server, "assess_evidence", {"items": [item("balancing_authority_observation")]})
    )
    assert ctx["level"] == "context_only" and ctx["allows_site_specific_claim"] is False
    site = payload(call(server, "assess_evidence", {"items": [item("utility_load_letter")]}))
    assert site["allows_site_specific_claim"] is True
    stale = payload(
        call(server, "assess_evidence", {"items": [item("utility_load_letter", "stale")]})
    )
    assert (
        stale["allows_site_specific_claim"] is False and stale["level_index"] < site["level_index"]
    )
    failed = payload(
        call(server, "assess_evidence", {"items": [item("utility_load_letter", "failed")]})
    )
    assert failed["level"] == "none"
    modeled = payload(call(server, "assess_evidence", {"items": [item("modeled_scenario")]}))
    assert modeled["level"] == "context_only"


@pytest.mark.parametrize(
    "name,args",
    [
        ("assess_evidence", {"items": [{"source_id": "s", "kind": "vibes", "retrieval": "fresh"}]}),
        ("assess_evidence", {"items": "nope"}),
        ("assess_evidence", {}),
        ("get_source", {"source_id": 5}),
        ("get_source", {"source_id": "x", "extra": 1}),
        ("parse_queue_rows", {"rows": [], "mapping": "other"}),
        ("product_call", {"endpoint": "study", "body": {}}),
        ("product_call", {"endpoint": "compare", "body": {}, "tier": "internal"}),
    ],
)
def test_invalid_arguments_are_tool_errors_not_crashes(server, name, args):
    r = call(server, name, args)
    assert r["isError"] is True and "invalid arguments" in payload(r)["error"]


def test_parse_queue_rows_flags_instead_of_dropping_and_reports_cohort(server):
    rows = [
        {
            "q_id": "Q1",
            "entity": "APS",
            "q_status": "active",
            "q_date": "43511",
            "on_date": "44000",
            "mw_1": "10",
        },
        {
            "q_id": "Q2",
            "entity": "APS",
            "q_status": "operational",
            "q_date": "42005",
            "on_date": "43831",
            "mw_1": "20",
        },
        {"q_id": "Q3", "entity": "APS", "q_status": "mystery", "q_date": "42005", "mw_1": "5"},
        {"q_id": "Q4", "entity": "APS", "q_status": "withdrawn", "q_date": "", "mw_1": "5"},
    ]
    out = payload(
        call(
            server,
            "parse_queue_rows",
            {
                "rows": rows,
                "mapping": "lbnl_2026",
                "entry_year": 2015,
                "as_of": "2026-01-01",
                "durations": True,
            },
        )
    )
    assert (out["rows_in"], out["kept"], out["rejected"]) == (4, 2, 2)
    assert out["findings_by_code"]["status_date_conflict"] == {"warning": 1}
    assert out["findings_by_code"]["unknown_status"] == {"error": 1}
    assert out["flag_counts"]["status_date_conflict"] == 1
    assert (
        out["cohort_outcomes"]["entered"] == 1
        and "not a data-centre load-connection" in out["cohort_outcomes"]["label"]
    )
    assert out["duration_summary"]["overall"]["n"] == 1


def test_parse_queue_rows_requires_as_of_with_entry_year_and_caps_rows(server):
    r = call(server, "parse_queue_rows", {"rows": [], "mapping": "lbnl_2026", "entry_year": 2015})
    assert r["isError"] and "as_of" in payload(r)["error"]
    too_many = [{"q_id": str(i)} for i in range(M.MAX_QUEUE_ROWS + 1)]
    r = call(server, "parse_queue_rows", {"rows": too_many, "mapping": "lbnl_2026"})
    assert r["isError"] is True


def test_probe_source_is_off_unless_the_operator_enables_it(server, monkeypatch):
    monkeypatch.delenv(M.LIVE_ENV, raising=False)
    r = call(server, "probe_source", {"source_id": "eia-930"})
    assert r["isError"] and M.LIVE_ENV in payload(r)["error"]
    monkeypatch.setenv(M.LIVE_ENV, "1")
    seen = []

    def fake(sid, url=None, **kw):
        from gridforge_stress import fetch as F

        seen.append((sid, url))
        man = F.Manifest(
            source_id=sid,
            source_url="https://www.eia.gov/x",
            origin="live_fetch",
            retrieval_status="fetched_unparsed",
            attribution="a",
            byte_count=3,
            response_status=200,
        )
        return b"<title>T</title>", man, {"content-type": "text/html"}

    from gridforge_stress import fetch as F

    monkeypatch.setattr(F, "probe_source", fake)
    ok = payload(call(server, "probe_source", {"source_id": "eia-930"}))
    assert ok["http_status"] == 200 and ok["title"] == "T" and seen == [("eia-930", None)]


def test_probe_source_cannot_be_pointed_at_arbitrary_urls_or_blocked_sources(server, monkeypatch):
    monkeypatch.setenv(M.LIVE_ENV, "1")
    for args in (
        {"source_id": "ercot-data-portal"},
        {"source_id": "http://169.254.169.254/"},
        {"source_id": "eia-930", "url": "https://evil.example/"},
    ):
        r = call(server, "probe_source", args)
        assert r["isError"] is True, args


def test_unexpected_exceptions_do_not_leak_internals(server, monkeypatch):
    def boom(_):
        raise RuntimeError("secret internal path /etc/shadow")

    monkeypatch.setitem(M.TOOLS["list_sources"], "fn", boom)
    r = call(server, "list_sources")
    assert (
        r["isError"] is True
        and "shadow" not in json.dumps(r)
        and "internal error" in payload(r)["error"]
    )


def test_large_output_is_truncated_with_a_notice(server, monkeypatch):
    monkeypatch.setitem(M.TOOLS["list_sources"], "fn", lambda _: ["x" * 1000] * 1000)
    r = call(server, "list_sources")
    text = r["content"][0]["text"]
    assert (
        len(text) < M.MAX_OUTPUT_CHARS + 200
        and "[truncated" in text
        and "structuredContent" not in r
    )


def test_structured_content_only_for_protocols_that_define_it():
    new = M.Server()
    new.handle(rpc("initialize", {"protocolVersion": "2025-06-18"}))
    old = M.Server()
    old.handle(rpc("initialize", {"protocolVersion": "2024-11-05"}))
    args = {"source_id": "eia-930"}
    assert (
        "structuredContent"
        in new.handle(rpc("tools/call", {"name": "get_source", "arguments": args}))["result"]
    )
    assert (
        "structuredContent"
        not in old.handle(rpc("tools/call", {"name": "get_source", "arguments": args}))["result"]
    )


def test_real_subprocess_speaks_clean_json_rpc_over_stdio():
    """What an MCP client actually does: spawn the server, talk over pipes, expect ONLY protocol lines on stdout."""
    import os
    import subprocess
    import sys

    msgs = [
        rpc(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "e2e"}},
            1,
        ),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        rpc("tools/list", None, 2),
        rpc("tools/call", {"name": "list_sources", "arguments": {}}, 3),
        rpc(
            "tools/call",
            {"name": "assess_evidence", "arguments": {"items": [item("generic_tariff")]}},
            4,
        ),
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "gridforge_stress.cli", "mcp"],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    lines = proc.stdout.splitlines()
    parsed = [json.loads(x) for x in lines]  # every stdout line must be valid JSON
    assert [p["id"] for p in parsed] == [1, 2, 3, 4]
    assert len(parsed[1]["result"]["tools"]) == len(M.TOOLS)
    assert len(json.loads(parsed[2]["result"]["content"][0]["text"])) == 9
    assert json.loads(parsed[3]["result"]["content"][0]["text"])["level"] == "context_only"


def test_parse_queue_rows_capacity_option_counts_requests_once(server):
    base = {"entity": "APS", "q_status": "active", "q_date": "43511", "mw_1": "100"}
    rows = [
        {**base, "q_id": "Q1", "county": "A"},
        {**base, "q_id": "Q1", "county": "B"},
        {**base, "q_id": "Q2"},
    ]
    out = payload(
        call(server, "parse_queue_rows", {"rows": rows, "mapping": "lbnl_2026", "capacity": True})
    )
    cap = out["capacity_summary"]
    assert cap["naive_row_sum"] == 300.0 and cap["deduplicated_sum"] == 200.0
    assert cap["double_counting_avoided_mw"] == 100.0 and out["flag_counts"]["duplicate_key"] == 2
