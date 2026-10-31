"""Hostile-input fuzz of every route of the REAL product (opt in with `pytest -m product`).

A hostile request must be refused (4xx) or answered, never end in an exception the HTTP layer would call a server fault (a 500 "engine error"
with a printed traceback), and never take seconds. The base documents are the harness's SYNTHETIC fixture. See gridforge_stress/fuzz.py for
what this can and cannot see (not the HTTP dispatcher; only the fixed catalogue of hostile values).
"""

from __future__ import annotations

import pytest

from gridforge_stress.fuzz import default_plans, format_report, reference_intake, run_fuzz
from gridforge_stress.product_adapter import ProductUnavailable, product_dir, product_required

pytestmark = [pytest.mark.integration, pytest.mark.product]

#: Per-route sample of intake paths. The loader is always fuzzed on every field; this keeps the whole run to a few minutes.
SAMPLE = 3


@pytest.fixture(scope="module")
def report():
    try:
        product_dir()
    except ProductUnavailable as exc:
        (pytest.fail if product_required() else pytest.skip)(str(exc))
    return run_fuzz(default_plans(reference_intake(), sample_intake_paths=SAMPLE), timeout_s=1800)


def test_no_hostile_request_escapes_a_handler_as_a_server_fault(report):
    assert not report.non_clean, "\n".join(format_report(report))


def test_no_hostile_request_is_slow(report):
    assert not report.slow, "\n".join(format_report(report))


def test_every_route_was_really_driven(report):
    plans = default_plans(reference_intake(), sample_intake_paths=SAMPLE)
    assert set(report.per_plan) == {p["name"] for p in plans}
    assert all(n > 0 for n in report.per_plan.values()), report.per_plan
    assert (
        report.calls >= 5000
    )  # the run really happened: a trivially small run would prove nothing
    assert report.ok > 0 and report.clean > 0  # some inputs were answered and many were refused
