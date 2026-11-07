"""P2-1: a relief must never LOWER capacity. Opt in with `pytest -m product`.

Base behaviour (verified on GridForge-AI b649b3e7): on a hall whose grid, transformer and UPS are ample, with 230 V single-phase feeds (a
GB300 rack then needs ~600 A), `lv.tapoff_max_A` 630, `lv.busway_ampacity_A` 400 and 40 kW of residual-air removal, /v1/compare and
/v1/screen report `hybrid_dlc` as_found = 4 racks and after_ladder = 0 (binding after the ladder: rack_feed_tapoff). The same hall with
busway 1000 A reports 10 as found and 267 after the ladder. Cause: the busway relief ("Replace busway with 800-1000 A and new tap-off
units") overwrites `tapoff_max_A` with 400 A, and the tap-off relief does the same, whatever the declared rating; 400 A is below the ~600 A
this rack needs, so a relief that is supposed to RAISE the busway drops a 630 or 800 A tap-off to 400 A. The same shape shows at busway 630
and 800 A (as_found 6 and 8 racks become 0) and at 120 V three-phase (a ~680 A rack). The residual-air relief overwrites a declared
capacity with 25 kW in the same way (declared 40 kW becomes 25 kW); that is not reachable through a rack count with the shipped platforms
(a residual load above 25 kW cannot be satisfied by 25 kW anyway), so it is covered only by the step-wise and family invariants below, on
halls that declare 8 and 40 kW.

Invariants (not a copy of the product's arithmetic), over families of halls that differ ONLY in the LV chain, the feed voltage / phase count
and the declared residual-air capacity, on /v1/compare and /v1/screen:
  1. no scenario's after_ladder racks are below its as_found racks;
  2. no ladder step lowers racks (racks_after >= racks_before), taken or later cut from the plan: this catches a fix that only clamps
     the headline number (on base the busway step reads 4 -> 0 and is listed as "adds no deployable capacity ... not costed");
  3. when busway or tap-off is the unique as-found binding constraint and the hall hosts racks, the first taken step on that constraint
     strictly raises racks, and after_ladder is above as_found;
  4. a relieved hall does not end below an already-modern one: after_ladder(busway b, tap-off t) >= after_ladder(busway 1000, tap-off t),
     because the ladder is meant to buy its way to the same ceiling (equality is fine);
  5. raising either rating never lowers as_found or after_ladder racks.
The cell busway 1000 A / tap-off 630 A is the arm the base product already satisfies; it proves the assertions exercise a hall where the
ladder earns racks. Not xfail'd: a defective commit must not produce a green acceptance run.
"""

from __future__ import annotations

from typing import Any

import pytest

from ._common import ample_hall, answered_many, scenarios

pytestmark = [pytest.mark.integration, pytest.mark.product]

BUSWAY_A = (400, 630, 800, 1000)
TAPOFF_A = (250, 400, 630, 800)
MAIN = (230, 1, 40)  # voltage V, phases, declared residual-air kW per rack
OTHER = [(120, 3, 40), (208, 3, 40), (400, 3, 40), (400, 1, 40), (230, 1, 8)]
SMALL_BUSWAY_A = (400, 1000)
SMALL_TAPOFF_A = (400, 800)
Cell = tuple[int, int, int, int, int]  # voltage, phases, residual, busway, tap-off
CELLS: list[Cell] = [(*MAIN, b, t) for t in TAPOFF_A for b in BUSWAY_A] + [
    (*cfg, b, t) for cfg in OTHER for t in SMALL_TAPOFF_A for b in SMALL_BUSWAY_A
]
BINDING_LV = ("busway_ampacity", "rack_feed_tapoff")


def _id(c: Cell) -> str:
    return f"{c[0]}V-{c[1]}ph-res{c[2]}-bus{c[3]}-tap{c[4]}"


@pytest.fixture(scope="module")
def family(intake: dict[str, Any]) -> dict[Cell, dict[str, dict[str, dict[str, Any]]]]:
    cases = {}
    for c in CELLS:
        doc = ample_hall(c[3], c[4], voltage_V=c[0], phases=c[1], residual_air_kW=c[2])
        cases[f"{_id(c)}|compare"] = ("compare", {"intake": doc})
        cases[f"{_id(c)}|screen"] = ("screen", {"intake": doc})
    got = answered_many(cases)
    return {c: {r: scenarios(got[f"{_id(c)}|{r}"]) for r in ("compare", "screen")} for c in CELLS}


@pytest.mark.parametrize("cell", CELLS, ids=_id)
@pytest.mark.parametrize("route", ["compare", "screen"])
def test_the_ladder_never_ends_below_where_it_started(family, route, cell):
    for sid, s in family[cell][route].items():
        before, after = s["as_found"]["racks"], s["after_ladder"]["racks"]
        assert after >= before, (
            f"/v1/{route} {sid} [{_id(cell)}]: {before} racks as found but {after} after the relief ladder "
            f"(binding after ladder: {s['after_ladder'].get('binding')}); a relief lowered capacity"
        )


@pytest.mark.parametrize("cell", CELLS, ids=_id)
def test_no_ladder_step_lowers_racks(family, cell):
    """Every step the ladder lists, taken or cut from the plan afterwards: the product shows a relief it considered lowering racks."""
    for sid, s in family[cell]["screen"].items():
        for step in s["ladder"]:
            assert step["racks_after"] >= step["racks_before"], (
                f"{sid} [{_id(cell)}] step {step['step']} ({step['constraint']}: {step['relief']!r}, taken={step['taken']}) "
                f"lowers racks {step['racks_before']} -> {step['racks_after']}"
            )


@pytest.mark.parametrize("cell", CELLS, ids=_id)
def test_relieving_the_binding_busway_or_tapoff_strictly_helps(family, cell):
    """Where the LV chain is the one thing that limits a hall that hosts racks, buying the relief for it must add racks."""
    for sid, s in family[cell]["screen"].items():
        found = s["as_found"]
        if found["binding"] not in BINDING_LV or found["racks"] == 0:
            continue
        tied = [c for c in s["constraints"] if c["racks_permitted"] == found["racks"]]
        if len(tied) != 1:
            continue  # a genuine tie: relieving one of two limits may add nothing alone
        first = [t for t in s["ladder"] if t["constraint"] == found["binding"]]
        assert first, (
            f"{sid} [{_id(cell)}]: the ladder never relieves its own as-found binding {found['binding']}"
        )
        assert first[0]["racks_after"] > first[0]["racks_before"], (
            f"{sid} [{_id(cell)}]: the relief for the as-found binding constraint {found['binding']} takes racks "
            f"{first[0]['racks_before']} -> {first[0]['racks_after']}"
        )
        assert s["after_ladder"]["racks"] > found["racks"], (
            f"{sid} [{_id(cell)}]: {found['racks']} racks as found, bound by {found['binding']}, and "
            f"{s['after_ladder']['racks']} after relieving it"
        )


@pytest.mark.parametrize("route", ["compare", "screen"])
@pytest.mark.parametrize("cfg", [MAIN, *OTHER], ids=lambda c: f"{c[0]}V-{c[1]}ph-res{c[2]}")
def test_a_relieved_hall_does_not_end_below_an_already_modern_one(family, route, cfg):
    for t in TAPOFF_A if cfg == MAIN else SMALL_TAPOFF_A:
        modern = family[(*cfg, 1000, t)][route]
        for b in BUSWAY_A if cfg == MAIN else SMALL_BUSWAY_A:
            for sid, s in family[(*cfg, b, t)][route].items():
                got, ref = s["after_ladder"]["racks"], modern[sid]["after_ladder"]["racks"]
                assert got >= ref, (
                    f"/v1/{route} {sid} [{cfg[0]}V {cfg[1]}ph res{cfg[2]}, tap-off {t} A]: busway {b} A ends at {got} racks after "
                    f"the ladder but busway 1000 A ends at {ref}; relieving the busway left it worse than one that needed no relief"
                )


def test_raising_either_rating_never_lowers_racks(family):
    """Monotonicity across the main family: a bigger busway or tap-off is a relief, so no figure may fall."""
    for route in ("compare", "screen"):
        for key in ("as_found", "after_ladder"):
            for sid in family[(*MAIN, BUSWAY_A[0], TAPOFF_A[0])][route]:
                for t in TAPOFF_A:
                    racks = [family[(*MAIN, b, t)][route][sid][key]["racks"] for b in BUSWAY_A]
                    assert racks == sorted(racks), (route, sid, key, f"tap-off {t} A", racks)
                for b in BUSWAY_A:
                    racks = [family[(*MAIN, b, t)][route][sid][key]["racks"] for t in TAPOFF_A]
                    assert racks == sorted(racks), (route, sid, key, f"busway {b} A", racks)


def test_the_family_is_not_degenerate(family):
    """Guard against a vacuous pass: the invariants only bite if members host racks as found and the ladder earns racks somewhere."""
    hybrid = {k: v["compare"]["hybrid_dlc"] for k, v in family.items()}
    assert any(s["as_found"]["racks"] > 0 for s in hybrid.values()), (
        "no member of the family hosts a rack as found"
    )
    ref = hybrid[(*MAIN, 1000, 630)]
    assert ref["after_ladder"]["racks"] > ref["as_found"]["racks"] > 0, (
        "the reference arm (busway 1000 A, tap-off 630 A) no longer shows the ladder earning racks"
    )
    bound = [
        k
        for k, v in family.items()
        if v["screen"]["hybrid_dlc"]["as_found"]["binding"] in BINDING_LV
        and v["screen"]["hybrid_dlc"]["as_found"]["racks"] > 0
    ]
    assert len(bound) >= 4, f"too few members where the LV chain binds a hosting hall: {bound}"
