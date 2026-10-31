"""The paid deliverables (deck, proposal, specification, study) must agree with the screen computed from the same intake.

Each variant changes the intake, so a document with hard-coded or stale numbers cannot keep up. Expected values come from /v1/screen's
structured result (see gridforge_stress.consistency), never from the document under test."""

from __future__ import annotations

import pytest

from gridforge_stress.consistency import check_deck, check_identity, check_study_against_screen

from ._product_helpers import mutated, require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]

VARIANTS = {
    "reference": lambda d: None,
    "tx_N": lambda d: d["transformers"][0].update(redundancy="N"),
    "contracted_10": lambda d: d["grid"].update(contracted_MW=10.0),
    "ups_N": lambda d: d["ups"].__getitem__(0).update(redundancy="N"),
    "tx_lead_200": lambda d: d["transformers"][0].update(replacement_lead_time_weeks=200),
    "peak_8": lambda d: d["grid"].update(current_site_peak_MW=8.0),
    "firm_mva_11": lambda d: d["grid"].update(firm_capacity_MVA=11.0),
    "tx_lead_missing": lambda d: d["transformers"][0].pop("replacement_lead_time_weeks"),
}


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


def documents(intake):
    body = {"intake": intake}
    return {
        "screen": run("/v1/screen", body),
        "study": run("/v1/study", body),
        "deck": run("/v1/deck", body),
        "proposal": run(
            "/v1/proposal",
            {**body, "engagement": "density_screen", "contact": "a@example.test", "format": "html"},
        ),
        "spec": run("/v1/spec", {**body, "scenario": "full_dlc_btm"}),
    }


@pytest.fixture(scope="module")
def sets():
    return {name: (intake := mutated(edit), documents(intake)) for name, edit in VARIANTS.items()}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_the_deck_states_what_the_screen_computed(sets, name):
    _, docs = sets[name]
    problems = check_deck(docs["screen"], docs["deck"]["document"])
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_the_study_equals_the_screen(sets, name):
    _, docs = sets[name]
    problems = check_study_against_screen(docs["screen"], docs["study"])
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_document_names_the_client_and_is_labelled_synthetic(sets, name):
    intake, docs = sets[name]
    texts = {k: docs[k]["document"] for k in ("deck", "proposal", "spec")}
    problems = check_identity(intake["project"], True, texts)
    assert not problems, "\n".join(problems)


def test_the_variants_really_change_what_the_documents_say(sets):
    """Guards against a vacuous comparison: the recommended scenario's printed racks or weeks must differ across variants."""
    seen = set()
    for _, docs in sets.values():
        s = next(x for x in docs["screen"]["scenarios"] if x["id"] == docs["screen"]["recommended"])
        seen.add((s["after_ladder"]["racks"], s["time_to_power"].get("weeks_to_full")))
    assert len(seen) >= 4, seen
