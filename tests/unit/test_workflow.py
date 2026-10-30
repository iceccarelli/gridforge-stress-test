"""The workflow oracle must catch wrong product output, and must never turn an absent product into a pass."""

from __future__ import annotations

import copy
import json

import pytest

from gridforge_stress import workflow as W
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.product_adapter import ProductUnavailable

pytestmark = pytest.mark.unit

INTAKE = {
    "grid": {
        "firm_capacity_MVA": 30.0,
        "power_factor": 0.97,
        "contracted_MW": 27.0,
        "current_site_peak_MW": {"value": 9.0},
    },
    "btm_options": [
        {"id": "G", "capacity_MW": 5.0, "firm": True},
        {"id": "B", "capacity_MW": 2.0, "firm": True, "firm_capacity_factor": 0.5},
        {"id": "X", "capacity_MW": 9.0, "firm": False},
    ],
}


def lt(name: str, weeks: float, lo: float, hi: float) -> dict:
    return {
        "name": name,
        "missing": False,
        "lead_time_weeks": {"value": weeks, "low": lo, "high": hi, "evidence": "E0_ASSUMPTION"},
    }


def scenario(sid: str, found: int, after: int, items: list[dict] | None = None) -> dict:
    items = (
        [lt("Transformer capacity", 160, 80, 210), lt("UPS", 30, 20, 52)]
        if items is None
        else items
    )
    dated = after > 0
    worst = (
        max(items, key=lambda x: x["lead_time_weeks"]["value"])["lead_time_weeks"]
        if items
        else None
    )
    return {
        "id": sid,
        "as_found": {"racks": found},
        "after_ladder": {"racks": after},
        "time_to_power": {
            "status": "dated" if dated else "no_deployable_racks",
            "weeks": (worst["value"] if worst else 0.0) if dated else None,
            "low": worst["low"] if worst else None,
            "high": worst["high"] if worst else None,
            "first_rack_weeks": 0.0,
            "date_basis": "assumed" if dated else "none",
            "date_label": "assumed lead time, not a date (E0)" if dated else "no date",
            "lead_times": items if dated else [],
        },
        "economics": {
            "capex_total_eur": {"value": 10.0, "low": 5.0, "high": 20.0} if dated else None,
            "capex_complete": False,
            "decision_grade": False,
            "unpriced_components": [{"component": "CDU commissioning"}],
        },
        "grade": "indicative" if dated else "undated",
    }


def payload() -> dict:
    return {
        "schema": "gridforge/ttp-compare/1",
        "scenarios": [
            scenario("retained_air", 0, 0),
            scenario("full_dlc", 20, 60),
            scenario("full_dlc_btm", 20, 90),
        ],
        "ranking": {"leader": "full_dlc_btm", "tie": False},
        "grade": "indicative",
        "caveats": ["At least one date rests on an assumed lead time (E0)."],
    }


def outcomes(out: dict) -> dict[str, str]:
    return {c.id: c.outcome for c in W.evaluate_compare(INTAKE, out)}


def test_a_sound_payload_passes_every_check():
    assert set(outcomes(payload()).values()) == {W.PASS}


def test_supply_headroom_uses_the_lesser_of_firm_and_contracted_less_site_peak_and_only_firm_btm():
    assert W.supply_headroom_mw(INTAKE, btm=False) == pytest.approx(27.0 - 9.0)
    assert W.supply_headroom_mw(INTAKE, btm=True) == pytest.approx(27.0 - 9.0 + 5.0 + 1.0)
    assert (
        W.supply_headroom_mw(
            {**INTAKE, "grid": {**INTAKE["grid"], "current_site_peak_MW": 99}}, btm=False
        )
        == 0.0
    )


def mutate(fn):
    out = payload()
    fn(out)
    return out


def sc(out: dict, sid: str) -> dict:
    return next(s for s in out["scenarios"] if s["id"] == sid)


CASES = {
    "more racks than the supply can feed": (
        lambda o: sc(o, "full_dlc").__getitem__("after_ladder").__setitem__("racks", 140),
        "compare.supply_bound",
    ),
    "as found above after-ladder": (
        lambda o: sc(o, "full_dlc").__getitem__("as_found").__setitem__("racks", 61),
        "compare.supply_bound",
    ),
    "btm reduces capacity": (
        lambda o: sc(o, "full_dlc_btm").__getitem__("after_ladder").__setitem__("racks", 10),
        "compare.btm_monotone",
    ),
    "leader is not the maximum": (
        lambda o: o["ranking"].__setitem__("leader", "full_dlc"),
        "compare.ranking",
    ),
    "leader named for a tie": (
        lambda o: sc(o, "full_dlc").__getitem__("after_ladder").__setitem__("racks", 90),
        "compare.ranking",
    ),
    "date is not the critical path": (
        lambda o: sc(o, "full_dlc").__getitem__("time_to_power").__setitem__("weeks", 30),
        "compare.dates",
    ),
    "range is not the setting item's": (
        lambda o: sc(o, "full_dlc").__getitem__("time_to_power").__setitem__("high", 999),
        "compare.dates",
    ),
    "assumed date not labelled": (
        lambda o: (
            sc(o, "full_dlc").__getitem__("time_to_power").__setitem__("date_label", "160 weeks")
        ),
        "compare.dates",
    ),
    "E0 lead time presented as supported": (
        lambda o: (
            sc(o, "full_dlc").__getitem__("time_to_power").__setitem__("date_basis", "supported")
        ),
        "compare.dates",
    ),
    "capex for a scenario with nothing deployable": (
        lambda o: (
            sc(o, "retained_air")
            .__getitem__("economics")
            .__setitem__("capex_total_eur", {"value": 1, "low": 1, "high": 1})
        ),
        "compare.economics",
    ),
    "incomplete capex presented as decision grade": (
        lambda o: sc(o, "full_dlc").__getitem__("economics").__setitem__("decision_grade", True),
        "compare.economics",
    ),
    "grade above the evidence": (
        lambda o: o.__setitem__("grade", "screening"),
        "compare.no_overclaim",
    ),
    "no caveats on assumed dates": (lambda o: o.__setitem__("caveats", []), "compare.no_overclaim"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_oracle_fails_on_wrong_product_output(name):
    fn, check_id = CASES[name]
    got = outcomes(mutate(fn))
    assert got[check_id] == W.FAIL, (
        got
    )  # knock-on failures in other checks are legitimate; the named one must fire


def test_a_dated_scenario_with_no_lead_time_must_need_no_relief():
    ok = mutate(lambda o: o["scenarios"].__setitem__(1, scenario("full_dlc", 60, 60, items=[])))
    assert outcomes(ok)["compare.dates"] == W.PASS
    bad = mutate(lambda o: o["scenarios"].__setitem__(1, scenario("full_dlc", 20, 60, items=[])))
    assert outcomes(bad)["compare.dates"] == W.FAIL


def test_an_absent_product_is_blocked_and_never_a_pass():
    def api(*_a, **_k):
        raise ProductUnavailable("no checkout")

    rec = W.run(INTAKE, api=api)
    assert [c.outcome for c in rec.checks] == [W.BLOCKED]
    assert rec.exit_code == 3


def test_a_refused_intake_is_a_failure():
    rec = W.run(INTAKE, api=lambda *_a, **_k: {"ok": False, "message": "nope"})
    assert rec.checks[0].outcome == W.FAIL and rec.exit_code == 1


def test_a_malformed_payload_is_a_recorded_failure_not_a_traceback():
    rec = W.run(INTAKE, api=lambda *_a, **_k: {"ok": True, "result": {"scenarios": [{"id": "x"}]}})
    assert any(c.id == "payload.shape" and c.outcome == W.FAIL for c in rec.checks)


def test_the_record_is_machine_readable_and_states_what_it_cannot_judge():
    rec = W.Record("abc", "2026-10-27", checks=[W.Check("a", W.PASS, "c")])
    doc = json.loads(json.dumps(rec.to_json()))
    assert doc["summary"] == {"PASS": 1, "FAIL": 0, "BLOCKED": 0, "UNCHECKED": 0}
    assert doc["limitations"] and doc["open_questions"] and "synthetic" in doc["label"]
    assert rec.exit_code == 0
    assert W.Record("abc", "d").exit_code == 3, "a record with no checks is not a pass"


def test_the_example_pack_intake_is_labelled_synthetic_and_matches_its_manifest():
    import hashlib

    p = fixtures_dir() / "product" / "nordic_ai_ready_intake.json"
    m = json.loads((p.parent / (p.name + ".manifest.json")).read_text())
    assert m["origin"] == "synthetic_fixture" and m["retrieval_status"] == "synthetic"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == m["sha256"]
    assert "SYNTHETIC" in json.loads(p.read_text())["project"]["note"]
    assert copy.deepcopy(m)["limitations"]


# --- the product's grid_queue_context, judged against this harness's own measurement ------------------------------------------------

WEEKS = 365.25 / 7


def queue_context(*, years=(3.086, 4.394, 5.844), n=1439, level="scale_reference_only") -> dict:
    """What a faithful product ships. The numbers are written out here, from the fixture's published measurement, on purpose."""
    p25, med, p75 = years
    return {
        "kind": "context_only",
        "source": {
            "workbook_sha256": json.loads(
                (fixtures_dir() / "lbnl_ir_to_cod_context.json").read_text()
            )["provenance"]["workbook_sha256"]
        },
        "observed": {
            "n": n,
            "p25_years": p25,
            "median_years": med,
            "p75_years": p75,
            "p25_weeks": round(p25 * WEEKS, 1),
            "median_weeks": round(med * WEEKS, 1),
            "p75_weeks": round(p75 * WEEKS, 1),
        },
        "survivorship": {
            "entered": 5633,
            "operational": 1299,
            "withdrawn": 4153,
            "unresolved": 181,
            "share_operational_of_entered": 0.231,
        },
        "applicability": {"level": level},
        "limits": ["Survivorship: only requests that reached commercial operation are included"],
        "must_not_claim": [
            "a",
            "b",
            "c",
            "that the assumption is validated, confirmed or raised in evidence class",
        ],
        "statements": [
            "It is not a connection time for a load, and it is not evidence about this site.",
            "This context changes no figure and no evidence class elsewhere in the response.",
        ],
    }


def with_context(**over) -> dict:
    out = payload()
    out["grid_queue_context"] = {**queue_context(), **over}
    out["caveats"] = [*out.get("caveats", []), "Context only: grid_queue_context ..."]
    return out


def screen_document(
    *,
    months=(53, 37, 70),
    n=1439,
    share="23%",
    entered=5633,
    heading=True,
    disclaimer=True,
    order_ok=True,
) -> str:
    median, p25, p75 = months
    section = (
        "## 5. Grid wait: what has been observed elsewhere (context only)\n\n"
        f"- US requests took a median of {median} months from request to operation (middle half {p25}-{p75} months), across {n} projects. "
        "Source: Lawrence Berkeley National Laboratory, Queued Up 2026 edition, CC BY 4.0.\n"
        f"- Only {share} of the {entered} requests that entered a queue in 2010-2015 reached operation.\n"
        + (
            "\n> Context only: this is not a connection time for a load or a data centre.\n"
            if disclaimer
            else ""
        )
    )
    nxt = "## 6. Recommended next step\n\ntext\n"
    body = (section + nxt) if order_ok else (nxt + section)
    if not heading:
        body = body.replace("Grid wait: what has been observed elsewhere (context only)", "Other")
    return "# Density Screen\n\n" + body


def regional_api(compare_out: dict, *, observed=None, change=None, document=None):
    """A product that answers a region request with PJM's measured figures; `change` lets a test make it move something else."""

    def api(route, body, report_date="2026-10-27"):
        if route == "screen":
            doc = document if document is not None else screen_document()
            return {"ok": True, "result": {"format": "md", "document": doc}}
        out = copy.deepcopy(compare_out)
        out["grid_queue_context"]["observed"] = (
            observed or queue_context(years=(3.243, 4.893, 6.45), n=427)["observed"]
        )
        if change:
            change(out)
        return {"ok": True, "result": out}

    return api


def judged(out: dict, api=None) -> dict[str, str]:
    checks = W.evaluate_queue_context(INTAKE, out, api or regional_api(out), "2026-10-27")
    return {c.id: c.outcome for c in checks}


def test_a_product_without_the_context_block_is_not_judged_for_it():
    assert W.evaluate_queue_context(INTAKE, payload(), regional_api(payload()), "2026-10-27") == []


def test_a_faithful_copy_of_the_measurement_passes_all_four_checks():
    assert judged(with_context()) == {
        "context.matches_independent_measurement": "PASS",
        "context.limits_are_stated": "PASS",
        "context.region_moves_nothing_else": "PASS",
        "context.document_matches": "PASS",
    }


QUEUE_CASES = {
    "a wrong median": (
        lambda o: o["grid_queue_context"]["observed"].update(median_years=3.0, median_weeks=156.4),
        "context.matches_independent_measurement",
    ),
    "a wrong sample size": (
        lambda o: o["grid_queue_context"]["observed"].update(n=1440),
        "context.matches_independent_measurement",
    ),
    "weeks that are not the years": (
        lambda o: o["grid_queue_context"]["observed"].update(median_weeks=300.0),
        "context.matches_independent_measurement",
    ),
    "a wrong survivorship share": (
        lambda o: o["grid_queue_context"]["survivorship"].update(share_operational_of_entered=0.9),
        "context.matches_independent_measurement",
    ),
    "unresolved that does not add up": (
        lambda o: o["grid_queue_context"]["survivorship"].update(unresolved=0),
        "context.matches_independent_measurement",
    ),
    "a different workbook": (
        lambda o: o["grid_queue_context"]["source"].update(workbook_sha256="0" * 64),
        "context.matches_independent_measurement",
    ),
    "not labelled context only": (
        lambda o: o["grid_queue_context"].update(kind="evidence"),
        "context.limits_are_stated",
    ),
    "dropping the load-connection disclaimer": (
        lambda o: o["grid_queue_context"].update(statements=["fine."]),
        "context.limits_are_stated",
    ),
    "dropping the survivorship limit": (
        lambda o: o["grid_queue_context"].update(limits=[]),
        "context.limits_are_stated",
    ),
    "dropping the refused claims": (
        lambda o: o["grid_queue_context"].update(must_not_claim=["a"]),
        "context.limits_are_stated",
    ),
    "claiming same-country context for a site with no US country": (
        lambda o: o["grid_queue_context"]["applicability"].update(level="same_country_context"),
        "context.limits_are_stated",
    ),
    "an unlabelled comparison": (
        lambda o: o.update(caveats=["indicative"]),
        "context.limits_are_stated",
    ),
}


@pytest.mark.parametrize("name", sorted(QUEUE_CASES))
def test_the_oracle_fails_a_product_that_misstates_its_own_context(name):
    change, check = QUEUE_CASES[name]
    out = with_context()
    change(out)
    result = judged(out)
    assert result[check] == "FAIL", (name, result)


def test_the_oracle_fails_a_region_that_returns_the_wrong_figures_or_moves_something_else():
    out = with_context()
    wrong = judged(
        out, regional_api(out, observed=queue_context(years=(3.0, 4.0, 5.0), n=427)["observed"])
    )
    assert wrong["context.region_moves_nothing_else"] == "FAIL"
    moved = judged(
        out, regional_api(out, change=lambda o: o["scenarios"][0]["after_ladder"].update(racks=999))
    )
    assert moved["context.region_moves_nothing_else"] == "FAIL"


def test_a_region_the_product_refuses_is_a_failure_not_a_pass():
    out = with_context()

    def refuse(route, body, report_date="2026-10-27"):
        return {"ok": False, "message": "unknown grid_queue_region"}

    assert judged(out, refuse)["context.region_moves_nothing_else"] == "FAIL"


def test_the_expected_values_come_from_the_harness_fixture_not_from_the_product():
    doc = json.loads((fixtures_dir() / "lbnl_ir_to_cod_context.json").read_text())
    pooled = doc["contexts"]["cod_2018_2025"]
    assert (pooled["n"], pooled["p25"], pooled["median"], pooled["p75"]) == (
        1439,
        3.086,
        4.394,
        5.844,
    )
    assert doc["by_region_cod_2018_2025"]["PJM"]["n"] == 427


DOCUMENT_CASES = {
    "a wrong median in the text": dict(months=(40, 37, 70)),
    "a wrong middle half": dict(months=(53, 30, 70)),
    "a wrong sample size": dict(n=1000),
    "a wrong survivorship share": dict(share="90%"),
    "a wrong cohort size": dict(entered=100),
    "no disclaimer": dict(disclaimer=False),
    "no section heading": dict(heading=False),
    "the section after the next step": dict(order_ok=False),
}


@pytest.mark.parametrize("name", sorted(DOCUMENT_CASES))
def test_the_oracle_fails_a_document_that_misstates_the_measurement(name):
    out = with_context()
    result = judged(out, regional_api(out, document=screen_document(**DOCUMENT_CASES[name])))
    assert result["context.document_matches"] == "FAIL", (name, result)


def test_a_refused_document_is_a_failure():
    out = with_context()

    def refuse_doc(route, body, report_date="2026-10-27"):
        if route == "screen":
            return {"ok": False, "message": "refused"}
        return regional_api(out)(route, body, report_date)

    assert judged(out, refuse_doc)["context.document_matches"] == "FAIL"


# --- the product's retail_price_context, judged against this harness's own parse of EIA's table -------------------------------------


def price_block(area="U.S. Total", *, ind=None, com=None, **over) -> dict:
    from gridforge_stress.paths import fixtures_dir
    from gridforge_stress.price import load_price_context

    doc = load_price_context(fixtures_dir() / "eia_retail_price_context.json")
    row = doc["rows"][area]

    def sector(name, forced):
        c = forced if forced is not None else row[name]["current"]
        return {"cents_per_kWh": c, "usd_per_MWh": round(c * 10, 1)}

    block = {
        "kind": "context_only",
        "source": {
            "page_sha256": doc["provenance"]["page_sha256"],
            "release_date": doc["release_date"],
        },
        "observed": {
            "area": area,
            "period": doc["period"],
            "sectors": {
                "commercial": sector("commercial", com),
                "industrial": sector("industrial", ind),
            },
        },
        "applicability": {"level": "scale_reference_only"},
        "assumed": {
            "eur_per_MWh": 140.0,
            "source": "library placeholder (evidence class E0); no contracted tariff supplied",
            "evidence_class_unchanged": True,
        },
        "statements": [
            "A load's price is set by its contracted tariff.",
            "They are shown in their own currencies and are not converted.",
        ],
        "limits": ["a", "b", "c", "that the price is validated or changed in evidence class"],
    }
    block.update(over)
    return block


def with_price(**over) -> dict:
    out = payload()
    out["retail_price_context"] = price_block(**over)
    out["caveats"] = [*out.get("caveats", []), "Context only: retail_price_context ..."]
    return out


def price_document(
    *,
    ind="96.4",
    com="144.7",
    used=140,
    heading=True,
    disclaimer=True,
    convert_note=True,
    order_ok=True,
) -> str:
    section = (
        "## 6. Electricity price: what has been published (context only)\n\n"
        f"- U.S. Total, 2026-08: industrial customers paid {ind} US dollars per MWh on average and commercial customers {com} US dollars per MWh.\n"
        f"- This document uses {used} euros per MWh (library placeholder).\n"
        + (
            "- The two figures are in different currencies and are not converted here.\n"
            if convert_note
            else ""
        )
        + ("\n> Context only: this is not the tariff for this site.\n" if disclaimer else "")
    )
    nxt = "## 7. Recommended next step\n\ntext\n"
    body = (section + nxt) if order_ok else (nxt + section)
    if not heading:
        body = body.replace("Electricity price: what has been published (context only)", "Other")
    return "# Density Screen\n\n" + body


def state_api(compare_out: dict, *, block=None, change=None, document=None):
    def api(route, body, report_date="2026-10-27"):
        if route == "screen":
            return {
                "ok": True,
                "result": {
                    "format": "md",
                    "document": document if document is not None else price_document(),
                },
            }
        out = copy.deepcopy(compare_out)
        out["retail_price_context"] = block or price_block("Virginia")
        if change:
            change(out)
        return {"ok": True, "result": out}

    return api


def judged_price(out: dict, api=None) -> dict[str, str]:
    checks = W.evaluate_price_context(INTAKE, out, api or state_api(out), "2026-10-27")
    return {c.id: c.outcome for c in checks}


def test_a_product_without_the_price_block_is_not_judged_for_it():
    assert W.evaluate_price_context(INTAKE, payload(), state_api(payload()), "2026-10-27") == []


def test_a_faithful_copy_of_the_parse_passes_all_four_checks():
    assert judged_price(with_price()) == {
        "context.price_matches_independent_parse": "PASS",
        "context.price_limits_are_stated": "PASS",
        "context.price_state_moves_nothing_else": "PASS",
        "context.price_document_matches": "PASS",
    }


@pytest.mark.parametrize(
    "doc",
    [
        price_document(ind="86.4"),
        price_document(com="14.47"),
        price_document(used=125),
        price_document(heading=False),
        price_document(disclaimer=False),
        price_document(convert_note=False),
        price_document(order_ok=False),
    ],
    ids=[
        "industrial",
        "commercial",
        "price-used",
        "no-section",
        "no-disclaimer",
        "no-conversion-note",
        "wrong-order",
    ],
)
def test_a_document_that_misstates_the_prices_or_drops_a_limit_fails(doc):
    out = with_price()
    assert (
        judged_price(out, state_api(out, document=doc))["context.price_document_matches"] == "FAIL"
    )


def test_a_wrong_industrial_price_fails_the_figures_check_and_names_it():
    out = with_price(ind=8.64)
    checks = W.evaluate_price_context(INTAKE, out, state_api(out), "2026-10-27")
    c = next(x for x in checks if x.id == "context.price_matches_independent_parse")
    assert c.outcome == "FAIL" and "8.64" in c.detail and "9.64" in c.detail


def test_a_wrong_conversion_fails():
    out = with_price()
    out["retail_price_context"]["observed"]["sectors"]["industrial"]["usd_per_MWh"] = 90.0
    assert judged_price(out)["context.price_matches_independent_parse"] == "FAIL"


def test_a_stale_or_foreign_source_fails():
    out = with_price()
    out["retail_price_context"]["source"]["page_sha256"] = "0" * 64
    assert judged_price(out)["context.price_matches_independent_parse"] == "FAIL"
    out = with_price()
    out["retail_price_context"]["observed"]["period"] = "2026-07"
    assert judged_price(out)["context.price_matches_independent_parse"] == "FAIL"


def test_dropping_the_no_conversion_statement_fails_the_limits_check():
    out = with_price(statements=["A load's price is set by its contracted tariff."])
    assert judged_price(out)["context.price_limits_are_stated"] == "FAIL"


def test_a_placeholder_not_labelled_as_one_fails():
    out = with_price(
        assumed={"eur_per_MWh": 140.0, "source": "from EIA", "evidence_class_unchanged": True}
    )
    assert judged_price(out)["context.price_limits_are_stated"] == "FAIL"


def test_a_state_request_that_returns_other_figures_or_moves_something_fails():
    out = with_price()
    wrong = state_api(out, block=price_block("Texas"))
    assert judged_price(out, wrong)["context.price_state_moves_nothing_else"] == "FAIL"
    moved = state_api(out, change=lambda o: o.update(headline="changed"))
    # the compare payload built by payload() carries no headline key; any added key is a movement
    assert judged_price(out, moved)["context.price_state_moves_nothing_else"] == "FAIL"


def test_a_tariff_declared_as_value_and_note_is_judged_as_declared_not_as_a_placeholder():
    intake = copy.deepcopy(INTAKE)
    intake.setdefault("economics", {})["electricity_eur_per_MWh"] = {
        "value": 125,
        "note": "contract",
    }
    out = with_price()
    out["retail_price_context"]["assumed"] = {
        "eur_per_MWh": 125.0,
        "source": "declared by the buyer (economics.electricity_eur_per_MWh)",
        "evidence_class_unchanged": True,
    }
    checks = W.evaluate_price_context(intake, out, state_api(out), "2026-10-27")
    assert {c.id: c.outcome for c in checks}["context.price_limits_are_stated"] == "PASS"
    # and a product that still calls that declared price a placeholder, or reports another number, is caught
    out["retail_price_context"]["assumed"]["eur_per_MWh"] = 140.0
    checks = W.evaluate_price_context(intake, out, state_api(out), "2026-10-27")
    assert {c.id: c.outcome for c in checks}["context.price_limits_are_stated"] == "FAIL"
