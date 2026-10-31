"""Every quantity in every response lies inside its own stated range (gridforge_stress.invariants).

Ordinary variants must have zero violations. A supplied transformer lead time outside the placeholder band 80-210 weeks still breaks the
invariant in many places at once (GridForge-AI#107): that is recorded as a strict xfail, so the fix flips it and the marker must then go."""

from __future__ import annotations

import pytest

from gridforge_stress.invariants import quantities, range_violations

from ._product_helpers import mutated, require_product, run

pytestmark = [pytest.mark.integration, pytest.mark.product]

CLEAN = {
    "reference": lambda d: None,
    "tx_N": lambda d: d["transformers"][0].update(redundancy="N"),
    "contracted_10": lambda d: d["grid"].update(contracted_MW=10.0),
    "peak_8": lambda d: d["grid"].update(current_site_peak_MW=8.0),
    "peak_above_ceiling": lambda d: d["grid"].update(current_site_peak_MW=13.0),
    "ups_2N": lambda d: d["ups"][0].update(redundancy="2N"),
    "tx_lead_missing": lambda d: d["transformers"][0].pop("replacement_lead_time_weeks"),
    "tx_lead_160_equals_placeholder": lambda d: d["transformers"][0].update(
        replacement_lead_time_weeks=160
    ),
    "tx_lead_200_inside_band": lambda d: d["transformers"][0].update(
        replacement_lead_time_weeks=200
    ),
}
BAND = "GridForge-AI#107: a supplied transformer lead time keeps the placeholder band 80-210"
OUTSIDE = [
    pytest.param(36, marks=pytest.mark.xfail(strict=True, reason=BAND)),
    pytest.param(300, marks=pytest.mark.xfail(strict=True, reason=BAND)),
]
ROUTES = ("/v1/screen", "/v1/study", "/v1/compare")


@pytest.fixture(scope="module", autouse=True)
def _need_product():
    require_product()


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_every_quantity_lies_inside_its_own_range(name):
    intake = mutated(CLEAN[name])
    outs = {route: run(route, {"intake": intake}) for route in ROUTES}
    scanned = sum(1 for out in outs.values() for _ in quantities(out))
    # compare legitimately prints few quantities when nothing is deployable, so the guard counts across routes
    assert scanned > 100, f"only {scanned} quantities found: the scan would prove nothing"
    problems = [f"{route}: {p}" for route, out in outs.items() for p in range_violations(out)]
    assert not problems, "\n".join(problems[:8])


@pytest.mark.parametrize("weeks", OUTSIDE)
def test_a_supplied_lead_time_outside_the_placeholder_band_keeps_every_range_coherent(weeks):
    intake = mutated(lambda d: d["transformers"][0].update(replacement_lead_time_weeks=weeks))
    problems = [p for route in ROUTES for p in range_violations(run(route, {"intake": intake}))]
    assert not problems, f"{len(problems)} incoherent quantities, e.g.\n" + "\n".join(problems[:6])
