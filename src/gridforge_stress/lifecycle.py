"""Source lifecycle: the highest state the RECORDED EVIDENCE supports, never the highest one anybody claims.

    UNVERIFIED -> PUBLISHER_PAGE_READ -> DATA_ACCESS_CONFIRMED -> FETCHED_UNPARSED -> CONTENT_VALIDATED
               -> SOURCE_DERIVED_FIXTURE -> READY_FOR_ORACLE

Each state needs the one before it. A state is reached only through its own evidence:
  PUBLISHER_PAGE_READ    registry verification.status == publisher_page_read
  DATA_ACCESS_CONFIRMED  the registry entry carries a `data_access` record (a dataset URL that answered, with when, how and how big)
  FETCHED_UNPARSED       a live_fetch manifest with retrieval_status fetched_unparsed (or beyond)
  CONTENT_VALIDATED      a live_fetch manifest with retrieval_status fetched_validated
  SOURCE_DERIVED_FIXTURE a source_derived_fixture manifest with complete provenance, from a source at CONTENT_VALIDATED or beyond
  READY_FOR_ORACLE       a SOURCE_DERIVED_FIXTURE whose parser and corruption tests are recorded (`oracle_ready` in the registry entry)
Terminal flags are reported alongside, not instead: BLOCKED_BY_POLICY, ACCESS_RESTRICTED, NOT_READ, FAILED_VALIDATION.

A fixture built outside this ladder (for example by a one-off GET that was not a probe run) is reported as `out_of_ladder_fixture`: it never raises
the source's state. That is the point: a fixture existing is not the source being verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .registry import Source


class State(IntEnum):
    UNVERIFIED = 0
    PUBLISHER_PAGE_READ = 1
    DATA_ACCESS_CONFIRMED = 2
    FETCHED_UNPARSED = 3
    CONTENT_VALIDATED = 4
    SOURCE_DERIVED_FIXTURE = 5
    READY_FOR_ORACLE = 6


@dataclass(frozen=True)
class Report:
    source_id: str
    state: State
    flags: tuple[str, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)


_FETCHED = {"fetched_unparsed", "fetched_validated"}
_PROVENANCE = ("retrieved_at", "upstream_release")


def _complete_fixture(m: dict[str, Any]) -> bool:
    return (
        all((m.get(k) or "").strip() for k in _PROVENANCE)
        and bool(m.get("units"))
        and bool(m.get("transformations"))
    )


def lifecycle_of(source: Source, manifests: list[dict[str, Any]]) -> Report:
    mine = [m for m in manifests if m.get("source_id") == source.id]
    flags: list[str] = []
    why: list[str] = []
    state = State.UNVERIFIED

    if source.live_fetch == "blocked_until_authorized":
        flags.append("BLOCKED_BY_POLICY")
    if source.access_status in ("registration_required", "restricted"):
        flags.append("ACCESS_RESTRICTED")
    if any(m.get("retrieval_status") == "fetched_failed_validation" for m in mine):
        flags.append("FAILED_VALIDATION")

    if source.raw["verification"]["status"] == "publisher_page_read":
        state = State.PUBLISHER_PAGE_READ
    else:
        flags.append("NOT_READ")
        why.append(f"verification.status is {source.raw['verification']['status']!r}")

    def reach(target: State, ok: bool, missing: str) -> None:
        nonlocal state
        if state == target - 1 and ok:
            state = target
        elif state < target:
            why.append(f"not {target.name}: {missing}")

    access = source.raw.get("data_access")
    reach(
        State.DATA_ACCESS_CONFIRMED,
        isinstance(access, dict) and bool(access.get("confirmed_on")),
        "no data_access record",
    )
    live = [m for m in mine if m.get("origin") == "live_fetch"]
    reach(
        State.FETCHED_UNPARSED,
        any(m.get("retrieval_status") in _FETCHED for m in live),
        "no stored fetch manifest",
    )
    reach(
        State.CONTENT_VALIDATED,
        any(m.get("retrieval_status") == "fetched_validated" for m in live),
        "no validated fetch",
    )
    derived = [m for m in mine if m.get("origin") == "source_derived_fixture"]
    complete = [m for m in derived if _complete_fixture(m)]
    reach(
        State.SOURCE_DERIVED_FIXTURE,
        bool(complete),
        "no complete source-derived fixture on a validated fetch",
    )
    reach(State.READY_FOR_ORACLE, bool(source.raw.get("oracle_ready")), "no oracle_ready record")

    if derived and state < State.SOURCE_DERIVED_FIXTURE:
        flags.append("out_of_ladder_fixture")
        why.append(
            f"{len(derived)} source-derived fixture(s) exist but were not built from a recorded, validated fetch: they do not raise the state"
        )
    return Report(source.id, state, tuple(flags), tuple(why))


def lifecycle_report(sources: dict[str, Source], manifests: list[dict[str, Any]]) -> list[Report]:
    return [lifecycle_of(s, manifests) for s in sources.values()]
