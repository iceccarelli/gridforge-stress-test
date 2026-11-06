"""P2-1: a relief must never LOWER capacity. Opt in with `pytest -m product`.

Defect (verified on GridForge-AI b649b3e7): on a hall whose grid, transformer and UPS are ample, with 230 V single-phase feeds (a GB300
rack then needs ~600 A), `lv.tapoff_max_A` 630 and `lv.busway_ampacity_A` 400 and ~40 kW of residual-air removal, /v1/compare and /v1/screen
report `hybrid_dlc` as_found = 4 racks and after_ladder = 0 racks (binding after the ladder: rack_feed_tapoff). The same hall with
`busway_ampacity_A` 1000 reports 10 as found and 267 after the ladder. Raising busway ampacity 400 -> 1000 therefore turns an answer of
0 racks into 267, yet the ladder that is supposed to *relieve* the 400 A busway ends below where it started. The same shape appears at
the other busway ratings below 1000 A (hybrid_dlc as_found 6 and 8 racks become 0 after the ladder at busway 630 and 800 A, tap-off 630 or 800 A);
in every failing cell the full_dlc scenarios are 0 either way, and hybrid_dlc is the one that drops.

Invariant (not a copy of the product's arithmetic): relieving a constraint never reduces what a hall can host. Over a 4 x 4 family of halls
that differ ONLY in `lv.tapoff_max_A` and `lv.busway_ampacity_A`, no scenario's after_ladder racks may be below its as_found racks, on
/v1/compare or on /v1/screen, and as_found / after_ladder racks may not fall when either rating is raised.

The cell busway 1000 A / tap-off 630 A is the arm the base product already satisfies; it proves the assertions below exercise a hall where
the ladder does earn racks. Not xfail'd: a defective commit must not produce a green acceptance run.
"""

from __future__ import annotations

from typing import Any

import pytest

from ._common import ample_hall, answered, scenarios

pytestmark = [pytest.mark.integration, pytest.mark.product]

BUSWAY_A = (400, 630, 800, 1000)
TAPOFF_A = (250, 400, 630, 800)
CELLS = [(b, t) for t in TAPOFF_A for b in BUSWAY_A]


@pytest.fixture(scope="module")
def family(intake: dict[str, Any]) -> dict[tuple[int, int], dict[str, dict[str, Any]]]:
    out: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
    for b, t in CELLS:
        doc = ample_hall(b, t)
        out[(b, t)] = {
            "compare": scenarios(answered("compare", doc)),
            "screen": scenarios(answered("screen", doc)),
        }
    return out


@pytest.mark.parametrize(("busway", "tapoff"), CELLS)
@pytest.mark.parametrize("route", ["compare", "screen"])
def test_the_ladder_never_ends_below_where_it_started(family, route, busway, tapoff):
    for sid, s in family[(busway, tapoff)][route].items():
        before, after = s["as_found"]["racks"], s["after_ladder"]["racks"]
        assert after >= before, (
            f"/v1/{route} {sid}: busway {busway} A, tap-off {tapoff} A: {before} racks as found but {after} after the "
            f"relief ladder (binding after ladder: {s['after_ladder'].get('binding')}); a relief lowered capacity"
        )


def test_raising_either_rating_never_lowers_racks(family):
    """Monotonicity across the family: a bigger busway or tap-off is a relief, so no figure may fall. (Satisfied on the base product.)"""
    for route in ("compare", "screen"):
        for key in ("as_found", "after_ladder"):
            for sid in family[(BUSWAY_A[0], TAPOFF_A[0])][route]:
                for t in TAPOFF_A:
                    racks = [family[(b, t)][route][sid][key]["racks"] for b in BUSWAY_A]
                    assert racks == sorted(racks), (route, sid, key, f"tap-off {t} A", racks)
                for b in BUSWAY_A:
                    racks = [family[(b, t)][route][sid][key]["racks"] for t in TAPOFF_A]
                    assert racks == sorted(racks), (route, sid, key, f"busway {b} A", racks)


def test_the_family_is_not_degenerate(family):
    """Guard against a vacuous pass: the invariant only bites if some member has racks as found and the ladder earns racks somewhere."""
    hybrid = {k: v["compare"]["hybrid_dlc"] for k, v in family.items()}
    assert any(s["as_found"]["racks"] > 0 for s in hybrid.values()), (
        "no member of the family hosts a rack as found"
    )
    assert (
        hybrid[(1000, 630)]["after_ladder"]["racks"] > hybrid[(1000, 630)]["as_found"]["racks"] > 0
    ), "the reference arm (busway 1000 A, tap-off 630 A) no longer shows the ladder earning racks"
