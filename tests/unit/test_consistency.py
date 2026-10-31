"""The cross-document checker: a faithful deck passes; each way a deck can disagree with its screen is caught."""

from __future__ import annotations

import copy

import pytest

from gridforge_stress.consistency import (
    check_deck,
    check_identity,
    check_study_against_screen,
    visible_text,
)

pytestmark = pytest.mark.unit


def scenario(sid, found, after, weeks, basis="assumed", setter="Transformer capacity"):
    return {
        "id": sid,
        "as_found": {"racks": found},
        "after_ladder": {"racks": after},
        "time_to_power": {
            "weeks_to_full": weeks,
            "schedule_complete": True,
            "sets_the_date": setter,
            "date_basis": basis,
        },
    }


SCREEN = {
    "project": {"client": "Reference Project (synthetic)"},
    "recommended": "full_dlc_btm",
    "scenarios": [scenario("full_dlc", 0, 28, 160.0), scenario("full_dlc_btm", 0, 62, 160.0)],
}

DECK = (
    "<title>t</title><style>body{color:red}</style><h1>Walkthrough</h1><p>After the ladder 62 racks · Full DLC</p>"
    "<p>Time to full capacity 160 weeks (range 80&ndash;210) · set by transformer capacity · assumed lead time, not a date (E0)</p>"
    "<p>Reference Project (synthetic) · modelled, not measured</p>"
)


def test_visible_text_drops_style_and_decodes_entities():
    t = visible_text(DECK)
    assert "color:red" not in t and "80\N{EN DASH}210" in t and "<" not in t


def test_a_faithful_deck_is_consistent():
    assert check_deck(SCREEN, DECK) == []


@pytest.mark.parametrize(
    "name,doc",
    [
        ("wrong_rack_count", DECK.replace("62 racks", "64 racks")),
        ("wrong_weeks", DECK.replace("160 weeks", "120 weeks")),
        ("no_time_statement", DECK.replace("Time to full capacity", "Time")),
        ("wrong_setter", DECK.replace("transformer capacity", "ups capacity")),
        (
            "assumption_not_disclosed",
            DECK.replace("assumed lead time, not a date (E0)", "on track"),
        ),
        ("modelled_not_measured_dropped", DECK.replace("modelled, not measured", "verified")),
    ],
)
def test_each_way_a_deck_can_disagree_is_caught(name, doc):
    assert check_deck(SCREEN, doc), name


def test_a_supported_date_called_an_assumption_is_caught():
    s = copy.deepcopy(SCREEN)
    s["scenarios"][1]["time_to_power"]["date_basis"] = "supported"
    assert any("calls the date an assumption" in p for p in check_deck(s, DECK))


def test_the_deck_is_judged_against_the_recommended_scenario_not_any_scenario():
    s = copy.deepcopy(SCREEN)
    s["recommended"] = "full_dlc"  # 28 racks
    assert check_deck(s, DECK)


STUDY = {"project": SCREEN["project"], "scenarios": copy.deepcopy(SCREEN["scenarios"])}


def test_a_faithful_study_is_consistent():
    assert check_study_against_screen(SCREEN, STUDY) == []


@pytest.mark.parametrize(
    "name,edit",
    [
        ("racks", lambda d: d["scenarios"][1]["after_ladder"].update(racks=60)),
        ("as_found", lambda d: d["scenarios"][0]["as_found"].update(racks=3)),
        ("weeks", lambda d: d["scenarios"][1]["time_to_power"].update(weeks_to_full=100.0)),
        ("basis", lambda d: d["scenarios"][1]["time_to_power"].update(date_basis="supported")),
        ("scenario_dropped", lambda d: d["scenarios"].pop()),
        ("project", lambda d: d.update(project={"client": "Someone Else"})),
    ],
)
def test_each_way_a_study_can_disagree_is_caught(name, edit):
    s = copy.deepcopy(STUDY)
    edit(s)
    assert check_study_against_screen(SCREEN, s), name


def test_identity_synthetic_both_ways():
    docs = {"deck": DECK, "proposal": "<p>Proposal Reference Project (synthetic)</p>"}
    assert check_identity(SCREEN["project"], True, docs) == []
    assert check_identity(
        SCREEN["project"], False, docs
    )  # a real client's document calling itself synthetic
    assert check_identity(
        SCREEN["project"],
        True,
        {"deck": "<p>Reference Project (synthetic)</p>".replace("synthetic", "demo")},
    )
    assert check_identity(
        SCREEN["project"], True, {"deck": "<p>Another client, synthetic</p>"}
    )  # wrong client
