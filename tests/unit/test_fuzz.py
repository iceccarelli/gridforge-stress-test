"""The fuzzer itself, against tiny stub products with defects planted on purpose.

A fuzzer that reports "healthy" is only worth something if it demonstrably reports "unhealthy" when a handler lets the wrong kind of
exception escape. Each stub below is a minimal stand-in for the product's import surface (`gridforge.api.server`, `gridforge.api.tiers`,
`gridforge.intake.loader`) with one behaviour: refuse everything cleanly, or fail in one specific way. No real product is involved.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from gridforge_stress import _fuzz_runner as runner
from gridforge_stress import fuzz
from gridforge_stress.product_adapter import ProductUnavailable

pytestmark = pytest.mark.unit

TIERS = """
import enum
class Tier(str, enum.Enum):
    CLIENT = "client"
    PUBLIC = "public"
"""

LOADER = """
class IntakeError(ValueError):
    pass
def load_document(doc):
    if not isinstance(doc, dict):
        raise IntakeError("an intake must be a JSON object")
    return doc
"""


def make_product(root: Path, handler_body: str) -> Path:
    """A stub product whose /v1/t handler runs `handler_body` (the body of `def handler(body, tier):`)."""
    files = {
        "gridforge/__init__.py": "",
        "gridforge/api/__init__.py": "",
        "gridforge/api/tiers.py": TIERS,
        "gridforge/intake/__init__.py": "",
        "gridforge/intake/loader.py": LOADER,
        "gridforge/api/server.py": "import copy, time\n\n"
        "class ApiError(Exception):\n    pass\n\n"
        f"def handler(body, tier):\n{textwrap.indent(textwrap.dedent(handler_body).strip(), '    ')}\n\n"
        'ROUTES = {"/v1/t": (handler, "client")}\n\n'
        "def _invoke(endpoint, handler, body, tier):\n    return handler(body, tier)\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def plan(base: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "name": "p",
        "route": "/v1/t",
        "tier": "client",
        "base": base,
        "intake_keys": [],
        "sample_intake_paths": 0,
        **extra,
    }


CLEAN = """
if not isinstance(body, dict) or not isinstance(body.get("x"), str):
    raise ApiError("x must be text")
return {"ok": True}
"""


def test_a_product_that_refuses_cleanly_is_healthy_and_every_hostile_value_is_tried_at_every_field(
    tmp_path,
):
    product = make_product(tmp_path, CLEAN)
    report = fuzz.run_fuzz([plan({"x": "a", "n": 1})], root=product)
    hostile = len(runner.hostile_values())
    assert report.calls == 2 * hostile  # two fields, each tried with every hostile value
    assert report.healthy and not report.non_clean and not report.slow
    assert report.ok + report.clean == report.calls
    assert (
        report.ok > 0 and report.clean > 0
    )  # the harmless values were answered, the hostile ones refused


def test_an_attribute_error_escaping_a_handler_is_reported_with_where_and_what(tmp_path):
    """The exact shape of a real finding: a field read with .get() / .upper() whatever its type."""
    product = make_product(tmp_path, 'return {"v": body["x"].upper()}')
    report = fuzz.run_fuzz([plan({"x": "a"})], root=product)
    assert not report.healthy
    found = [f for f in report.non_clean if f.exception == "AttributeError"]
    assert found, report
    assert found[0].plan == "p" and "server.py" in found[0].raised_in
    assert any(w.startswith("p x=") for w in found[0].where)  # which field and which hostile value


def test_a_recursion_error_from_a_deeply_nested_value_is_reported(tmp_path):
    product = make_product(tmp_path, "return {'copy': copy.deepcopy(body)}")
    report = fuzz.run_fuzz([plan({"x": "a"})], root=product)
    findings = [f for f in report.non_clean if f.exception == "RecursionError"]
    assert findings and any("deep" in w for f in findings for w in f.where)


def test_exceptions_that_the_http_layer_refuses_cleanly_are_not_findings(tmp_path):
    body = """
    if body["x"] is None:
        raise TypeError("t")
    if body["x"] == "":
        raise KeyError("k")
    if body["x"] == "x":
        raise ArithmeticError("a")
    if body["x"] == " ":
        raise ValueError("v")
    return {}
    """
    report = fuzz.run_fuzz([plan({"x": "a"})], root=make_product(tmp_path, body))
    assert report.healthy and report.clean >= 4


def test_a_slow_call_is_reported_separately_from_a_fault(tmp_path):
    product = make_product(tmp_path, "time.sleep(0.03)\nreturn {}")
    report = fuzz.run_fuzz([plan({"x": "a"})], root=product, slow_s=0.01)
    assert report.slow and not report.non_clean and not report.healthy
    assert all(s.seconds >= 0.01 and s.plan == "p" for s in report.slow)


def test_the_loader_route_is_driven_without_a_handler(tmp_path):
    product = make_product(tmp_path, CLEAN)
    loader_plan = {
        "name": "loader",
        "route": "loader",
        "tier": "client",
        "base": {"a": 1, "b": {"c": 2}},
        "intake_keys": [],
        "sample_intake_paths": 0,
    }
    report = fuzz.run_fuzz([loader_plan], root=product)
    assert report.calls == 3 * len(runner.hostile_values()) and report.healthy  # a, b and b.c


def test_a_whole_top_level_key_is_always_tried_but_inner_paths_are_sampled(tmp_path):
    product = make_product(tmp_path, CLEAN)
    base = {"intake": {"a": 1, "b": 2, "c": 3, "d": 4}}
    none_sampled = fuzz.run_fuzz(
        [plan(base, intake_keys=["intake"], sample_intake_paths=0)], root=product
    )
    two_sampled = fuzz.run_fuzz(
        [plan(base, intake_keys=["intake"], sample_intake_paths=2)], root=product
    )
    hostile = len(runner.hostile_values())
    assert none_sampled.calls == 1 * hostile  # only ("intake",) itself
    assert two_sampled.calls == 3 * hostile  # ("intake",) plus two sampled inner paths


def test_the_same_seed_reproduces_the_run_and_the_findings(tmp_path):
    product = make_product(tmp_path, 'return {"v": body["intake"]["a"].upper()}')
    base = {"intake": {"a": "x", "b": "y", "c": "z", "d": "w", "e": "v"}}
    p = plan(base, intake_keys=["intake"], sample_intake_paths=3)
    first = fuzz.run_fuzz([p], root=product, seed=11)
    second = fuzz.run_fuzz([p], root=product, seed=11)
    assert (first.calls, first.ok, first.clean) == (second.calls, second.ok, second.clean)
    assert [(f.exception, f.where) for f in first.non_clean] == [
        (f.exception, f.where) for f in second.non_clean
    ]


def test_findings_are_grouped_and_the_list_of_places_is_bounded(tmp_path):
    product = make_product(tmp_path, 'return {"v": body["x"].upper()}')
    report = fuzz.run_fuzz([plan({"x": "a"})], root=product)
    attr = [f for f in report.non_clean if f.exception == "AttributeError"]
    assert (
        sum(f.count for f in attr) > runner.MAX_WHERE_PER_FINDING
    )  # many hostile values hit it...
    assert all(
        len(f.where) <= runner.MAX_WHERE_PER_FINDING for f in attr
    )  # ...but the report stays readable


def test_a_runner_that_cannot_start_is_unavailable_not_a_finding(tmp_path):
    with pytest.raises(ProductUnavailable, match="fuzz runner failed"):
        fuzz.run_fuzz([plan({"x": "a"})], root=tmp_path)  # no gridforge package here


def test_the_report_names_the_verdict_and_every_finding(tmp_path):
    product = make_product(tmp_path, 'return {"v": body["x"].upper()}')
    text = "\n".join(fuzz.format_report(fuzz.run_fuzz([plan({"x": "a"})], root=product)))
    assert "NON-CLEAN [p]" in text and "AttributeError" in text and text.endswith("UNHEALTHY")
    healthy = "\n".join(
        fuzz.format_report(
            fuzz.run_fuzz([plan({"x": "a"})], root=make_product(tmp_path / "ok", CLEAN))
        )
    )
    assert healthy.endswith("HEALTHY") and "UNHEALTHY" not in healthy


def test_the_default_plans_cover_every_route_with_a_synthetic_base():
    intake = fuzz.reference_intake()
    assert "SYNTHETIC" in intake["project"]["note"]  # never real customer data
    plans = fuzz.default_plans(intake, sample_intake_paths=2)
    routes = {p["route"] for p in plans}
    assert routes >= {
        "loader", "/v1/screen", "/v1/compare", "/v1/study", "/v1/diff", "/v1/deck", "/v1/proposal", "/v1/spec",
        "/v1/portfolio", "/v1/power/assess", "/v1/power/deploy/assess", "/v1/qualify", "/v1/estimate",
    }  # fmt: skip
    assert len({p["name"] for p in plans}) == len(
        plans
    )  # unique names: findings are attributed by plan
    assert {p["tier"] for p in plans} == {"client", "public"}
    assert all(
        isinstance(p["base"], dict) and p["sample_intake_paths"] == 2
        for p in plans
        if p["route"] != "loader"
    )


def test_the_hostile_catalogue_contains_what_real_findings_needed():
    names = {n for n, _ in runner.hostile_values()}
    # the three classes found in the product: wrong types, enormous/non-finite numbers, structures nested past the parser's limit
    assert {
        "None",
        "x",
        "empty_list",
        "10**400",
        "1e308",
        "nan",
        "inf",
        "deep_list_3000",
        "deep_dict_3000",
    } <= names
    assert len(names) == len(
        runner.hostile_values()
    )  # names are unique so a finding says exactly which value


# --- the command -------------------------------------------------------------------------------------------------------------------


def test_the_cli_exit_codes_distinguish_healthy_unhealthy_and_unavailable(
    monkeypatch, capsys, tmp_path
):
    from gridforge_stress import cli

    healthy = fuzz.FuzzReport(calls=10, ok=4, clean=6, non_clean=(), slow=())
    sick = fuzz.FuzzReport(
        calls=10,
        ok=4,
        clean=5,
        non_clean=(fuzz.Finding("p", "AttributeError", "m", "x.py:1 f", 1, ("p x=None",)),),
        slow=(),
    )
    monkeypatch.setattr(fuzz, "run_fuzz", lambda plans, **kw: healthy)
    assert cli.main(["fuzz", "--sample", "1"]) == 0 and capsys.readouterr().out.rstrip().endswith(
        "HEALTHY"
    )
    monkeypatch.setattr(fuzz, "run_fuzz", lambda plans, **kw: sick)
    assert cli.main(["fuzz"]) == 1 and "NON-CLEAN [p]" in capsys.readouterr().out

    def unavailable(plans, **kw):
        raise ProductUnavailable("no checkout")

    monkeypatch.setattr(fuzz, "run_fuzz", unavailable)
    assert cli.main(["fuzz"]) == 2 and "NOT FUZZED: no checkout" in capsys.readouterr().err


def test_the_cli_passes_its_options_to_the_run(monkeypatch):
    from gridforge_stress import cli

    seen: dict[str, Any] = {}

    def spy(plans, **kw):
        seen.update(kw, sample={p["sample_intake_paths"] for p in plans if p["route"] != "loader"})
        return fuzz.FuzzReport(calls=1, ok=1, clean=0, non_clean=(), slow=())

    monkeypatch.setattr(fuzz, "run_fuzz", spy)
    assert cli.main(["fuzz", "--sample", "5", "--seed", "99", "--slow", "0.5"]) == 0
    assert seen["seed"] == 99 and seen["slow_s"] == 0.5 and seen["sample"] == {5}
