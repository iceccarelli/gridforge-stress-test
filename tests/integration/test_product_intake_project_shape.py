"""Regression for the 29 fuzz server faults on `/v1/deck`: a malformed `intake.project` must be refused, never raise.

Baseline finding (harness db18c64, product 8bbcdf7): `intake.project` of None, bool, str, int, float (NaN/inf too) or list reached
`Intake.client` -> `self.project.get(...)` in gridforge/intake/loader.py and escaped as AttributeError (a 500 "engine error").

Independent acceptance: for every route that takes an intake, a non-object `project` is refused cleanly (4xx class) or answered; no case may
end as a server fault. Valid projects must still be answered. This test is NOT marked xfail: it fails against a product with the defect.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from gridforge_stress.fuzz import default_plans, reference_intake
from gridforge_stress.product_adapter import (
    ProductUnavailable,
    call_batch,
    product_dir,
    product_required,
)

pytestmark = [pytest.mark.integration, pytest.mark.product]

MALFORMED_PROJECTS: list[tuple[str, Any]] = [
    ("null", None),
    ("true", True),
    ("false", False),
    ("empty_str", ""),
    ("str", "Acme DC"),
    ("numeric_str", "1e999"),
    ("int_pos", 7),
    ("int_neg", -7),
    ("int_zero", 0),
    ("int_huge", 10**400),
    ("int_2_63", 2**63),
    ("float", 1.5),
    ("float_neg_zero", -0.0),
    ("float_huge", 1e308),
    ("nan", float("nan")),
    ("inf", float("inf")),
    ("neg_inf", float("-inf")),
    ("empty_list", []),
    ("list_of_obj", [{"client": "x"}]),
    ("nested_list", [[]]),
    ("deep_nested_list", [[[[[[1]]]]]]),
    ("list_with_null", [None]),
    ("obj_client_null", {"client": None}),
    ("obj_client_int", {"client": 5}),
    ("obj_client_list", {"client": ["a"]}),
    ("obj_client_obj", {"client": {"name": "x"}}),
    ("obj_reference_obj", {"reference": {"a": 1}}),
    ("obj_synthetic_str", {"synthetic": "yes"}),
    ("obj_synthetic_list", {"synthetic": [True]}),
    ("obj_nested_junk", {"client": {"a": [None, {"b": float("nan")}]}, "note": [1, [2]]}),
]

VALID_PROJECTS: list[tuple[str, Any]] = [
    ("reference", None),  # filled from the reference intake
    ("empty_object", {}),
    ("extra_keys", {"client": "Acme", "reference": "R-1", "synthetic": True, "unknown": 1}),
]


def _routes_with_intake() -> list[tuple[str, str, str, dict[str, Any], str]]:
    """(plan name, route, tier, base body, key holding the intake) for every plan whose body carries a top-level intake document."""
    found = []
    for p in default_plans(reference_intake(), sample_intake_paths=1):
        for key in p["intake_keys"]:
            if isinstance(p["base"].get(key), dict) and "project" in p["base"][key]:
                found.append((p["name"], p["route"], p["tier"], p["base"], key))
    return found


ROUTES = _routes_with_intake()


@pytest.fixture(scope="module")
def outcomes():
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))
    cases = []
    for name, route, tier, base, key in ROUTES:
        for label, project in MALFORMED_PROJECTS + VALID_PROJECTS:
            if label == "reference":
                project = copy.deepcopy(base[key]["project"])
            body = copy.deepcopy(base)
            body[key]["project"] = copy.deepcopy(project)
            cases.append({"id": f"{name}|{label}", "route": route, "tier": tier, "body": body})
    return call_batch(cases)


def test_the_regression_drives_the_route_that_failed():
    assert "deck" in {r[0] for r in ROUTES}, "deck (the route that escaped) must be exercised"
    assert len(ROUTES) >= 5, [r[0] for r in ROUTES]


def test_no_malformed_project_escapes_as_a_server_fault(outcomes):
    faults = {
        k: f"{v['error']}: {v['message']}"
        for k, v in outcomes.items()
        if v["status"] == "fault" and k.split("|", 1)[1] in {lbl for lbl, _ in MALFORMED_PROJECTS}
    }
    assert not faults, f"{len(faults)} server faults, e.g.\n" + "\n".join(
        f"  {k} -> {v}" for k, v in sorted(faults.items())[:12]
    )


#: Routes whose fuzz base body is refused for a reason unrelated to `project` (bids: the base supplier response carries compliance code "YES").
BASE_BODY_REFUSED_FOR_OTHER_REASONS = {"bids"}


def test_a_valid_project_is_still_answered(outcomes):
    """Guards against 'fixing' the defect by refusing everything: the unmodified reference project must be answered on every route."""
    refused = {
        k: v
        for k, v in outcomes.items()
        if k.endswith("|reference")
        and v["status"] != "ok"
        and k.split("|")[0] not in BASE_BODY_REFUSED_FOR_OTHER_REASONS
    }
    assert not refused, refused
    answered = {
        k.split("|")[0]
        for k, v in outcomes.items()
        if k.endswith("|reference") and v["status"] == "ok"
    }
    assert {"deck", "screen", "compare", "spec", "proposal"} <= answered


def test_valid_project_variants_never_fault(outcomes):
    faults = {
        k: v
        for k, v in outcomes.items()
        if k.split("|", 1)[1] in {"empty_object", "extra_keys"} and v["status"] == "fault"
    }
    assert not faults, faults
