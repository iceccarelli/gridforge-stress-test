from __future__ import annotations

import pytest

from gridforge_stress import fetch as F
from gridforge_stress.probe import summarize
from gridforge_stress.registry import load_sources

pytestmark = pytest.mark.unit
PUB = lambda h: ["93.184.216.34"]  # noqa: E731
HTML = (
    b"<html><title>Queued Up 2026</title><h2>Data</h2>"
    b'<a href="/files/q.xlsx?x=1">w</a> Data licensed CC BY 4.0, attribute LBNL.'
    b" Requires an API key. Times are UTC.</html>"
)


def ok(body=HTML, ctype="text/html; charset=utf-8", headers=None):
    return F.Response(200, {"content-type": ctype, **(headers or {})}, body)


def test_probe_works_while_live_fetch_disabled_and_needs_no_credentials():
    reg = load_sources()
    assert reg["eia-930"].live_fetch == "disabled_until_verified" and reg["eia-930"].credential_env
    body, man, headers = F.probe_source(
        "eia-930", resolver=PUB, transport=lambda *a: ok(), sources=reg
    )
    assert man.retrieval_status == "fetched_unparsed" and body == HTML and headers


def test_probe_does_not_enforce_unverified_content_types():
    reg = load_sources()
    F.probe_source(
        "caiso-pge-c16-poi", resolver=PUB, transport=lambda *a: ok(ctype="text/html"), sources=reg
    )


def test_probe_keeps_ercot_blocked_and_arbitrary_ids_refused():
    with pytest.raises(F.FetchBlocked):
        F.probe_source("ercot-data-portal", resolver=PUB, transport=lambda *a: ok())
    with pytest.raises(F.FetchBlocked):
        F.probe_source("https://169.254.169.254/", resolver=PUB, transport=lambda *a: ok())


def test_probe_keeps_ssrf_redirect_and_size_guards():
    with pytest.raises(F.FetchBlocked):
        F.probe_source("eia-930", resolver=lambda h: ["10.0.0.1"], transport=lambda *a: ok())
    off = lambda *a: F.Response(302, {"location": "https://evil.example/"}, b"")  # noqa: E731
    with pytest.raises(F.FetchBlocked):
        F.probe_source("eia-930", resolver=PUB, transport=off)
    seen = {}

    def spy(ip, host, target, headers, max_bytes, deadline):
        seen["cap"] = max_bytes
        return ok()

    F.probe_source("eia-930", resolver=PUB, transport=spy)
    assert seen["cap"] <= F.PROBE_MAX_BYTES
    with pytest.raises(F.FetchFailed):
        F.probe_source("eia-930", resolver=PUB, transport=lambda *a: ok(body=b"x" * 4_000_000))


def test_probe_makes_a_single_attempt():
    calls = []

    def flaky(*a):
        calls.append(1)
        return F.Response(503, {}, b"")

    with pytest.raises(F.FetchFailed):
        F.probe_source("eia-930", resolver=PUB, transport=flaky)
    assert len(calls) == 1


def test_summary_extracts_evidence_and_labels_non_text():
    reg = load_sources()
    _, man, headers = F.probe_source(
        "lbnl-queued-up",
        resolver=PUB,
        transport=lambda *a: ok(headers={"last-modified": "Tue, 01 Jan 2026"}),
        sources=reg,
    )
    s = summarize(man, headers, HTML)
    assert s["title"] == "Queued Up 2026" and s["data_links"] == ["/files/q.xlsx"]
    assert any("CC BY" in h for h in s["signals"]["license"])
    assert any("API key" in h for h in s["signals"]["credentials"])
    assert any("UTC" in h for h in s["signals"]["time"])
    assert s["last_modified"] == "Tue, 01 Jan 2026"
    _, man2, h2 = F.probe_source(
        "lbnl-queued-up", resolver=PUB, transport=lambda *a: ok(b"%PDF", "application/pdf")
    )
    assert "not parsed" in summarize(man2, h2, b"%PDF")["note"]
