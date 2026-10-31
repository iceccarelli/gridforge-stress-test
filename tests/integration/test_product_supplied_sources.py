"""Buyer-supplied figures with a source or note (company PR #109): the product must not let that text change a number, raise a claim, or inject markup.

Rules tested come from the product's own stated policy: a client may attest to their own data but never to validation (E6/E7 are earned only by
the calibration ledger, here empty), and the source/note are carried as text.

Known limit, NOT asserted: the Markdown report (`format: md`) carries buyer text verbatim, raw HTML included, where the HTML report escapes it. Markdown
allows raw HTML, and the company's deliverable route serves .md as a file attachment rather than rendering it, so this is a risk only for a downstream
viewer that renders raw HTML. It is recorded here as UNCHECKED, not filed as a defect."""

from __future__ import annotations

import re

import pytest

from ._product_helpers import mutated, require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]

PEAK = ("grid", "current_site_peak_MW")


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


def with_peak(**fields):
    return mutated(
        lambda d: d["grid"].__setitem__("current_site_peak_MW", {"value": 7.4, **fields})
    )


def outside_echo_and_warnings(node, path=""):
    """Strings of a response except the echoed input document and the warnings list (where an E6/E7 declaration is legitimately quoted)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if (path == ".intake" and k == "document") or k == "warnings":
                continue
            yield from outside_echo_and_warnings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from outside_echo_and_warnings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield node


@pytest.mark.parametrize("declared", ["E6", "E7", "e7", "E7 validated", "E6_CALIBRATED"])
def test_a_client_cannot_declare_validation_level_evidence(declared):
    """With 0 calibration observations no quantity may be E6/E7, however the intake spells it; the buyer is told it was capped."""
    out = run("/v1/screen", {"intake": with_peak(evidence=declared)})
    assert "0 reconciled observations" in out["calibration"]["statement"], (
        "premise: the calibration ledger is empty"
    )
    leaks = [s[:100] for s in outside_echo_and_warnings(out) if re.search(r"\bE[67]\b", s)]
    assert not leaks, leaks
    assert any("capped at E5" in w for w in out["intake"]["warnings"]), out["intake"]["warnings"]


def test_the_hostile_declarations_are_still_refused_when_not_an_evidence_class():
    from gridforge_stress.product_adapter import call_batch

    r = call_batch(
        [
            {
                "id": "x",
                "route": "/v1/screen",
                "tier": "client",
                "body": {"intake": with_peak(evidence="validated")},
            }
        ]
    )["x"]
    assert r["status"] == "clean", (
        r
    )  # refused with a message, not a server fault, and not silently accepted


def headline(out):
    """Everything numeric a decision rests on: racks, schedule and constraint limits per scenario, and the recommendation."""
    return {
        "recommended": out["recommended"],
        "scenarios": {
            s["id"]: (
                s["as_found"]["racks"],
                s["after_ladder"]["racks"],
                s["time_to_power"].get("weeks_to_full"),
                {c["id"]: c["racks_permitted"] for c in s["constraints"]},
            )
            for s in out["scenarios"]
        },
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"source": "Utility letter 2026-03"},
        {"note": "12 months of metered data"},
        {"source": "s", "note": "n"},
        {"source": "x" * 5000},
    ],
)
def test_attaching_a_source_or_note_changes_no_number(fields):
    plain = run("/v1/screen", {"intake": with_peak()})
    sourced = run("/v1/screen", {"intake": with_peak(**fields)})
    assert headline(sourced) == headline(plain)


PAYLOADS = {
    "img": "<img src=x onerror=alert(1)>",
    "script": "<script>alert(2)</script>",
    "attr": '"><svg onload=alert(3)>',
    "amp": "a&b<c>d",
}


def test_the_html_report_renders_buyer_text_inert():
    intake = mutated(
        lambda d: d["grid"].update(
            current_site_peak_MW={
                "value": 7.4,
                "note": PAYLOADS["img"],
                "source": PAYLOADS["script"],
            },
            current_it_load_MW={"value": 4.9, "note": PAYLOADS["attr"], "source": PAYLOADS["amp"]},
        )
    )
    doc = run("/v1/screen", {"intake": intake, "format": "html"})["document"]
    assert "supplied with a source" in doc.lower(), (
        "premise: the table that carries buyer text was rendered"
    )
    for name, payload in PAYLOADS.items():
        assert payload not in doc, f"{name}: buyer markup reached the HTML unescaped"
    assert "&lt;img" in doc and "&lt;script" in doc
    assert not re.search(r"<script", doc, re.I)
    assert not re.search(r"""<[a-z][^>]*\son[a-z]+\s*=""", doc, re.I), (
        "an event-handler attribute appeared in a tag"
    )
    assert not re.search(r"""(href|src|action)\s*=\s*["']?\s*javascript:""", doc, re.I)


def test_the_supplied_figures_table_never_shows_validation_level_evidence():
    out = run("/v1/screen", {"intake": with_peak(source="doc", evidence="E7"), "format": "md"})[
        "document"
    ]
    rows = [
        ln for ln in out.splitlines() if ln.startswith("|") and "current site peak" in ln.lower()
    ]
    assert rows, "premise: the figure appears in the supplied-with-a-source table"
    assert all(not re.search(r"\|\s*E[67]\s*\|", r) for r in rows), rows


# --- which fields keep a buyer's source? (47 numeric leaves of the reference intake, measured on company d54e893) ---------------------------------
DROPPED = (
    "hall.build_year",
    "hall.rack_positions",
    "hall.positions_available",
    "lv.phases",
    "lv.busway_runs",
    "economics.horizon_years",
)
#: The fields the company's buyer-confirmation flow (#109 CONFIRMABLE) can target. They are in SHOWN below, so they are asserted to keep their source.
CONFIRMABLE = (
    "grid.firm_capacity_MVA",
    "hall.floor_loading_kPa",
    "thermal.plant.chilled_water_capacity_kW",
)
SHOWN = (
    "grid.current_site_peak_MW",
    "grid.contracted_MW",
    "grid.power_factor",
    "grid.current_it_load_MW",
    *CONFIRMABLE,
)
OPEN_110 = pytest.mark.xfail(
    strict=True,
    reason="GridForge-AI#110: annotated value accepted, source and note silently dropped",
)


def annotate(dotted, **fields):
    keys = dotted.split(".")

    def edit(d):
        cur = d
        for k in keys[:-1]:
            cur = cur[k]
        cur[keys[-1]] = {
            "value": cur[keys[-1]]["value"] if isinstance(cur[keys[-1]], dict) else cur[keys[-1]],
            **fields,
        }

    return mutated(edit)


@pytest.mark.parametrize(
    "path", [pytest.param(p, marks=OPEN_110) if p in DROPPED else p for p in (*SHOWN, *DROPPED)]
)
def test_an_annotated_figure_is_either_shown_with_its_source_or_refused(path):
    from gridforge_stress.product_adapter import call_batch

    body = {"intake": annotate(path, source="SRC-MARK", note="NOTE-MARK"), "format": "md"}
    r = call_batch(
        [{"id": "x", "route": "/v1/screen", "tier": "client", "body": body, "want_result": True}]
    )["x"]
    if r["status"] == "clean":
        return  # refused with a message: an acceptable answer
    assert r["status"] == "ok", r
    doc = r["result"]["document"]
    assert "SRC-MARK" in doc and "NOTE-MARK" in doc, (
        f"{path}: accepted, but its source and note appear nowhere in the report"
    )


def test_list_field_values_are_refused_cleanly_not_crashed():
    """Transformer / UPS / BTM figures cannot carry an annotation (a limitation, not asserted as a defect); the refusal must be controlled."""
    from gridforge_stress.product_adapter import call_batch

    for path in ("transformers.0.units", "ups.0.unit_rating_kW", "btm_options.0.capacity_MW"):
        keys = path.split(".")

        def edit(d, keys=keys):
            cur = d
            for k in keys[:-1]:
                cur = cur[int(k)] if k.isdigit() else cur[k]
            last = int(keys[-1]) if keys[-1].isdigit() else keys[-1]
            cur[last] = {"value": cur[last], "source": "s"}

        r = call_batch(
            [
                {
                    "id": "x",
                    "route": "/v1/screen",
                    "tier": "client",
                    "body": {"intake": mutated(edit)},
                }
            ]
        )["x"]
        assert r["status"] in ("ok", "clean"), (path, r)
