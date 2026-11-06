"""P2-3: a declared evidence class is exactly E0..E7, never a prefix of something else. Opt in with `pytest -m product`.

Defect (verified on GridForge-AI b649b3e7): the intake loader reads `{"value": ..., "evidence": X}` with `str(X).upper()[:2]`
(gridforge/intake/loader.py `_annotation`; the diff path in gridforge/change.py does the same). Only the first two characters are looked at,
so 'E10', 'E1x', 'E1 (measured)', 'E39', 'E70' and 'e5foo' are all ACCEPTED, silently as E1, E1, E1, E3, E7 and E5, while 'E8' and 'E' are
refused with a message naming the field. 'E39' is read as an engineering estimate and 'E70' as the top of the scale, neither of which the
caller wrote; 'E10' is read as the lowest-but-one class when it is not a class at all.

Invariant: only an exact class name (E0..E7, case-insensitive) may be accepted; anything else is REFUSED with an IntakeError that names the
field and quotes the value, on /v1/compare and /v1/screen alike. The accept arm (E0..E7, 'e3') is satisfied on the base product and shows
the refusal assertions are not "refuse everything" in disguise. Companion of test_an_unknown_evidence_class_is_refused_by_field_name in
test_product_evidence_semantics.py, which pins 'E9' only. Not xfail'd.
"""

from __future__ import annotations

from typing import Any

import pytest

from gridforge_stress.product_adapter import call

from ._common import mutated

pytestmark = [pytest.mark.integration, pytest.mark.product]

#: (field the class is declared on, setter)
FIELDS = {
    "residual_air_capacity_kW_per_rack": lambda d, v: d["thermal"][
        "residual_air_capacity_kW_per_rack"
    ].__setitem__("evidence", v),
    "busway_ampacity_A": lambda d, v: d["lv"]["busway_ampacity_A"].__setitem__("evidence", v),
}
NOT_CLASSES = ["E10", "E1x", "E1 (measured)", "E39", "E70", "e5foo", "E8", "E", "E7x", "E1.5"]
CLASSES = ["E0", "E1", "E2", "E3", "E4", "E5", "E6", "E7", "e3"]


def _declare(intake: dict[str, Any], field: str, value: str) -> dict[str, Any]:
    return mutated(intake, lambda d: FIELDS[field](d, value))


@pytest.mark.parametrize("route", ["compare", "screen"])
@pytest.mark.parametrize("field", list(FIELDS))
@pytest.mark.parametrize("bad", NOT_CLASSES)
def test_a_string_that_is_not_an_evidence_class_is_refused_by_field_name(intake, route, field, bad):
    out = call(route, {"intake": _declare(intake, field, bad)})
    assert out["ok"] is False, (
        f"/v1/{route}: evidence {bad!r} on {field} was ACCEPTED (it is not one of E0..E7)"
    )
    assert out["error"] == "IntakeError", out
    assert field in out["message"], f"the refusal does not name {field}: {out['message']}"
    assert repr(bad) in out["message"], f"the refusal does not quote {bad!r}: {out['message']}"


@pytest.mark.parametrize("field", list(FIELDS))
@pytest.mark.parametrize("good", CLASSES)
def test_every_real_class_is_still_accepted(intake, field, good):
    """Control arm (satisfied on the base product): the exact names E0..E7, in either case, are not refused."""
    out = call("compare", {"intake": _declare(intake, field, good)})
    assert out["ok"], out
