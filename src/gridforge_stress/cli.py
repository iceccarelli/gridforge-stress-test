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
    sub.add_parser("validate-fixtures", help="check fixture manifests, checksums, labels, content")
    f = sub.add_parser(
        "fetch", help="LIVE: fetch one registered source (opt-in; network + credentials)"
    )
    f.add_argument("source_id")
    pr = sub.add_parser("probe", help="LIVE, read-only: show what a source's publisher page says")
    pr.add_argument("source_id", nargs="?")
    pr.add_argument("--all", action="store_true")
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
    if ns.cmd == "probe":
        return _probe(ns.source_id, ns.all)
    return 2


def _probe(source_id: str | None, everything: bool) -> int:
    from .fetch import FetchError, probe_source
    from .probe import summarize

    ids = list(load_sources()) if everything else [source_id or ""]
    failures = 0
    for sid in ids:
        try:
            body, man, headers = probe_source(sid)
            print(json.dumps(summarize(man, headers, body), indent=1, ensure_ascii=False))
        except FetchError as exc:
            failures += 1
            print(json.dumps({"source_id": sid, "probe": "NOT_READ", "reason": str(exc)}))
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
