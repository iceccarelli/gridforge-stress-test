"""GET /v1/version `capabilities.qualify_assumed_fields` is true if and only if POST /v1/qualify really honours `assumed_fields`. Opt in with `pytest -m product`.

CAPABILITY-HONESTY oracle. The product publishes behaviour its route list does not show as named boolean flags, and the website trusts them
(lib/engine-capabilities.ts: "a flag counts only when its value is exactly true"). A flag is a claim, so it is checked against the behaviour it
names, over the product's real HTTP handler:
  * PROBE: the tap-off rating is sent plain and then declared assumed; the route HONOURS the parameter when BOTH observable effects occur, the
    figure is recorded in `inputs_declared_assumed` as E0 AND some stated class is strictly lower than in the plain answer. One without the other
    is a half-implemented capability, which no flag may call whole; neither is "not honoured" (ignored, or refused).
  * INVARIANT: flag exactly true  <=>  honoured. A flag true while the route ignores or refuses the key, a route that honours it with no flag (or
    a false one), a half-implementation under any flag, and a flag that is not a boolean, are each a failure.
Flags other than this one have no probe here and are not judged. Judge: `assumed_fields.honouring` + `capability_violations`; tests/unit/
test_assumed_fields.py proves each failure mode. Not xfail'd.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gridforge_stress.assumed_fields import capability_violations, honouring
from gridforge_stress.product_adapter import http_batch

from ._common import HALL

pytestmark = [pytest.mark.integration, pytest.mark.product]


@pytest.fixture(scope="module")
def observed() -> dict[str, Any]:
    rows = http_batch(
        [
            {"id": "version", "method": "GET", "path": "/v1/version"},
            {"id": "plain", "method": "POST", "path": "/v1/qualify", "json": dict(HALL)},
            {
                "id": "marked",
                "method": "POST",
                "path": "/v1/qualify",
                "json": {**HALL, "assumed_fields": ["tapoff_max_A"]},
            },
        ]
    )
    assert rows["version"]["status"] == 200, rows["version"]
    assert rows["plain"]["status"] == 200, rows["plain"]
    return {
        "version": json.loads(rows["version"]["body"]),
        "plain": json.loads(rows["plain"]["body"]),
        "marked": json.loads(rows["marked"]["body"]) if rows["marked"]["status"] == 200 else None,
        "marked_status": rows["marked"]["status"],
    }


def test_the_flag_is_true_if_and_only_if_the_route_honours_the_parameter(observed):
    honours = honouring(observed["plain"], observed["marked"])
    problems = capability_violations(observed["version"], honours)
    assert not problems, (
        f"{problems} (probe: HTTP {observed['marked_status']}, honouring={honours})"
    )


def test_the_product_supports_the_round3_baseline(observed):
    """Honest absence is not enough: the round-3 suite needs a product that implements assumed_fields."""
    caps = observed["version"].get("capabilities") or {}
    assert caps.get("qualify_assumed_fields") is True, (
        "The round-3 suite requires assumed_fields support; check .product-pin or GRIDFORGE_PRODUCT_DIR"
    )
    assert honouring(observed["plain"], observed["marked"]) == "full"


def test_the_probe_sees_the_capability_as_whole_not_partial(observed):
    """The probe's own verdict on the shipped route, stated apart from the flag: if this is not 'full' or 'none' the flag test cannot pass."""
    assert honouring(observed["plain"], observed["marked"]) in ("full", "none")


def test_the_version_body_declares_its_capabilities_as_booleans(observed):
    """An engine older than the key has no `capabilities` and, honestly, no capability; one that has the object keeps its flags boolean."""
    caps = observed["version"].get("capabilities")
    if caps is None:
        assert honouring(observed["plain"], observed["marked"]) == "none"
        return
    assert isinstance(caps, dict), caps
    assert all(isinstance(v, bool) for v in caps.values()), caps
