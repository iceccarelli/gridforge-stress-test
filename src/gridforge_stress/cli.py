"""Command line: `gridforge-stress <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .doctor import run_doctor
from .fixtures import ok, validate_fixtures
from .paths import fixtures_dir
from .registry import load_scenarios, load_sources


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gridforge-stress")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="environment, registry, fixtures, product checkout, credentials")
    s = sub.add_parser("sources", help="source registry").add_subparsers(dest="sub", required=True)
    s.add_parser("list")
    s.add_parser(
        "lifecycle", help="the highest lifecycle state each source's recorded evidence supports"
    )
    sc = sub.add_parser("scenarios", help="scenario catalogue").add_subparsers(
        dest="sub", required=True
    )
    sc.add_parser("list")
    br = sub.add_parser(
        "bridge", help="set the product's grid-wait assumption beside observed queue history"
    )
    br.add_argument("intake", help="path to a GridForge intake JSON")
    br.add_argument("--region", default=None)
    br.add_argument(
        "--context", default="cod_2018_2025", choices=["all_cod_years", "cod_2018_2025"]
    )
    wf = sub.add_parser(
        "workflow",
        help="run the product's real compare/screen/diff journey on an intake and judge it (PASS/FAIL/BLOCKED)",
    )
    wf.add_argument("intake", help="path to a GridForge intake JSON")
    wf.add_argument("-o", "--output", default=None, help="write the decision record JSON here")
    fz = sub.add_parser(
        "fuzz",
        help="hostile-input fuzz of every product route (needs the product checkout); exit 1 if anything escapes as a server fault or is slow",
    )
    fz.add_argument(
        "--sample",
        type=int,
        default=12,
        help="intake paths sampled per route (the loader is always fuzzed exhaustively)",
    )
    fz.add_argument("--seed", type=int, default=7)
    fz.add_argument(
        "--slow", type=float, default=3.0, help="seconds after which a single call counts as slow"
    )
    sub.add_parser("mcp", help="serve the harness as an MCP server over stdio")
    sub.add_parser("validate-fixtures", help="check fixture manifests, checksums, labels, content")
    f = sub.add_parser(
        "fetch", help="LIVE: fetch one registered source (opt-in; network + credentials)"
    )
    f.add_argument("source_id")
    pr = sub.add_parser("probe", help="LIVE, read-only: show what a source's publisher page says")
    pr.add_argument("source_id", nargs="?")
    pr.add_argument("--all", action="store_true")
    pr.add_argument(
        "--profile",
        action="store_true",
        help="for the registered LBNL workbook URL: bounded value profile of the observed columns",
    )
    pr.add_argument(
        "--queue-stats",
        action="store_true",
        help="for the registered LBNL workbook URL: queue-entry to in-service durations computed in memory",
    )
    pr.add_argument(
        "--show-sheet",
        action="append",
        default=[],
        help="for a registered .xlsx probe URL: print the leading rows of this sheet (repeatable)",
    )
    pr.add_argument(
        "--peek-xlsx",
        action="store_true",
        help="for registered .xlsx probe URLs: read within the source cap, in memory, print sheet layout",
    )
    ns = p.parse_args(argv)

    if ns.cmd == "doctor":
        rep = run_doctor()
        for c in rep["checks"]:
            print(f"[{c['status']:>6}] {c['check']}: {c['detail']}")
        print("HEALTHY" if rep["healthy"] else "UNHEALTHY")
        return 0 if rep["healthy"] else 1
    if ns.cmd == "sources" and ns.sub == "lifecycle":
        from .lifecycle import lifecycle_report
        from .registry import load_json

        manifests = [load_json(p) for p in sorted(fixtures_dir().rglob("*.manifest.json"))]
        for r in lifecycle_report(load_sources(), manifests):
            print(f"{r.source_id:24} {r.state.name:24} {' '.join(r.flags)}")
        return 0
    if ns.cmd == "sources":
        for src in load_sources().values():
            v = src.raw["verification"]["status"]
            print(
                f"{src.id:24} access={src.access_status:22} live={src.live_fetch:26} verified={v}"
            )
        return 0
    if ns.cmd == "scenarios":
        for sc_ in load_scenarios():
            print(
                f"{sc_['id']} {sc_['implemented']:18} {sc_['title']}  [{', '.join(sc_['source_ids'])}]"
            )
        return 0
    if ns.cmd == "validate-fixtures":
        found = validate_fixtures(fixtures_dir())
        for x in found:
            print(f"{x.severity}: {x.where}: {x.code}: {x.message}")
        print("fixtures OK" if ok(found) else "fixtures FAILED")
        return 0 if ok(found) else 1
    if ns.cmd == "fetch":
        from .fetch import FetchError, fetch_source

        try:
            body, man = fetch_source(ns.source_id)
        except FetchError as exc:
            print(f"NOT FETCHED: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(man.to_dict(), indent=2),
            f"\n{len(body)} bytes (NOT stored: transport checks only; no content validator has run)",
        )
        return 0
    if ns.cmd == "bridge":
        from pathlib import Path

        from .context_bridge import BridgeError, bridge
        from .product_adapter import ProductUnavailable, call

        intake = json.loads(Path(ns.intake).read_text())
        try:
            screened = call("screen", {"intake": intake}, tier="client")
            if not screened.get("ok"):
                print(f"PRODUCT REFUSED: {screened.get('message')}", file=sys.stderr)
                return 2
            print(
                json.dumps(
                    bridge(screened["result"], intake, context_key=ns.context, region=ns.region),
                    indent=1,
                )
            )
        except (ProductUnavailable, BridgeError) as exc:
            print(f"NOT BRIDGED: {exc}", file=sys.stderr)
            return 2
        return 0
    if ns.cmd == "workflow":
        from pathlib import Path

        from .workflow import run

        record = run(json.loads(Path(ns.intake).read_text()))
        doc = record.to_json()
        if ns.output:
            Path(ns.output).write_text(json.dumps(doc, indent=2) + "\n")
        for c in record.checks:
            print(f"[{c.outcome:>7}] {c.id}: {c.claim}" + (f"  <- {c.detail}" if c.detail else ""))
        print(json.dumps(doc["summary"]))
        return record.exit_code
    if ns.cmd == "fuzz":
        from .fuzz import default_plans, format_report, reference_intake, run_fuzz
        from .product_adapter import ProductUnavailable

        try:
            report = run_fuzz(
                default_plans(reference_intake(), sample_intake_paths=ns.sample),
                seed=ns.seed,
                slow_s=ns.slow,
            )
        except ProductUnavailable as exc:
            print(f"NOT FUZZED: {exc}", file=sys.stderr)
            return 2
        print("\n".join(format_report(report)))
        return 0 if report.healthy else 1
    if ns.cmd == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main()
    if ns.cmd == "probe":
        return _probe(ns.source_id, ns.all, ns.peek_xlsx, ns.profile, ns.queue_stats, ns.show_sheet)
    return 2


EXIT_POLICY = (
    "exit 0: every probed target was read or deliberately policy-blocked (expected, not a failure); "
    "exit 3: at least one target was NOT_READ (HTTP error, guard refusal, oversize, unsafe workbook, "
    "unknown source or no registered dataset URL), which is actionable"
)


def _record(
    sid: str, url: str | None, kind: str, probe: str, reason: str, http_status: int | None = None
) -> dict[str, object]:
    """A probe line for a target that was not read: identity, URL, status, time and the truthful reason."""
    from .manifest import utc_now

    return {
        "source_id": sid,
        "url": url,
        "kind": kind,
        "probe": probe,
        "retrieval_status": "not_fetched",
        "http_status": http_status,
        "at": utc_now(),
        "reason": reason,
    }


def _probe(
    source_id: str | None,
    everything: bool,
    peek_xlsx: bool = False,
    profile: bool = False,
    queue_stats: bool = False,
    show_sheets: list[str] | None = None,
) -> int:
    from .fetch import FetchError, PolicyBlocked, probe_source
    from .probe import summarize
    from .xlsx_peek import UnsafeWorkbook

    registry = load_sources()
    ids = list(registry) if everything else [source_id or ""]
    tally = {"read": 0, "policy_blocked": 0, "not_read": 0}
    wants_workbook = peek_xlsx or profile or queue_stats or bool(show_sheets)
    kind = "dataset_inspection" if wants_workbook else "publisher_page"
    for sid in ids:
        src = registry.get(sid)
        targets: list[str | None]
        if wants_workbook:
            targets = [u for u in (src.data_urls if src else ()) if u.lower().endswith(".xlsx")]
            if not targets:
                tally["not_read"] += 1
                reason = (
                    "no registered .xlsx data_urls entry for this source (or unknown source id)"
                )
                print(json.dumps(_record(sid, None, kind, "NOT_READ", reason)))
                continue
        else:
            targets = [src.locator, *src.probe_urls] if src else [None]
        for target in targets:
            try:
                body, man, headers = probe_source(sid, url=target, dataset=wants_workbook)
                if wants_workbook:
                    from .queue import (
                        LBNL_2026_MAP,
                        LBNL_2026_PROFILE,
                        capacity_summary,
                        duration_summary,
                        parse_rows,
                    )
                    from .xlsx_peek import (
                        compact_report,
                        peek,
                        profile_lines,
                        profile_sheet,
                        sheet_rows,
                        show_sheet_lines,
                    )

                    head = summarize(man, headers, body)
                    print(
                        json.dumps(
                            {k: head[k] for k in ("source_id", "final_url", "bytes", "sha256")}
                        )
                    )
                    if queue_stats:
                        rows = sheet_rows(
                            body, LBNL_2026_PROFILE["sheet_name"], header_marker="q_id"
                        )
                        recs, found = parse_rows(rows, LBNL_2026_MAP)
                        errs = sum(1 for f in found if f.severity == "error")
                        print(f"parsed rows={len(rows)} kept={len(recs)} rejected={errs}")
                        for span in ((None, None), (2018, 2025)):
                            res = duration_summary(
                                recs,
                                cod_year_min=span[0],
                                cod_year_max=span[1],
                                group_by="region",
                            )
                            print(json.dumps(res, sort_keys=True))
                        print(
                            json.dumps({"capacity_summary": capacity_summary(recs)}, sort_keys=True)
                        )
                        for label, pop in (
                            ("any_status", "has_in_service_date"),
                            ("operational", "operational"),
                        ):
                            by_year = duration_summary(
                                recs,
                                cod_year_min=2005,
                                cod_year_max=2025,
                                group_by="cod_year",
                                population=pop,
                            )
                            n_all = by_year["overall"].get("n")
                            print(
                                f"{label}_by_cod_year_2005_2025: overall_n={n_all} "
                                f"excluded_order={by_year['excluded_impossible_date_order']}"
                            )
                            for yr, st in by_year["by_cod_year"].items():
                                print(f"  {yr} n={st['n']} median_months={st['median_months']}")
                        from datetime import date

                        from .queue import cohort_outcomes

                        anyp = duration_summary(
                            recs,
                            cod_year_min=2018,
                            cod_year_max=2025,
                            population="has_in_service_date",
                        )
                        print(
                            json.dumps(
                                {
                                    "any_status_cod_2018_2025": anyp["overall"],
                                    "excluded_order": anyp["excluded_impossible_date_order"],
                                },
                                sort_keys=True,
                            )
                        )
                        for yr in range(2010, 2020):
                            co = cohort_outcomes(recs, entry_year=yr, as_of=date(2026, 1, 1))
                            print(
                                f"cohort {yr}: entered={co['entered']} operational={co['operational']} "
                                f"withdrawn={co['withdrawn']} active={co['active_censored']} "
                                f"suspended={co['suspended_unresolved']} unknown={co['status_unknown']} "
                                f"share_operational_of_resolved={co['share_operational_of_resolved']}"
                            )
                    if show_sheets:
                        for name in show_sheets:
                            print("\n".join(show_sheet_lines(body, name)))
                    if profile:
                        print("\n".join(profile_lines(profile_sheet(body, **LBNL_2026_PROFILE))))
                    elif not (queue_stats or show_sheets):
                        print("\n".join(compact_report(peek(body, max_rows=10))))
                    tally["read"] += 1
                    continue
                report = summarize(man, headers, body)
                print(json.dumps(report, indent=1, ensure_ascii=False))
                tally["read"] += 1
            except PolicyBlocked as exc:  # the registry's deliberate gate (ERCOT): expected
                tally["policy_blocked"] += 1
                print(json.dumps(_record(sid, target, kind, "BLOCKED_BY_POLICY", str(exc))))
            except (FetchError, UnsafeWorkbook) as exc:  # includes guard refusals: actionable
                tally["not_read"] += 1
                status = getattr(exc, "http_status", None)
                print(json.dumps(_record(sid, target, kind, "NOT_READ", str(exc), status)))
    print(json.dumps({"probe_summary": {**tally, "exit_policy": EXIT_POLICY}}))
    return 0 if tally["not_read"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
