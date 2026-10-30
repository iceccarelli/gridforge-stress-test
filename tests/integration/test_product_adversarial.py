"""Adversarial and invariant tests through the REAL product handlers (screen, compare, Hall Watch diff).

Opt in with `pytest -m product`. Input is the SYNTHETIC reference-hall fixture. Observations about product
gaps found in the product stay pinned as strict xfail while open (visible, and loud when fixed), then become ordinary regressions.
"""

from __future__ import annotations

import copy
import itertools
import json

import pytest

from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import ProductUnavailable, call, product_dir, product_required

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module")
def intake():
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))
    return json.loads((fixtures_dir() / "product" / "reference_hall_intake.json").read_text())


def mutated(base, fn):
    x = copy.deepcopy(base)
    fn(x)
    return x


def racks(intake, objective="max_compute"):
    out = call("compare", {"intake": intake, "objective": objective})
    assert out["ok"], out
    return {s["id"]: s["after_ladder"]["racks"] for s in out["result"]["scenarios"]}


def keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from keys(v)


# --- public tier must not leak schedule numbers -------------------------------------------------------------------


def test_public_tier_screen_carries_no_weeks(intake):
    out = call("screen", {"intake": intake}, tier="public")
    assert out["ok"]
    assert not [k for k in keys(out["result"]) if "weeks" in k.lower()]
    assert any("weeks" in k.lower() for k in keys(call("screen", {"intake": intake})["result"]))


# --- Hall Watch (diff) ---------------------------------------------------------------------------------------------

DIFF_MUTATIONS = {
    "longer_transformer": lambda x: x["transformers"][0].__setitem__(
        "replacement_lead_time_weeks", 200
    ),
    "less_firm_capacity": lambda x: x["grid"].__setitem__("firm_capacity_MVA", 11.0),
    "drop_btm_lead_times": lambda x: [o.pop("lead_time_weeks") for o in x["btm_options"]],
}


def test_diff_of_identical_states_is_empty(intake):
    r = call("diff", {"before": intake, "after": intake})["result"]
    assert (r["material"], r["racks_delta"], r["weeks_delta"]) == (False, 0, 0.0)


@pytest.mark.parametrize("name", sorted(DIFF_MUTATIONS))
def test_diff_is_antisymmetric_and_deterministic(intake, name):
    after = mutated(intake, DIFF_MUTATIONS[name])
    fwd = call("diff", {"before": intake, "after": after})
    rev = call("diff", {"before": after, "after": intake})
    assert fwd["ok"] and rev["ok"], (fwd, rev)
    f, r = fwd["result"], rev["result"]
    assert f["racks_delta"] == -r["racks_delta"]
    if f["weeks_delta"] is not None and r["weeks_delta"] is not None:
        assert f["weeks_delta"] == -r["weeks_delta"]
    assert call("diff", {"before": intake, "after": after})["result"] == f


def test_longer_lead_time_is_reported_as_later_and_still_an_assumption(intake):
    r = call(
        "diff", {"before": intake, "after": mutated(intake, DIFF_MUTATIONS["longer_transformer"])}
    )["result"]
    assert r["weeks_delta"] == 40.0 and r["material"]
    assert r["date_basis"] == "assumed" and "not a date" in r["headline"]


# --- hostile numeric inputs are refused, never defaulted ----------------------------------------------------------


@pytest.mark.parametrize(
    "mut",
    [
        lambda x: x["btm_options"][0].__setitem__("capacity_MW", float("nan")),
        lambda x: x["btm_options"][0].__setitem__("capacity_MW", float("inf")),
        lambda x: x["btm_options"][0].__setitem__("capacity_MW", True),
        lambda x: x["grid"].__setitem__("power_factor", 0),
        lambda x: x["grid"].__setitem__("power_factor", 1.5),
        lambda x: x["grid"].__setitem__("firm_capacity_MVA", -5),
    ],
    ids=["nan", "inf", "bool", "pf_zero", "pf_over_one", "negative_firm"],
)
def test_hostile_numbers_refused(intake, mut):
    out = call("compare", {"intake": mutated(intake, mut)})
    assert out["ok"] is False and out["error"] == "IntakeError"


# --- dimensional/monotonic invariants -----------------------------------------------------------------------------


def test_capacity_is_monotone_in_firm_and_contracted_power_and_capped_by_the_smaller(intake):
    firm = [
        racks(mutated(intake, lambda x, v=v: x["grid"].__setitem__("firm_capacity_MVA", v)))[
            "full_dlc_btm"
        ]
        for v in (7.0, 10.0, 14.0, 20.0)
    ]
    assert firm == sorted(firm) and firm[0] < firm[-1]
    con = [
        racks(mutated(intake, lambda x, v=v: x["grid"].__setitem__("contracted_MW", v)))[
            "full_dlc_btm"
        ]
        for v in (6.0, 12.0, 50.0, 5_000.0)  # 5,000 MW is the product's ceiling
    ]
    assert con == sorted(con)
    assert con[-2] == con[-1], "contracted MW above firm capacity must not buy extra compute"


# --- ranking stability under plausible alternative lead-time assumptions -------------------------------------------


def test_leader_stable_and_no_leader_on_tied_dates(intake):
    leaders, tied_or_strict = set(), []
    for tx, b, g in itertools.product((80, 160, 210), (0.65, 1.0, 1.35), (0.65, 1.0, 1.35)):
        x = copy.deepcopy(intake)
        x["transformers"][0]["replacement_lead_time_weeks"] = tx
        x["btm_options"][0]["lead_time_weeks"] *= b
        x["btm_options"][1]["lead_time_weeks"] *= g
        rc = call("compare", {"intake": x, "objective": "max_compute"})["result"]
        leaders.add(rc["ranking"]["leader"])
        rf = call("compare", {"intake": x, "objective": "fastest"})["result"]["ranking"]
        vals = rf["values"]
        if rf["leader"] is not None:
            tied_or_strict.append(
                all(vals[rf["leader"]] < v for k, v in vals.items() if k != rf["leader"])
            )
        else:
            tied_or_strict.append(len(set(vals.values())) < len(vals))
    assert leaders == {"full_dlc_btm"}
    assert all(tied_or_strict), "a 'fastest' leader must be strictly faster; ties must not name one"


# --- gaps found in the product and since fixed: ordinary regressions now (they were strict xfails until GridForge-AI #102) ----


def test_contradictory_grid_inputs_are_flagged(intake):
    """Contracted power above what the firm connection can deliver is an input discrepancy to confirm, never silence.

    Fixed in the product (GridForge-AI #102); this was a strict xfail until then."""
    base = call("compare", {"intake": intake})["result"]["caveats"]
    odd = call(
        "compare", {"intake": mutated(intake, lambda x: x["grid"].__setitem__("contracted_MW", 50))}
    )
    assert odd["ok"], odd
    caveats = odd["result"]["caveats"]
    assert caveats != base
    stated = [c for c in caveats if "contracted" in c.lower() and "firm" in c.lower()]
    assert len(stated) == 1, caveats  # said once, not dropped and not duplicated
    assert (
        "network operator" in stated[0]
    )  # it asks for confirmation; it does not assert the site is impossible


def test_numeric_strings_are_refused(intake):
    """A quantity is a JSON number: text is refused exactly as a boolean is, and the field is named.

    Fixed in the product (GridForge-AI #102); this was a strict xfail until then."""
    out = call(
        "compare",
        {"intake": mutated(intake, lambda x: x["grid"].__setitem__("firm_capacity_MVA", "14"))},
    )
    assert out["ok"] is False
    assert "firm_capacity_MVA" in out["message"] and "string" in out["message"]
