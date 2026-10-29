"""End-to-end Time-to-Power workflow, judged by an independent oracle.

`run` drives the product's REAL handlers through the customer journey (compare, screen, then what-if diffs) and
`evaluate_compare` / `evaluate_whatifs` check what came back against expectations derived from the INTAKE and from
physics bounds, never from the product's own calculation. Each check ends in exactly one of PASS, FAIL, BLOCKED or
UNCHECKED; a blocked or unchecked check is never a pass, and `Record.exit_code` is 0 only when every check passed.

The oracle checks structure, bounds, monotonicity and honesty of labels. It does not reproduce the engine's rack
count, and it says so in `Record.limitations`.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .product_adapter import ProductUnavailable, call, claim_violations

PASS, FAIL, BLOCKED, UNCHECKED = "PASS", "FAIL", "BLOCKED", "UNCHECKED"

#: GB300 NVL72 rack power band (kW) as CITED BY THE PRODUCT (Lenovo Press LP2357, vendor-published, 2026). The
#: harness has not independently verified the vendor figure; it only needs a physical lower bound per rack, so a
#: wrong low end can loosen a bound but cannot make a correct engine fail.
RACK_KW_LOW = 132.0

LIMITATIONS = (
    "Oracle checks bounds, monotonicity, cross-endpoint consistency and label honesty; it does not reproduce the "
    "engine's rack count, so a wrong-but-plausible count inside the bounds is not caught here.",
    "The rack power lower bound is the vendor figure cited by the product and is not independently verified.",
    "The supply bound ignores cooling overhead (PUE >= 1), so it can only be looser than physics, never tighter.",
    "A generation queue or a library lead time is not an energisation commitment; every date checked here is an "
    "assumption and is required to say so.",
)


#: Observations the oracle cannot judge (no independent expected value exists). They are not checks and never pass.
OPEN_QUESTIONS = (
    "A liquid-cooling scenario with racks as found reports first_rack_weeks 0.0: the lead times cover capacity "
    "relief only, so the time to convert the hall to that architecture is in no date. Whether that is intended "
    "scope is for the product owner; the date label already says 'assumed lead time, not a date'.",
    "The headline 'racks today' is the recommended architecture's as-found figure; another architecture may host "
    "more racks as found. Which architecture 'today' refers to is a wording decision for the product owner.",
)


@dataclass
class Check:
    id: str
    outcome: str
    claim: str
    detail: str = ""


@dataclass
class Record:
    intake_sha256: str
    report_date: str
    product: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    limitations: tuple[str, ...] = LIMITATIONS
    open_questions: tuple[str, ...] = OPEN_QUESTIONS

    def counts(self) -> dict[str, int]:
        return {
            o: sum(c.outcome == o for c in self.checks) for o in (PASS, FAIL, BLOCKED, UNCHECKED)
        }

    @property
    def exit_code(self) -> int:
        n = self.counts()
        if n[FAIL]:
            return 1
        return 0 if self.checks and n[PASS] == len(self.checks) else 3

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": "gridforge-stress/workflow-record/1",
            "label": "synthetic intake; product behaviour judged, no live data involved",
            "intake_sha256": self.intake_sha256,
            "report_date": self.report_date,
            "product": self.product,
            "summary": self.counts(),
            "checks": [c.__dict__ for c in self.checks],
            "limitations": list(self.limitations),
            "open_questions": list(self.open_questions),
        }


def _check(out: list[Check], cid: str, claim: str, problems: list[str]) -> None:
    out.append(Check(cid, FAIL if problems else PASS, claim, "; ".join(problems)))


def _num(v: Any) -> float:
    return float(v["value"] if isinstance(v, dict) else v)


def supply_headroom_mw(intake: dict[str, Any], *, btm: bool) -> float:
    """Upper bound on new IT supply (MW) from the intake alone: firm grid ceiling less today's site peak, plus the
    firm part of declared behind-the-meter options when the scenario includes them."""
    g = intake["grid"]
    ceiling = min(_num(g["firm_capacity_MVA"]) * _num(g["power_factor"]), _num(g["contracted_MW"]))
    headroom = ceiling - _num(g["current_site_peak_MW"])
    if btm:
        for o in intake.get("btm_options", []):
            if o.get("firm", False):
                headroom += _num(o["capacity_MW"]) * float(o.get("firm_capacity_factor", 1.0))
    return max(headroom, 0.0)


def evaluate_compare(intake: dict[str, Any], out: dict[str, Any]) -> list[Check]:
    res: list[Check] = []
    scen = {s["id"]: s for s in out.get("scenarios", [])}
    _check(
        res,
        "compare.schema",
        "compare names its schema and returns scenarios",
        []
        if out.get("schema") == "gridforge/ttp-compare/1" and scen
        else ["schema or scenarios missing"],
    )

    bad = []
    for sid, s in scen.items():
        found, after = s["as_found"]["racks"], s["after_ladder"]["racks"]
        bound = supply_headroom_mw(intake, btm=sid.endswith("_btm")) * 1000 / RACK_KW_LOW
        if after > bound + 1e-9:
            bad.append(f"{sid}: {after} racks exceed the supply bound {bound:.1f}")
        if found > after:
            bad.append(f"{sid}: as found {found} exceeds after-ladder {after}")
    _check(
        res, "compare.supply_bound", "no scenario hosts more racks than the supply can feed", bad
    )

    grid_only, with_btm = scen.get("full_dlc"), scen.get("full_dlc_btm")
    p = []
    if grid_only and with_btm:
        lo, hi = grid_only["after_ladder"]["racks"], with_btm["after_ladder"]["racks"]
        if hi < lo:
            p = [f"behind-the-meter supply reduced racks: {hi} < {lo}"]
    _check(res, "compare.btm_monotone", "adding firm on-site supply never reduces capacity", p)

    counts = {
        sid: s["after_ladder"]["racks"] for sid, s in scen.items() if s["after_ladder"]["racks"] > 0
    }
    top = max(counts.values(), default=None)
    leaders = [sid for sid, n in counts.items() if n == top]
    rk = out.get("ranking", {})
    p = []
    if top is None:
        p = [] if rk.get("leader") is None else ["a leader is named although nothing is deployable"]
    elif len(leaders) > 1:
        p = (
            []
            if rk.get("leader") is None or rk.get("tie")
            else ["a tied top is named as a strict leader"]
        )
    elif rk.get("leader") != leaders[0]:
        p = [f"leader {rk.get('leader')!r}, independent maximum is {leaders[0]!r}"]
    _check(res, "compare.ranking", "the leader is the strict maximum of deployable racks", p)

    bad = []
    for sid, s in scen.items():
        t = s["time_to_power"]
        if t["status"] != "dated":
            if t.get("weeks") is not None:
                bad.append(f"{sid}: weeks given with status {t['status']!r}")
            continue
        items = [x for x in t["lead_times"] if not x.get("missing") and x.get("lead_time_weeks")]
        if (
            not items
        ):  # nothing to build: the racks are the as-found racks and there is nothing to wait for
            if t["weeks"] != 0 or s["as_found"]["racks"] != s["after_ladder"]["racks"]:
                bad.append(f"{sid}: dated with no lead-time item although racks depend on relief")
            continue
        worst = max(items, key=lambda x: x["lead_time_weeks"]["value"])["lead_time_weeks"]
        if t["weeks"] != worst["value"]:
            bad.append(f"{sid}: weeks {t['weeks']} is not the longest lead time {worst['value']}")
        if (t["low"], t["high"]) != (worst["low"], worst["high"]):
            bad.append(f"{sid}: range is not the setting item's own range")
        if t["first_rack_weeks"] > t["weeks"]:
            bad.append(f"{sid}: first rack after full capacity")
        if t["date_basis"] == "assumed" and "not a date" not in str(t["date_label"]):
            bad.append(f"{sid}: assumed date not labelled as one")
        if (
            any(x["lead_time_weeks"]["evidence"] == "E0_ASSUMPTION" for x in items)
            and t["date_basis"] != "assumed"
        ):
            bad.append(f"{sid}: date built on E0 lead times presented as {t['date_basis']!r}")
    _check(
        res,
        "compare.dates",
        "every date is its critical path and is labelled for what it rests on",
        bad,
    )

    bad = []
    for sid, s in scen.items():
        e = s["economics"]
        if s["after_ladder"]["racks"] == 0 and e["capex_total_eur"] is not None:
            bad.append(f"{sid}: capex stated for a scenario with nothing deployable")
        if e["capex_total_eur"] is not None:
            c = e["capex_total_eur"]
            if not c["low"] <= c["value"] <= c["high"]:
                bad.append(f"{sid}: capex value outside its own band")
            if not e["capex_complete"] and (e["decision_grade"] or not e["unpriced_components"]):
                bad.append(
                    f"{sid}: incomplete capex presented as decision-grade or with no unpriced list"
                )
    _check(
        res, "compare.economics", "capex is banded, and incomplete capex says what is unpriced", bad
    )

    bad = claim_violations(out, "indicative")
    if not out.get("caveats"):
        bad.append("no caveats although dates rest on assumed lead times")
    _check(res, "compare.no_overclaim", "grade and dates do not exceed indicative evidence", bad)
    return res


def _strip_context(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v for k, v in payload.items() if k not in ("grid_queue_context", "caveats", "provenance")
    }


def evaluate_queue_context(
    intake: dict[str, Any],
    compare: dict[str, Any],
    api: Callable[..., dict[str, Any]],
    report_date: str,
) -> list[Check]:
    """Judge the product's `grid_queue_context` against THIS harness's own measurement of the same statistic.

    The product ships a copy of LBNL's request-to-operation durations beside its refusal to date grid reinforcement. This does not trust that
    copy: the expected values are read from the harness fixture (computed in CI from the published workbook), never from the product. A
    product that does not carry the block yet is not wrong, so nothing is judged then; once it does, every figure must match, the limits must
    be stated, and asking for a region must change nothing but the block.
    """
    ctx = compare.get("grid_queue_context")
    if ctx is None:
        return []
    from .context_bridge import WEEKS_PER_YEAR, _survivorship, load_queue_context, site_country

    doc = load_queue_context()
    res: list[Check] = []

    def figures(observed: dict[str, Any], expected: dict[str, Any], label: str) -> list[str]:
        bad = []
        for have, want in (
            ("n", "n"),
            ("p25_years", "p25"),
            ("median_years", "median"),
            ("p75_years", "p75"),
        ):
            if observed.get(have) != expected[want]:
                bad.append(
                    f"{label}: {have} is {observed.get(have)!r}, the independent measurement says {expected[want]!r}"
                )
        for have, want in (("p25_weeks", "p25"), ("median_weeks", "median"), ("p75_weeks", "p75")):
            w = observed.get(have)
            if not isinstance(w, (int, float)) or abs(w - expected[want] * WEEKS_PER_YEAR) > 0.06:
                bad.append(f"{label}: {have} {w!r} is not {expected[want]} years in weeks")
        return bad

    bad = figures(ctx.get("observed", {}), doc["contexts"]["cod_2018_2025"], "pooled")
    want = _survivorship(doc)
    have = ctx.get("survivorship") or {}
    if want is None:
        bad.append("the harness fixture has no survivorship to compare with")
    else:
        for k in ("entered", "operational", "withdrawn", "share_operational_of_entered"):
            if have.get(k) != want[k]:
                bad.append(
                    f"survivorship {k} is {have.get(k)!r}, the independent measurement says {want[k]!r}"
                )
        if have.get("unresolved") != want["active"] + want["suspended"] + want["unknown"]:
            bad.append("survivorship unresolved does not equal active + suspended + unknown")
    if ctx.get("source", {}).get("workbook_sha256") != doc["provenance"]["workbook_sha256"]:
        bad.append("the source workbook hash is not the one the harness measured")
    _check(
        res,
        "context.matches_independent_measurement",
        "the product's queue statistic equals the harness's own measurement of the same workbook",
        bad,
    )

    text = " ".join(ctx.get("statements", []))
    country = site_country(intake)
    expected_level = (
        "same_country_context" if country == doc["geography"] else "scale_reference_only"
    )
    bad = []
    if ctx.get("kind") != "context_only":
        bad.append(f"kind is {ctx.get('kind')!r}, not context_only")
    for phrase in ("not a connection time for a load", "changes no figure and no evidence class"):
        if phrase not in text:
            bad.append(f"the statements no longer say: {phrase}")
    if len(ctx.get("must_not_claim", [])) < 4 or not any(
        "evidence class" in m for m in ctx.get("must_not_claim", [])
    ):
        bad.append("the claims it refuses to make are missing or incomplete")
    if not any("Survivorship" in x for x in ctx.get("limits", [])):
        bad.append("the survivorship limit is not stated")
    if ctx.get("applicability", {}).get("level") != expected_level:
        bad.append(
            f"applicability is {ctx.get('applicability', {}).get('level')!r} for country {country!r}; expected {expected_level}"
        )
    if not any("Context only" in c for c in compare.get("caveats", [])):
        bad.append("the comparison's caveats do not label it context only")
    _check(
        res,
        "context.limits_are_stated",
        "it says what it is, what it is not, and whom it applies to",
        bad,
    )

    region = "PJM"
    regional = api(
        "compare", {"intake": intake, "grid_queue_region": region}, report_date=report_date
    )
    bad = []
    if not regional.get("ok"):
        bad.append(f"a valid region was refused: {regional.get('message')}")
    else:
        out = regional["result"]
        exp = doc["by_region_cod_2018_2025"][region]
        bad += figures(out.get("grid_queue_context", {}).get("observed", {}), exp, region)
        if _strip_context(out) != _strip_context(compare):
            bad.append("asking for a region changed something other than the context block")
    _check(
        res,
        "context.region_moves_nothing_else",
        "a region request changes the context block and nothing else in the comparison",
        bad,
    )
    return res


def _best_racks(c: dict[str, Any]) -> int:
    """Most after-ladder racks any scenario reaches: the recommended scenario's figure, also when the top is tied."""
    return max((s["after_ladder"]["racks"] for s in c["scenarios"]), default=0)


def _first_transformer(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["transformers"][0]  # type: ignore[no-any-return]


def evaluate_whatifs(
    intake: dict[str, Any],
    compare: dict[str, Any],
    api: Callable[..., dict[str, Any]] = call,
    report_date: str = "2026-10-27",
) -> list[Check]:
    res: list[Check] = []

    def cmp(doc: dict[str, Any]) -> dict[str, Any]:
        o = api("compare", {"intake": doc}, report_date=report_date)
        if not o.get("ok"):
            raise ProductUnavailable(f"compare refused a what-if: {o.get('message')}")
        r: dict[str, Any] = o["result"]
        return r

    def diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        o = api("diff", {"before": a, "after": b}, report_date=report_date)
        if not o.get("ok"):
            raise ProductUnavailable(f"diff refused: {o.get('message')}")
        r: dict[str, Any] = o["result"]
        return r

    same = diff(intake, intake)
    p = (
        []
        if not same["material"] and same["racks_delta"] == 0 and not same["changed_inputs"]
        else ["no-change diff"]
    )
    _check(res, "whatif.identity", "an unchanged intake diffs to nothing", p)

    relieved = copy.deepcopy(intake)
    _first_transformer(relieved)["units"] += 1
    c2 = cmp(relieved)
    d = diff(intake, relieved)
    b, a = _best_racks(compare), _best_racks(c2)
    p = []
    if a < b:
        p.append(f"more transformer capacity reduced racks {b} -> {a}")
    if (d["before"]["racks_after_ladder"], d["after"]["racks_after_ladder"]) != (b, a):
        p.append("diff and compare disagree on the recommended scenario's racks")
    if d["racks_delta"] != a - b:
        p.append(f"diff racks_delta {d['racks_delta']} != compare difference {a - b}")
    if not any("transformer" in str(x) for x in d["changed_inputs"]):
        p.append("diff does not name the transformer input it was given")
    _check(
        res,
        "whatif.relief",
        "relieving a constraint never lowers capacity and diff agrees with compare",
        p,
    )

    starved = copy.deepcopy(intake)
    starved["grid"]["firm_capacity_MVA"] = _num(intake["grid"]["firm_capacity_MVA"]) / 2
    starved["grid"]["contracted_MW"] = _num(intake["grid"]["contracted_MW"]) / 2
    c3 = cmp(starved)
    nb, na = _best_racks(compare), _best_racks(c3)
    p = [f"halving the connection raised racks {nb} -> {na}"] if na > nb else []
    p += [
        f"halved-connection result violates {c.id}"
        for c in evaluate_compare(starved, c3)
        if c.outcome == FAIL
    ]
    _check(
        res,
        "whatif.starved",
        "halving the grid connection never raises capacity and every bound still holds",
        p,
    )

    shuffled = {k: intake[k] for k in reversed(list(intake))}
    c4 = cmp(shuffled)
    p = (
        []
        if c4["request_digest"] == compare["request_digest"]
        else ["request digest depends on key order"]
    )
    if c4["ranking"] != compare["ranking"]:
        p.append("ranking depends on key order")
    changed = cmp(relieved)["request_digest"]
    if changed == compare["request_digest"]:
        p.append("request digest did not change with the input")
    _check(
        res,
        "whatif.determinism",
        "the same intake gives the same digest and answer; a changed one does not",
        p,
    )

    gap = copy.deepcopy(intake)
    del _first_transformer(gap)["replacement_lead_time_weeks"]
    c5 = cmp(gap)
    relies = [
        s["id"]
        for s in c5["scenarios"]
        if s["time_to_power"]["status"] == "dated"
        and any(x["name"] == "Transformer capacity" for x in s["time_to_power"]["lead_times"])
    ]
    named = any(
        "lead time" in c and "transformer" in c.lower() and "not supplied" in c
        for c in c5["caveats"]
    )
    _check(
        res,
        "whatif.missing_lead_time",
        "an omitted date-setting lead time is never silent: no date, or the compare says it was not supplied",
        [
            f"{', '.join(relies)} dated on a lead time the intake never supplied, with no caveat saying so"
        ]
        if relies and not named
        else [],
    )
    return res


def run(
    intake: dict[str, Any],
    *,
    report_date: str = "2026-10-27",
    api: Callable[..., dict[str, Any]] = call,
) -> Record:
    rec = Record(
        intake_sha256=hashlib.sha256(json.dumps(intake, sort_keys=True).encode()).hexdigest(),
        report_date=report_date,
    )
    try:
        first = api("compare", {"intake": intake}, report_date=report_date)
        if not first.get("ok"):
            rec.checks.append(
                Check(
                    "compare.accepts_intake",
                    FAIL,
                    "the product accepts the intake",
                    str(first.get("message")),
                )
            )
            return rec
        compare = first["result"]
        rec.product = {
            k: compare.get("provenance", {}).get(k)
            for k in ("engine_version", "code_sha256", "cost_library_sha256")
        }
        rec.checks += evaluate_compare(intake, compare)
        screen = api("screen", {"intake": intake}, report_date=report_date)
        sc = screen.get("result", {}) if screen.get("ok") else {}
        top = max((x["after_ladder"]["racks"] for x in compare["scenarios"]), default=0)
        tied = [
            x["id"] for x in compare["scenarios"] if top > 0 and x["after_ladder"]["racks"] == top
        ]
        pick = sc.get("recommended")
        _check(
            rec.checks,
            "screen.agrees_with_compare",
            "screen recommends a scenario compare ranks first (any of a tie; unconstrained when nothing deploys)",
            []
            if not tied or pick in tied
            else [f"screen {pick!r} is not among compare's top {tied}"],
        )
        rec.checks += evaluate_whatifs(intake, compare, api, report_date)
        rec.checks += evaluate_queue_context(intake, compare, api, report_date)
    except ProductUnavailable as exc:
        rec.checks.append(
            Check(
                "product.available", BLOCKED, "the real product handlers could be called", str(exc)
            )
        )
    except (KeyError, TypeError, StopIteration, ValueError) as exc:
        rec.checks.append(
            Check(
                "payload.shape",
                FAIL,
                "the product's payload has the documented shape",
                f"{type(exc).__name__}: {exc}",
            )
        )
    return rec
