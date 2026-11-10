"""Hostile `assumed_fields` values are refused cleanly, quickly and without echoing the attack. Opt in with `pytest -m product`.

FUZZ oracle for the one new request key of the free, unauthenticated route POST /v1/qualify. Every request below goes through the product's REAL
HTTP handler (a private loopback server in the product's own interpreter, `product_adapter.http_batch`), so the status is what a caller would
see and the time includes parsing and serialising. The values: wrong types (text, numbers, booleans, objects), nested lists and objects,
non-text entries (null, numbers, huge integers, lists, objects), duplicates, unknown and case-variant names, evidence classes in place of
names, lists of 8, 10,000 and 10,500 entries, a 100,000-character name, control characters (NUL, ESC, CR/LF, U+0085, U+2028, bidi override), a
lone surrogate, markup and format-string text, non-finite and out-of-range numbers, a duplicate JSON key, and 50,000-deep nesting.

Invariant, per request: the status is below 500 (a caller's mistake is never a server fault); a value that must be refused is refused with a
4xx (never a quiet 200); the answer arrives within 3 s (the fuzz runner's own limit); the body is JSON; a refusal's text is at most 600
characters, holds no raw control, format, separator, surrogate or private-use character, and quotes at most 80 characters of what was sent.
A value the contract allows (absent, null, [], a valid list) is answered 200 and the answer is judged by the metamorphic oracle's judge.
After the whole barrage the server still answers /health and a plain request exactly as it did before (no state is poisoned).
Judge: `assumed_fields.hostile_response_violations`; its unit proof is tests/unit/test_assumed_fields.py. Not xfail'd.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gridforge_stress.assumed_fields import annotation_violations, hostile_response_violations
from gridforge_stress.product_adapter import http_batch

from ._common import HALL

pytestmark = [pytest.mark.integration, pytest.mark.product]

NAMES = list(HALL)
#: label -> (value of assumed_fields, may the request succeed?)
VALUES: dict[str, tuple[Any, bool]] = {
    "text": ("tapoff_max_A", False),
    "empty text": ("", False),
    "number": (7, False),
    "float": (1.5, False),
    "true": (True, False),
    "object": ({"tapoff_max_A": True}, False),
    "empty object": ({}, False),
    "list in list": ([["tapoff_max_A"]], False),
    "deep list in list": ([[[[[["tapoff_max_A"]]]]]], False),
    "null entry": ([None], False),
    "number entry": ([7], False),
    "bool entry": ([True], False),
    "float entry": ([1.5], False),
    "huge integer entry": ([10**400], False),
    "object entry": ([{"value": 63, "evidence": "E5"}], False),
    "empty text entry": ([""], False),
    "duplicate": (["tapoff_max_A", "tapoff_max_A"], False),
    "unknown name": (["no_such_field"], False),
    "one valid one unknown": (["tapoff_max_A", "no_such_field"], False),
    "case variant": (["Tapoff_max_A"], False),
    "padded name": ([" tapoff_max_A"], False),
    "path not name": (["lv.tapoff_max_A"], False),
    "derived field": (["firm_capacity_MVA"], False),
    "evidence class": (["E5"], False),
    "eight entries": (NAMES + ["contracted_MW"], False),
    "10k valid names": (["tapoff_max_A"] * 10_000, False),
    "10.5k all names": (NAMES * 1_500, False),
    "10k integers": (list(range(10_000)), False),
    "10k empty lists": ([[]] * 10_000, False),
    "100k character name": (["A" * 100_000], False),
    "control characters": (["\x00\x1b[31m\n\r\t\x85" + chr(0x2028) + chr(0x202E)], False),
    "control after a valid name": (["tapoff_max_A\x00"], False),
    "lone surrogate": (["\ud800"], False),
    "markup": (["<script>alert(1)</script>"], False),
    "format string": (["%s%s%n{0}{__class__}"], False),
    "sql": (["'; DROP TABLE halls; --"], False),
    "null": (None, True),
    "empty list": ([], True),
    "one valid name": (["tapoff_max_A"], True),
    "all valid names": (NAMES, True),
}
RAW: dict[str, tuple[str, bool]] = {
    "NaN entry": ('{"contracted_MW":12,"assumed_fields":[NaN]}', False),
    "Infinity entry": ('{"contracted_MW":12,"assumed_fields":[Infinity]}', False),
    "1e999 entry": ('{"contracted_MW":12,"assumed_fields":[1e999]}', False),
    "repeated key": (
        json.dumps(HALL)[:-1] + ',"assumed_fields":["tapoff_max_A"],"assumed_fields":"x"}',
        False,
    ),
    "50k nested lists": (
        '{"contracted_MW":12,"assumed_fields":' + "[" * 50_000 + "]" * 50_000 + "}",
        False,
    ),
    "50k nested objects": (
        '{"assumed_fields":' + '{"a":' * 50_000 + "1" + "}" * 50_000 + "}",
        False,
    ),
    "unterminated list": (json.dumps(HALL)[:-1] + ',"assumed_fields":["tapoff_max_A"', False),
    "invalid utf-8": (json.dumps(HALL)[:-1] + ',"assumed_fields":["\ud800"]}', False),
}
PLAIN_REQUEST = {"id": "plain-before", "method": "POST", "path": "/v1/qualify", "json": dict(HALL)}


@pytest.fixture(scope="module")
def answers() -> dict[str, dict[str, Any]]:
    reqs: list[dict[str, Any]] = [dict(PLAIN_REQUEST)]
    for label, (value, _) in VALUES.items():
        reqs.append(
            {
                "id": label,
                "method": "POST",
                "path": "/v1/qualify",
                "json": {**HALL, "assumed_fields": value},
            }
        )
    for label, (raw, _) in RAW.items():
        reqs.append({"id": label, "method": "POST", "path": "/v1/qualify", "raw": raw})
    reqs.append({"id": "plain-after", "method": "POST", "path": "/v1/qualify", "json": dict(HALL)})
    reqs.append({"id": "health-after", "method": "GET", "path": "/health"})
    return http_batch(reqs)


@pytest.mark.parametrize("label", list(VALUES))
def test_a_hostile_value_is_answered_cleanly(answers, label):
    value, may_succeed = VALUES[label]
    row = answers[label]
    problems = hostile_response_violations(
        label,
        value,
        row["status"],
        row["content_type"],
        row["body"],
        row["elapsed_s"],
        may_succeed=may_succeed,
    )
    if row["status"] == 200 and may_succeed:
        plain = json.loads(answers["plain-before"]["body"])
        request = {**HALL, "assumed_fields": value}
        problems += annotation_violations(request, plain, json.loads(row["body"]))
    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("label", list(RAW))
def test_a_hostile_body_is_answered_cleanly(answers, label):
    _, may_succeed = RAW[label]
    row = answers[label]
    problems = hostile_response_violations(
        label,
        None,
        row["status"],
        row["content_type"],
        row["body"],
        row["elapsed_s"],
        may_succeed=may_succeed,
    )
    assert not problems, "; ".join(problems)


def test_the_barrage_leaves_the_server_unchanged(answers):
    assert answers["health-after"]["status"] == 200
    assert answers["plain-before"]["status"] == 200
    assert answers["plain-after"]["body"] == answers["plain-before"]["body"]


def test_the_catalogue_is_not_all_refusals(answers):
    """Control arm: the valid declarations in the catalogue are answered, so a server that refuses everything would fail here."""
    accepted = [k for k, (_, ok) in VALUES.items() if ok and answers[k]["status"] == 200]
    assert accepted == [k for k, (_, ok) in VALUES.items() if ok], accepted
