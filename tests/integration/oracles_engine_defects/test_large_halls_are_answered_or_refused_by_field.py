"""P2-4: a large hall is answered completely, or refused by the ceiling it exceeds; never "the ladder did not converge". `-m product`.

Base behaviour (verified on GridForge-AI b649b3e7): take the reference hall and scale `grid.firm_capacity_MVA`, `grid.contracted_MW`
(x 12/14) and the transformer unit rating (half the supply each, 3 units N+1) to 150, 170, 200 or 300 MVA with `hall.rack_positions`
20,000. Every number is inside the product's plausibility ceilings (loader.PLAUSIBLE_MAX: 5,000 MVA / MW of supply, 1,000 MVA per transformer
unit, 50,000 rack positions), so the intake loader accepts the hall. /v1/compare, /v1/screen and /v1/diff (before = after = the 170 MVA
hall) then fail inside the engine with `LadderDidNotConverge: the headroom ladder was still finding reliefs after 200 steps` (the same hall
at 100 and 130 MVA is answered: 446 / 592 hybrid_dlc racks, bound by grid_firm_capacity). The message names no field and no ceiling.
Because LadderDidNotConverge subclasses ValueError it is also counted as a clean refusal by this harness's fuzz classification
(`_fuzz_runner.py`, `product_adapter._BATCH_RUNNER`), so `gridforge-stress fuzz` cannot see it; the last test pins that at the oracle level.

Invariant: for each supply size the product either
  * ANSWERS, and the answer is complete: after_ladder racks are not below as_found, the IT load does not exceed the contracted supply,
    racks never fall as the supply grows (100..300 MVA), and the hall is bound by the one constraint that cannot be bought away
    (grid_firm_capacity) rather than by one the ladder would still be buying (a truncated "best effort" answer stops on, e.g.,
    ups_capacity at the same count for 150 and 300 MVA); or
  * REFUSES (a caller-facing 4xx) with a message that talks about the intake, not the engine's step budget, names an intake field and cites a
    ceiling number that the hall's value for that field really exceeds. Lowering a ceiling to what the engine can solve is a legitimate fix.
Satisfied on the base product at 100 and 130 MVA (the answered arm). Not xfail'd.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

import pytest

from ._common import leaves, mutated, product_modules_json, run_many

pytestmark = [pytest.mark.integration, pytest.mark.product]

SIZES_MVA = [100, 130, 150, 170, 200, 250, 300]
ENGINE_PHRASES = re.compile(
    r"after \d+ steps|did not converge|still finding reliefs|LadderDidNotConverge|headroom ladder|iteration limit|step budget",
    re.IGNORECASE,
)
LIQUID = ("hybrid_dlc", "full_dlc", "full_dlc_btm")


def scaled_hall(intake: dict[str, Any], mva: float) -> dict[str, Any]:
    def edit(d: dict[str, Any]) -> None:
        d["grid"]["firm_capacity_MVA"] = mva
        d["grid"]["contracted_MW"] = round(mva * 12 / 14, 1)
        d["transformers"][0]["unit_rating_MVA"] = mva / 2
        d["hall"]["rack_positions"] = 20000
        d["hall"]["positions_available"] = 18000

    return mutated(intake, edit)


def _numeric_fields(doc: dict[str, Any]) -> dict[str, float]:
    """leaf-key -> value for every numeric leaf of the intake (the first occurrence wins; fine for the fields scaled here)."""
    out: dict[str, float] = {}
    for path, v in leaves(doc):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.setdefault(re.sub(r"\[\d+\]", "", path).split(".")[-1], float(v))
    return out


def refusal_problem(message: str, doc: dict[str, Any]) -> str | None:
    """None when `message` is an intake-facing refusal citing a ceiling the hall exceeds; otherwise what is wrong with it."""
    if ENGINE_PHRASES.search(message):
        return "it speaks of the engine's own search (steps / convergence), not of the intake"
    fields = _numeric_fields(doc)
    named = [f for f in fields if "_" in f and f in message]
    if not named:
        return "it names no intake field"
    numbers = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", message)]
    for f in named:
        if any(fields[f] > n for n in numbers):
            return None
    return (
        f"it names {named} but cites no number that the hall's value ({[fields[f] for f in named]}) exceeds "
        f"(numbers in the message: {numbers})"
    )


@pytest.fixture(scope="module")
def outcomes(intake: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases: dict[str, Any] = {}
    for m in SIZES_MVA:
        doc = scaled_hall(intake, m)
        cases[f"compare|{m}"] = ("compare", {"intake": doc})
        cases[f"screen|{m}"] = ("screen", {"intake": doc})
    big = scaled_hall(intake, 170)
    cases["diff|170"] = ("diff", {"before": big, "after": big})
    return run_many(cases)


@pytest.mark.parametrize("route", ["compare", "screen"])
@pytest.mark.parametrize("mva", SIZES_MVA)
def test_a_large_hall_is_answered_or_refused_naming_the_ceiling_it_exceeds(
    intake, outcomes, route, mva
):
    row = outcomes[f"{route}|{mva}"]
    if row["status"] == "ok":
        return
    doc = scaled_hall(intake, mva)
    assert row["status"] == "clean", f"/v1/{route} at {mva} MVA escaped as a server fault: {row}"
    why = refusal_problem(row["message"], doc)
    assert why is None, (
        f"/v1/{route} at {mva} MVA failed with {row['error']}: {row['message']!r}; {why}. Inside PLAUSIBLE_MAX: "
        f"{_inside_ceilings(doc)}"
    )


def test_a_diff_of_a_large_hall_with_itself_is_answered_or_refused_naming_the_ceiling(
    intake, outcomes
):
    row = outcomes["diff|170"]
    if row["status"] == "ok":
        return
    assert row["status"] == "clean", row
    why = refusal_problem(row["message"], scaled_hall(intake, 170))
    assert why is None, (
        f"/v1/diff of the 170 MVA hall with itself failed with {row['error']}: {row['message']!r}; {why}"
    )


@pytest.mark.parametrize("route", ["compare", "screen"])
def test_an_answered_large_hall_is_complete_not_truncated(outcomes, route):
    """Whatever sizes the product chooses to answer must be real answers: not below as found, not above the supply, growing with the
    supply, and bound by the supply rather than by a constraint the ladder was still buying when it stopped."""
    answered_sizes = [m for m in SIZES_MVA if outcomes[f"{route}|{m}"]["status"] == "ok"]
    assert answered_sizes, "no size was answered at all"
    previous: dict[str, int] = {}
    for m in answered_sizes:
        result = outcomes[f"{route}|{m}"]["result"]
        for s in result["scenarios"]:
            if s["id"] not in LIQUID:
                continue
            after, found = s["after_ladder"]["racks"], s["as_found"]["racks"]
            assert after >= found, (
                f"/v1/{route} {s['id']} at {m} MVA: {found} racks as found, {after} after the ladder"
            )
            assert after >= previous.get(s["id"], 0), (
                f"/v1/{route} {s['id']}: {after} racks at {m} MVA but {previous[s['id']]} at the previous, smaller size"
            )
            previous[s["id"]] = after
            if route == "screen":
                contracted_kw = round(m * 12 / 14, 1) * 1000
                assert s["after_ladder"]["it_load_kW"]["value"] <= contracted_kw, (
                    f"{s['id']} at {m} MVA hosts {s['after_ladder']['it_load_kW']['value']:,.0f} kW of IT load on {contracted_kw:,.0f} kW contracted"
                )
            if s["id"] != "full_dlc_btm":
                assert s["after_ladder"]["binding"] == "grid_firm_capacity", (
                    f"/v1/{route} {s['id']} at {m} MVA stops on {s['after_ladder']['binding']} with {after} racks; the supply scales "
                    "and everything else on the ladder can be bought, so the answer is truncated"
                )
    for s_id in LIQUID[:2]:
        grid_bound = [(m, outcomes[f"{route}|{m}"]["result"]) for m in answered_sizes if m >= 100]
        racks = [
            next(x for x in r["scenarios"] if x["id"] == s_id)["after_ladder"]["racks"]
            for _, r in grid_bound
        ]
        assert all(b > a for a, b in itertools.pairwise(racks)), (
            f"/v1/{route} {s_id}: sizes {[m for m, _ in grid_bound]} MVA give {racks} racks; each larger supply must host strictly more "
            "because the supply is what limits"
        )


def _inside_ceilings(doc: dict[str, Any]) -> str:
    try:
        ceilings = product_modules_json(
            "from gridforge.intake import loader; print(json.dumps(loader.PLAUSIBLE_MAX))"
        )
    except (
        Exception
    ) as exc:  # the ceilings table is the product's to rename; its absence is not a failure here
        return f"unknown ({type(exc).__name__})"
    checks = {
        "grid.firm_capacity_MVA": doc["grid"]["firm_capacity_MVA"],
        "grid.contracted_MW": doc["grid"]["contracted_MW"],
        "transformers.unit_rating_MVA": doc["transformers"][0]["unit_rating_MVA"],
        "hall.rack_positions": doc["hall"]["rack_positions"],
    }
    return str({k: v <= ceilings[k] for k, v in checks.items() if k in ceilings})


@pytest.mark.parametrize("mva", SIZES_MVA)
def test_the_fuzz_clean_class_is_not_a_refusal_for_an_engine_internal_failure(outcomes, mva):
    """`run_many` classifies outcomes exactly as `gridforge-stress fuzz` does. A 'clean' row must be a caller-facing refusal about the intake;
    a ValueError subclass raised by the engine's own solver is not, even though the fuzz counts it as one."""
    row = outcomes[f"compare|{mva}"]
    assert row["status"] in {"ok", "clean"}, row
    if row["status"] == "clean":
        assert (
            not ENGINE_PHRASES.search(row["message"]) and row["error"] != "LadderDidNotConverge"
        ), (
            f"{mva} MVA: counted as a clean refusal by the fuzz classification, but it is {row['error']}: {row['message']!r}"
        )
