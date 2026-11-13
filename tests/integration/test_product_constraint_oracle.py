"""The real /v1/screen against the independent headroom oracle (gridforge_stress.oracles), over redundancy / derating / power-factor /
contracted-vs-firm / peak variants. Every variant is also a mutation of the intake, so a product that ignores one of these inputs fails."""

from __future__ import annotations

import pytest

from gridforge_stress.oracles import check_screen, expected_headroom_kw

from ._product_helpers import mutated, require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]

VARIANTS = {
    "reference": lambda d: None,
    "tx_N": lambda d: d["transformers"][0].update(redundancy="N"),
    "tx_N+2": lambda d: d["transformers"][0].update(redundancy="N+2"),
    "tx_2N_3_units": lambda d: d["transformers"][0].update(redundancy="2N"),
    "tx_2N_4_units": lambda d: d["transformers"][0].update(redundancy="2N", units=4),
    "tx_derate_0.8": lambda d: d["transformers"][0].update(derating_factor=0.8),
    "tx_derate_1.0": lambda d: d["transformers"][0].update(derating_factor=1.0),
    "ups_N": lambda d: d["ups"][0].update(redundancy="N"),
    "ups_N+2": lambda d: d["ups"][0].update(redundancy="N+2"),
    "ups_2N": lambda d: d["ups"][0].update(redundancy="2N"),
    "ups_rating_1000": lambda d: d["ups"][0].update(unit_rating_kW=1000),
    "contracted_10": lambda d: d["grid"].update(contracted_MW=10.0),
    "contracted_20": lambda d: d["grid"].update(contracted_MW=20.0),
    "firm_mva_10": lambda d: d["grid"].update(firm_capacity_MVA=10.0),
    "pf_0.8": lambda d: d["grid"].update(power_factor=0.8),
    "pf_1.0": lambda d: d["grid"].update(power_factor=1.0),
    "peak_above_ceiling": lambda d: d["grid"].update(current_site_peak_MW=13.0),
}


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_screen_agrees_with_the_independent_headroom(name):
    intake = mutated(VARIANTS[name])
    problems = check_screen(intake, run("/v1/screen", {"intake": intake}))
    assert not problems, "\n".join(problems)


def test_the_variants_actually_move_the_expected_headroom():
    """Guards against a vacuous sweep: the oracle's own expectation must differ across the variants."""
    seen = {
        tuple(round(v) for v in expected_headroom_kw(mutated(f)).values())
        for f in VARIANTS.values()
    }
    assert len(seen) >= 12, len(seen)


def _without_btm(d):
    VARIANTS["peak_above_ceiling"](d)
    d["btm_options"] = []


def _firm_btm_kw(options) -> float:
    """The firm supply a list of behind-the-meter options adds, from the options' own declared figures: capacity x firm_capacity_factor, for an option
    declared firm (the factor defaults to 1). The oracle does not run the product's arithmetic."""
    return sum(
        o["capacity_MW"] * 1000 * (o.get("firm_capacity_factor", 1.0) if o.get("firm") else 0.0)
        for o in options
    )


def test_no_headroom_means_no_racks_and_no_leader():
    """Peak 13 MW against a 12 MW contracted ceiling and no behind-the-meter supply: nothing can be deployed, whatever relief is taken, and the product
    must say so."""
    intake = mutated(_without_btm)
    assert expected_headroom_kw(intake)["grid_firm_capacity"] < 0
    out = run("/v1/compare", {"intake": intake})
    assert [s["id"] for s in out["scenarios"] if s["after_ladder"]["racks"] != 0] == []
    assert out["ranking"]["leader"] is None


def test_behind_the_meter_supply_closes_a_grid_shortfall_by_no_more_than_it_adds():
    """The same 1 MW shortfall with the reference's behind-the-meter options declared (a 2 MW battery at a firm factor of 0.5 and a 5 MW engine: 6 MW
    firm). Only a scenario that includes that supply may hold racks, and its IT load cannot exceed what the supply leaves after the shortfall."""
    intake = mutated(VARIANTS["peak_above_ceiling"])
    shortfall_kw = -expected_headroom_kw(intake)["grid_firm_capacity"]
    assert shortfall_kw > 0
    left_kw = _firm_btm_kw(intake["btm_options"]) - shortfall_kw
    assert left_kw > 0, (
        "the fixture no longer has behind-the-meter supply that closes the shortfall"
    )
    out = run("/v1/compare", {"intake": intake})
    with_racks = [s for s in out["scenarios"] if s["after_ladder"]["racks"] != 0]
    assert [s["id"] for s in with_racks] == ["full_dlc_btm"], [s["id"] for s in with_racks]
    load_kw = with_racks[0]["after_ladder"]["it_load_kW"]["value"]
    assert 0 < load_kw <= left_kw, (load_kw, left_kw)
    assert out["ranking"]["leader"] == "full_dlc_btm"


def test_behind_the_meter_supply_that_only_equals_the_shortfall_deploys_nothing():
    """The boundary: a battery whose firm share is exactly the 1 MW shortfall leaves nothing, so there are no racks and no leader."""

    def battery_only(d):
        VARIANTS["peak_above_ceiling"](d)
        d["btm_options"] = [o for o in d["btm_options"] if o["id"] == "BESS-2MW-8MWh"]

    intake = mutated(battery_only)
    assert (
        _firm_btm_kw(intake["btm_options"]) == -expected_headroom_kw(intake)["grid_firm_capacity"]
    )
    out = run("/v1/compare", {"intake": intake})
    assert [s["id"] for s in out["scenarios"] if s["after_ladder"]["racks"] != 0] == []
    assert out["ranking"]["leader"] is None
