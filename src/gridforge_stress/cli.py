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
    sc = sub.add_parser("scenarios", help="scenario catalogue").add_subparsers(
        dest="sub", required=True
    )
    sc.add_parser("list")
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
    if ns.cmd == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main()
    if ns.cmd == "probe":
        return _probe(ns.source_id, ns.all, ns.peek_xlsx, ns.profile, ns.queue_stats, ns.show_sheet)
    return 2


def _probe(
    source_id: str | None,
    everything: bool,
    peek_xlsx: bool = False,
    profile: bool = False,
    queue_stats: bool = False,
    show_sheets: list[str] | None = None,
) -> int:
    from .fetch import FetchBlocked, FetchError, probe_source
    from .probe import summarize
    from .xlsx_peek import UnsafeWorkbook

    registry = load_sources()
    ids = list(registry) if everything else [source_id or ""]
    failures = 0
    for sid in ids:
        src = registry.get(sid)
        targets: list[str | None] = [None]
        if src is not None:
            targets += list(src.probe_urls)
        for target in targets:
            try:
                wants_workbook = peek_xlsx or profile or queue_stats or bool(show_sheets)
                is_xlsx = wants_workbook and bool(target) and str(target).lower().endswith(".xlsx")
                if wants_workbook and not is_xlsx:
                    continue
                body, man, headers = probe_source(sid, url=target, allow_large=is_xlsx)
                if is_xlsx:
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
                    if show_sheets:
                        for name in show_sheets:
                            print("\n".join(show_sheet_lines(body, name)))
                    if profile:
                        print("\n".join(profile_lines(profile_sheet(body, **LBNL_2026_PROFILE))))
                    elif not (queue_stats or show_sheets):
                        print("\n".join(compact_report(peek(body, max_rows=10))))
                    continue
                report = summarize(man, headers, body)
                print(json.dumps(report, indent=1, ensure_ascii=False))
            except FetchBlocked as exc:  # policy refusal (e.g. ERCOT): expected, not a read failure
                print(
                    json.dumps(
                        {
                            "source_id": sid,
                            "url": target,
                            "probe": "BLOCKED_BY_POLICY",
                            "reason": str(exc),
                        }
                    )
                )
            except (FetchError, UnsafeWorkbook) as exc:
                failures += 1
                print(
                    json.dumps(
                        {"source_id": sid, "url": target, "probe": "NOT_READ", "reason": str(exc)}
                    )
                )
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
