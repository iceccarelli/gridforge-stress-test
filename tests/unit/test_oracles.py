"""The headroom oracle: hand-derived expectations, properties, and mutations proving it can FAIL."""

from __future__ import annotations

import copy

import pytest

from gridforge_stress.fuzz import reference_intake
from gridforge_stress.oracles import (
    Unsupported,
    bess_bound_violations,
    check_screen,
    effective_units,
    expected_headroom_kw,
    racks_for,
)

# Reference intake (transformers 3 x 5 MVA N+1 derate 0.9; pf 0.97; UPS 6 x 1200 kW N+1; MVA 14; contracted 12; peak 7.4; IT 4.9).
# Worked by hand: transformer 2 x 5 x 0.97 x 0.9 = 8.73 MW - 7.4 = 1.33 MW; UPS 5 x 1200 = 6000 - 4900 = 1100 kW;
# grid min(14 x 0.97 = 13.58, 12) = 12 - 7.4 = 4.6 MW.
HAND = {"transformer_capacity": 1330.0, "ups_capacity": 1100.0, "grid_firm_capacity": 4600.0}


def intake():
    return copy.deepcopy(reference_intake())


def test_reference_headrooms_match_the_hand_arithmetic():
    got = expected_headroom_kw(intake())
    for k, v in HAND.items():
        assert got[k] == pytest.approx(v, abs=1e-6), k


@pytest.mark.parametrize(
    "edit,key,kw",
    [
        # N, 3 units: 3 x 5 x 0.97 x 0.9 = 13.095 - 7.4 = 5.695 MW
        (lambda d: d["transformers"][0].update(redundancy="N"), "transformer_capacity", 5695.0),
        # derate 0.8: 2 x 5 x 0.97 x 0.8 = 7.76 - 7.4 = 0.36 MW
        (lambda d: d["transformers"][0].update(derating_factor=0.8), "transformer_capacity", 360.0),
        # 2N with 3 units leaves 1: 1 x 5 x 0.97 x 0.9 = 4.365 - 7.4 = -3.035 MW
        (lambda d: d["transformers"][0].update(redundancy="2N"), "transformer_capacity", -3035.0),
        # UPS N, 6 units: 7200 - 4900
        (lambda d: d["ups"][0].update(redundancy="N"), "ups_capacity", 2300.0),
        # UPS 2N, 6 units leaves 3: 3600 - 4900
        (lambda d: d["ups"][0].update(redundancy="2N"), "ups_capacity", -1300.0),
        # contracted 10 MW binds: 10 - 7.4
        (lambda d: d["grid"].update(contracted_MW=10.0), "grid_firm_capacity", 2600.0),
        # contracted 20 MW does not bind: 14 x 0.97 = 13.58 - 7.4
        (lambda d: d["grid"].update(contracted_MW=20.0), "grid_firm_capacity", 6180.0),
        # 10 MVA x 0.97 = 9.7 - 7.4
        (lambda d: d["grid"].update(firm_capacity_MVA=10.0), "grid_firm_capacity", 2300.0),
        # pf 0.8: 14 x 0.8 = 11.2 < 12 - 7.4
        (lambda d: d["grid"].update(power_factor=0.8), "grid_firm_capacity", 3800.0),
        # pf 0.8 also hits the transformer: 2 x 5 x 0.8 x 0.9 = 7.2 - 7.4
        (lambda d: d["grid"].update(power_factor=0.8), "transformer_capacity", -200.0),
    ],
)
def test_hand_worked_variants(edit, key, kw):
    d = intake()
    edit(d)
    assert expected_headroom_kw(d)[key] == pytest.approx(kw, abs=1e-6)


def test_mva_is_not_mw():
    """Treating the MVA figure as MW (pf ignored) overstates the ceiling; the oracle must not."""
    d = intake()
    d["grid"].update(firm_capacity_MVA=11.0, contracted_MW=50.0, power_factor=0.9)
    assert expected_headroom_kw(d)["grid_firm_capacity"] == pytest.approx(11.0 * 0.9 * 1000 - 7400)
    assert expected_headroom_kw(d)["grid_firm_capacity"] < (11.0 - 7.4) * 1000


@pytest.mark.parametrize("units", range(0, 9))
def test_redundancy_is_ordered_and_never_negative(units):
    n, n1, n2, two_n = (effective_units(units, r) for r in ("N", "N+1", "N+2", "2N"))
    assert n >= n1 >= n2 >= 0 and n >= two_n >= 0
    assert n == units


def test_headroom_is_monotone_in_capacity_and_antitone_in_load():
    base = expected_headroom_kw(intake())
    more = intake()
    more["transformers"][0]["units"] = 4
    more["ups"][0]["units"] = 7
    more["grid"]["contracted_MW"] = 13.0
    up = expected_headroom_kw(more)
    assert all(up[k] >= base[k] for k in base)
    loaded = intake()
    loaded["grid"]["current_site_peak_MW"] = 8.0
    loaded["grid"]["current_it_load_MW"] = 5.5
    dn = expected_headroom_kw(loaded)
    assert all(dn[k] <= base[k] for k in base)


def test_racks_for_floors_and_never_goes_negative():
    assert (
        racks_for(1330.0, 135 * 1.2) == 8
        and racks_for(-5.0, 135.0) == 0
        and racks_for(0.0, 135.0) == 0
    )


@pytest.mark.parametrize("bad", [True, "5", None, float("nan"), float("inf")])
def test_non_numbers_are_unsupported_not_zero(bad):
    d = intake()
    d["grid"]["power_factor"] = bad
    with pytest.raises(Unsupported):
        expected_headroom_kw(d)


def test_more_than_one_transformer_entry_is_unchecked_not_passed():
    d = intake()
    d["transformers"].append(copy.deepcopy(d["transformers"][0]))
    with pytest.raises(Unsupported):
        expected_headroom_kw(d)


def test_unknown_redundancy_is_unsupported():
    d = intake()
    d["transformers"][0]["redundancy"] = "N+3"
    with pytest.raises(Unsupported):
        expected_headroom_kw(d)


# --- check_screen against a screen result written by hand from the arithmetic above (not produced by the oracle) ---
def good_screen():
    rack_text = "the target platform draws 135 [132-142] kW (E0) per rack"
    return {
        "scenarios": [
            {
                "id": "full_dlc_btm",
                "constraints": [
                    {"id": "architecture_capability", "racks_permitted": None, "basis": rack_text},
                    {
                        "id": "transformer_capacity",
                        "racks_permitted": 8,
                        "basis": "1,330 [845-1,810] kW (E3) usable after redundancy and derating",
                    },
                    {
                        "id": "ups_capacity",
                        "racks_permitted": 8,
                        "basis": "1,100 kW (E0) of protected-load headroom after N+1 redundancy",
                    },
                    {
                        "id": "grid_firm_capacity",
                        "racks_permitted": 28,
                        "basis": "4,600 kW (E3) of firm-supply headroom at PUE 1.2 [1.15-1.28]",
                    },
                ],
            }
        ]
    }


def _con(s, cid):
    return next(c for c in s["scenarios"][0]["constraints"] if c["id"] == cid)


def test_a_correct_screen_is_consistent():
    assert check_screen(intake(), good_screen()) == []


MUTATIONS = {
    "wrong_unit_kw_to_w": lambda s: _con(s, "transformer_capacity").update(
        basis="1,330,000 kW usable"
    ),
    "headroom_off_by_a_transformer": lambda s: _con(s, "transformer_capacity").update(
        basis="6,195 kW usable"
    ),
    "redundancy_ignored_headroom": lambda s: _con(s, "ups_capacity").update(
        basis="2,300 kW of protected-load headroom"
    ),
    "grid_ceiling_uses_mva_as_mw": lambda s: _con(s, "grid_firm_capacity").update(
        basis="6,600 kW of firm-supply headroom at PUE 1.2"
    ),
    "racks_one_too_many": lambda s: _con(s, "grid_firm_capacity").update(racks_permitted=29),
    "racks_one_too_few": lambda s: _con(s, "ups_capacity").update(racks_permitted=7),
    "negative_headroom_still_permits_racks": lambda s: _con(s, "transformer_capacity").update(
        basis="-200 kW usable", racks_permitted=3
    ),
    "constraint_dropped": lambda s: s["scenarios"][0]["constraints"].remove(
        _con(s, "ups_capacity")
    ),
    "pue_below_one": lambda s: _con(s, "grid_firm_capacity").update(
        basis="4,600 kW of firm-supply headroom at PUE 0.5"
    ),
    "no_headroom_text": lambda s: _con(s, "grid_firm_capacity").update(basis="plenty"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_oracle_catches_each_wrong_product_answer(name):
    s = good_screen()
    MUTATIONS[name](s)
    assert check_screen(intake(), s), name


def test_oracle_notices_when_the_intake_not_the_answer_changed():
    """The same (previously correct) answer must fail once the intake says the capacity is different."""
    d = intake()
    d["transformers"][0]["redundancy"] = "N"
    assert any("transformer_capacity" in p for p in check_screen(d, good_screen()))


def test_missing_declared_rack_power_is_a_finding_not_a_pass():
    s = good_screen()
    s["scenarios"][0]["constraints"].pop(0)
    assert any("cannot read" in p for p in check_screen(intake(), s))


@pytest.mark.parametrize("minus", ["-", "\N{MINUS SIGN}"])
def test_negative_headroom_text_is_read_with_either_minus(minus):
    s = good_screen()
    d = intake()
    d["transformers"][0]["redundancy"] = "2N"  # independent headroom is -3,035 kW
    c = _con(s, "transformer_capacity")
    c.update(basis=f"{minus}3,040 kW (E3) usable", racks_permitted=0)
    assert not [p for p in check_screen(d, s) if "transformer_capacity" in p]


# --- BESS bounds: MW is not MWh -------------------------------------------------------------------------------------------------
def bess(**over):
    base = dict(
        grid_firm_mw=8.0, power_mw=2.0, energy_mwh=8.0, ride_through_h=4.0, available_mw=9.5
    )
    return bess_bound_violations(**{**base, **over})


def test_a_physically_possible_answer_has_no_violation():
    assert bess() == []
    assert bess(available_mw=8.0) == []  # too conservative is allowed: the bound is an upper bound
    assert (
        bess(ride_through_h=0.5, available_mw=10.0) == []
    )  # full power for half an hour: 2 MW x 0.5 h = 1 MWh <= 8


@pytest.mark.parametrize(
    "name,over",
    [
        ("battery_reduces_grid", dict(available_mw=7.0)),
        ("exceeds_power_rating", dict(ride_through_h=0.5, available_mw=10.5)),
        (
            "ignores_energy_at_long_duration",
            dict(ride_through_h=24.0, available_mw=10.0),
        ),  # 2 MW x 24 h = 48 MWh > 8
        (
            "ignores_a_tiny_battery",
            dict(power_mw=20.0, energy_mwh=1.0, ride_through_h=4.0, available_mw=28.0),
        ),  # 20 MW x 4 h = 80 MWh
        (
            "mw_read_as_mwh",
            dict(power_mw=2.0, energy_mwh=2.0, ride_through_h=4.0, available_mw=10.0),
        ),  # needs 8 MWh, holds 2
    ],
)
def test_the_bess_oracle_catches_each_wrong_answer(name, over):
    assert bess(**over), name
