from __future__ import annotations

import copy
import json

import pytest

from gridforge_stress import context_bridge as B
from gridforge_stress.paths import fixtures_dir

pytestmark = pytest.mark.unit
W = 365.25 / 7  # weeks per year, computed independently of the module under test


def weeks(years: float) -> float:
    return years * W


def screen(value=260, low=150, high=520, evidence="E0_ASSUMPTION"):
    """SYNTHETIC payload shaped like the real /v1/screen output for the grid_firm_capacity constraint."""
    lt = {
        "value": value,
        "low": low,
        "high": high,
        "unit": "weeks",
        "evidence": evidence,
        "label": "grid reinforcement lead time",
    }
    return {
        "scenarios": [
            {
                "id": "s0",
                "constraints": [
                    {"id": "tap_off"},
                    {
                        "id": "grid_firm_capacity",
                        "relief": {
                            "lead_time_weeks": lt,
                            "risk": "relief may not exist in the horizon",
                        },
                    },
                ],
            }
        ]
    }


def intake(country="DE"):
    return {"site": {"country": country}} if country else {}


def test_context_fixture_loads_and_matches_hand_checks():
    doc = B.load_queue_context()
    c = doc["contexts"]["cod_2018_2025"]
    assert (c["n"], c["p25"], c["median"], c["p75"]) == (1439, 3.086, 4.394, 5.844)
    assert sum(r["n"] for r in doc["by_region_cod_2018_2025"].values()) == c["n"]
    assert doc["geography"] == "US" and doc["evidence_kind"] == "generation_queue_statistic"


def test_position_is_computed_in_weeks_and_matches_hand_arithmetic():
    out = B.bridge(screen(), intake())
    cmp = out["comparison"]
    assert cmp["observed_p25_weeks"] == pytest.approx(weeks(3.086), abs=0.05)
    assert cmp["observed_median_weeks"] == pytest.approx(weeks(4.394), abs=0.05)
    assert cmp["observed_p75_weeks"] == pytest.approx(weeks(5.844), abs=0.05)
    assert (
        weeks(4.394) <= 260 < weeks(5.844)
        and cmp["position_of_assumption"] == "between_median_and_p75"
    )
    assert cmp["assumed_over_observed_median"] == pytest.approx(260 / weeks(4.394), abs=1e-3)
    assert (
        cmp["assumed_band_covers_observed_median"] is True
        and cmp["assumed_band_covers_observed_iqr"] is True
    )
    assert out["product_assumption"]["years"] == pytest.approx(260 / W, abs=1e-3)


@pytest.mark.parametrize(
    "assumed,expected",
    [
        (100, "below_p25"),
        (200, "between_p25_and_median"),
        (260, "between_median_and_p75"),
        (400, "at_or_above_p75"),
    ],
)
def test_all_four_positions(assumed, expected):
    # observed quartiles in weeks: 161.0 / 229.3 / 304.9
    assert (
        B.bridge(screen(value=assumed), intake())["comparison"]["position_of_assumption"]
        == expected
    )


@pytest.mark.parametrize(
    "region,expected",
    [("ERCOT", "at_or_above_p75"), ("CAISO", "below_p25"), ("West", "between_median_and_p75")],
)
def test_regional_contexts_change_the_position_as_hand_computed(region, expected):
    # ERCOT p75 4.705y = 245.5 wk < 260; CAISO p25 6.329y = 330.2 wk > 260; West median 165.5 < 260 < p75 289.2
    assert (
        B.bridge(screen(), intake(), region=region)["comparison"]["position_of_assumption"]
        == expected
    )


def test_band_coverage_is_reported_honestly_when_the_band_is_narrow():
    narrow = B.bridge(screen(value=260, low=240, high=280), intake())["comparison"]
    assert (
        narrow["assumed_band_covers_observed_iqr"] is False
        and narrow["assumed_band_covers_observed_median"] is False
    )


def test_geography_decides_applicability_and_defaults_to_scale_reference():
    de = B.bridge(screen(), intake("DE"))
    us = B.bridge(screen(), intake("us"))
    none = B.bridge(screen(), intake(None))
    assert de["applicability"] == {
        "site_country": "DE",
        "context_geography": "US",
        "level": "scale_reference_only",
    }
    assert us["applicability"]["level"] == "same_country_context"
    assert (
        none["applicability"]["level"] == "scale_reference_only"
        and none["applicability"]["site_country"] is None
    )
    assert any("scale reference only" in s for s in de["statements"]) and not any(
        "scale reference only" in s for s in us["statements"]
    )


def test_the_bridge_never_changes_the_evidence_class_or_the_input_and_always_labels_context_only():
    payload = screen()
    before = copy.deepcopy(payload)
    out = B.bridge(payload, intake())
    assert payload == before
    assert out["product_evidence_class_unchanged"] == "E0_ASSUMPTION"
    assert (
        out["evidence"]["level"] == "context_only"
        and out["evidence"]["allows_site_specific_claim"] is False
    )
    assert any("unchanged" in s for s in out["statements"])
    assert len(out["must_not_claim"]) == 4 and any("load" in m for m in out["must_not_claim"])
    assert (
        "survivorship" in out["observed_context"]["definition"]
        and out["observed_context"]["limits"]
    )


def test_refuses_instead_of_defaulting_when_the_product_assumption_is_absent_or_incomplete():
    with pytest.raises(B.BridgeError, match="public tier"):
        B.bridge({"scenarios": [{"id": "s", "constraints": []}]}, intake())
    with pytest.raises(B.BridgeError):
        B.bridge({"scenarios": []}, intake())
    bad = screen()
    bad["scenarios"][0]["constraints"][1]["relief"]["lead_time_weeks"]["low"] = None
    with pytest.raises(B.BridgeError, match="refusing to fill"):
        B.bridge(bad, intake())


def test_unknown_region_or_context_is_an_error():
    with pytest.raises(B.BridgeError, match="unknown region"):
        B.bridge(screen(), intake(), region="Mars")
    with pytest.raises(B.BridgeError, match="unknown context"):
        B.bridge(screen(), intake(), context_key="nope")


def test_invalid_context_file_is_rejected(tmp_path):
    doc = json.loads((fixtures_dir() / B.CONTEXT_FILE).read_text())
    doc["contexts"]["cod_2018_2025"]["median"] = 1.0  # below p25
    p = tmp_path / "c.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(B.BridgeError, match="not a valid quartile"):
        B.load_queue_context(p)
    doc["schema"] = "other"
    p.write_text(json.dumps(doc))
    with pytest.raises(B.BridgeError, match="schema"):
        B.load_queue_context(p)


def test_context_labels_are_human_readable():
    assert B.bridge(screen(), intake())["observed_context"]["name"] == "COD years 2018-2025"
    assert (
        B.bridge(screen(), intake(), context_key="all_cod_years")["observed_context"]["name"]
        == "all COD years"
    )
    assert "COD years 2018-2025" in " ".join(B.bridge(screen(), intake())["statements"])


def test_bridge_states_pooled_mature_cohort_survivorship_from_the_fixture():
    out = B.bridge(screen(), intake(), context_key="cod_2018_2025")
    sv = out["survivorship"]
    # hand sums of the 2010-2015 cohorts: 5633 entered, 1299 operational, 4153 withdrawn
    assert (sv["entered"], sv["operational"], sv["withdrawn"]) == (5633, 1299, 4153)
    assert sv["active"] + sv["suspended"] + sv["unknown"] == 181
    assert sv["share_operational_of_entered"] == 0.231
    assert sv["share_operational_of_resolved"] == 0.238
    assert any("Survivorship" in x for x in out["statements"])
