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


def test_probe_urls_are_allowed_but_arbitrary_urls_are_not():
    reg = load_sources()
    extra = reg["nrel-cambium"].probe_urls[0]
    F.probe_source("nrel-cambium", url=extra, resolver=PUB, transport=lambda *a: ok(), sources=reg)
    for bad in (
        "https://eta-publications.lbl.gov/other",
        "https://emp.lbl.gov/queues",  # the 403 landing page is deliberately not registered
        "https://169.254.169.254/",
        "http://eta-publications.lbl.gov/publications/us-interconnection-queue-data-0",
    ):
        with pytest.raises(F.FetchBlocked):
            F.probe_source(
                "lbnl-queued-up", url=bad, resolver=PUB, transport=lambda *a: ok(), sources=reg
            )


# --- probe design: pages, datasets, policy blocks and unexpected failures are distinct -----------------


_REAL_PROBE = F.probe_source  # bound once: re-patching within a test must not wrap a wrapper


def _route(monkeypatch, transport):
    """Run the real probe_source (all guards) through the CLI with a fake network."""
    real = _REAL_PROBE
    seen: list[tuple[str, str | None, bool]] = []

    def routed(sid, url=None, dataset=False, **kw):
        seen.append((sid, url, dataset))
        return real(sid, url=url, dataset=dataset, resolver=PUB, transport=transport)

    monkeypatch.setattr(F, "probe_source", routed)
    return seen


def _lines(capsys):
    """Single-line JSON records printed by the CLI (pretty-printed page reports are skipped)."""
    import json

    out = []
    for line in capsys.readouterr().out.splitlines():
        if line.startswith("{") and line.endswith("}"):
            out.append(json.loads(line))
    return out


def test_probe_all_never_downloads_a_registered_dataset_and_policy_block_is_not_a_failure(
    monkeypatch, capsys
):
    from gridforge_stress import cli

    bytes_asked = []

    def net(ip, host, target, headers, max_bytes, deadline):
        bytes_asked.append((host + target, max_bytes))
        return ok()

    seen = _route(monkeypatch, net)
    assert cli.main(["probe", "--all"]) == 0
    reg = load_sources()
    datasets = {u for s in reg.values() for u in s.data_urls}
    assert datasets, "the registry must declare the LBNL workbook as a dataset download"
    assert not any(url in datasets for _, url, _ in seen)
    assert all(not ds for _, _, ds in seen)
    assert all(cap <= F.PROBE_MAX_BYTES for _, cap in bytes_asked)
    assert not any(h.endswith(".xlsx") for h, _ in bytes_asked)
    summary = next(x for x in _lines(capsys) if "probe_summary" in x)["probe_summary"]
    assert summary["policy_blocked"] == 1 and summary["not_read"] == 0
    assert summary["read"] == sum(1 + len(s.probe_urls) for s in reg.values()) - 1


def test_dataset_urls_cannot_be_read_as_pages_and_pages_cannot_be_inspected_as_datasets():
    reg = load_sources()
    src = reg["lbnl-queued-up"]
    calls = []
    net = lambda *a: calls.append(a) or ok()  # noqa: E731
    with pytest.raises(F.FetchBlocked, match="explicit inspection"):
        F.probe_source(
            "lbnl-queued-up", url=src.data_urls[0], resolver=PUB, transport=net, sources=reg
        )
    with pytest.raises(F.FetchBlocked, match="data_urls"):
        F.probe_source("lbnl-queued-up", dataset=True, resolver=PUB, transport=net, sources=reg)
    with pytest.raises(F.FetchBlocked):
        F.probe_source(
            "lbnl-queued-up",
            url="https://eta-publications.lbl.gov/other.xlsx",
            dataset=True,
            resolver=PUB,
            transport=net,
            sources=reg,
        )
    assert calls == []  # every refusal happened before any network I/O


def test_dataset_inspection_enforces_the_source_cap_and_is_not_widened_globally():
    reg = load_sources()
    raw = dict(reg["lbnl-queued-up"].raw, max_bytes=1000)
    small = {"lbnl-queued-up": type(reg["lbnl-queued-up"])(raw)}
    url = small["lbnl-queued-up"].data_urls[0]
    with pytest.raises(F.FetchFailed, match="cap"):
        F.probe_source(
            "lbnl-queued-up",
            url=url,
            dataset=True,
            resolver=PUB,
            transport=lambda *a: F.Response(200, {}, b"x" * 1001),
            sources=small,
        )
    with pytest.raises(F.FetchFailed, match="declares"):
        F.check_declared_length({"content-length": "1001"}, 1000)
    assert F.PROBE_MAX_BYTES == 3_000_000  # the generic page cap is unchanged


@pytest.mark.parametrize("status", [403, 404])
def test_unexpected_http_errors_are_actionable_failures_with_the_truthful_reason(
    monkeypatch, capsys, status
):
    from gridforge_stress import cli

    _route(monkeypatch, lambda *a: F.Response(status, {}, b"denied"))
    assert cli.main(["probe", "eia-930"]) == 3
    rec = next(x for x in _lines(capsys) if x.get("probe") == "NOT_READ")
    assert rec["source_id"] == "eia-930" and rec["http_status"] == status
    assert f"HTTP {status}" in rec["reason"]
    assert rec["retrieval_status"] == "not_fetched" and rec["kind"] == "publisher_page"
    assert rec["url"] == load_sources()["eia-930"].locator
    assert rec["at"].endswith("Z")


def test_redirect_to_an_unregistered_host_is_not_read_and_not_mistaken_for_policy(
    monkeypatch, capsys
):
    from gridforge_stress import cli

    _route(monkeypatch, lambda *a: F.Response(302, {"location": "https://evil.example/x"}, b""))
    assert cli.main(["probe", "eia-930"]) == 3
    rec = next(x for x in _lines(capsys) if "probe" in x)
    assert rec["probe"] == "NOT_READ" and "not registered" in rec["reason"]


def test_oversized_page_and_unsafe_workbook_are_not_read(monkeypatch, capsys):
    from gridforge_stress import cli

    _route(monkeypatch, lambda *a: ok(body=b"x" * 3_000_001))
    assert cli.main(["probe", "eia-930"]) == 3
    assert "exceeds cap" in next(x for x in _lines(capsys) if "probe" in x)["reason"]
    _route(monkeypatch, lambda *a: ok(body=b"not a zip file", ctype="application/octet-stream"))
    assert cli.main(["probe", "lbnl-queued-up", "--peek-xlsx"]) == 3
    recs = [x for x in _lines(capsys) if x.get("probe") == "NOT_READ"]
    assert recs and recs[0]["kind"] == "dataset_inspection"
    assert recs[0]["url"] == load_sources()["lbnl-queued-up"].data_urls[0]


def test_workbook_inspection_without_a_registered_dataset_url_is_a_failure(monkeypatch, capsys):
    from gridforge_stress import cli

    seen = _route(monkeypatch, lambda *a: ok())
    assert cli.main(["probe", "eia-930", "--peek-xlsx"]) == 3
    assert seen == []
    assert "no registered .xlsx" in next(x for x in _lines(capsys) if "probe" in x)["reason"]


def test_ercot_stays_blocked_through_every_entry_point_without_touching_the_network():
    reg = load_sources()
    calls = []
    net = lambda *a: calls.append(a) or ok()  # noqa: E731
    env = {n: "secret-value" for n in reg["ercot-data-portal"].credential_env}
    for call in (
        lambda: F.probe_source("ercot-data-portal", resolver=PUB, transport=net),
        lambda: F.probe_source("ercot-data-portal", dataset=True, resolver=PUB, transport=net),
        lambda: F.probe_source(
            "ercot-data-portal",
            url="https://apiexplorer.ercot.com/api",
            resolver=PUB,
            transport=net,
        ),
        lambda: F.fetch_source("ercot-data-portal", env=env, resolver=PUB, transport=net),
    ):
        with pytest.raises(F.PolicyBlocked):
            call()
    assert calls == []


def test_exit_policy_is_documented_and_matches_behaviour(monkeypatch, capsys):
    import json

    from gridforge_stress import cli

    assert "policy-blocked" in cli.EXIT_POLICY and "NOT_READ" in cli.EXIT_POLICY
    # a policy block alone -> 0 (real guards, no network involved)
    assert cli.main(["probe", "ercot-data-portal"]) == 0
    out = capsys.readouterr().out.splitlines()
    blocked = json.loads(out[0])
    assert blocked["probe"] == "BLOCKED_BY_POLICY" and blocked["source_id"] == "ercot-data-portal"
    assert blocked["retrieval_status"] == "not_fetched" and blocked["at"]
    assert json.loads(out[-1])["probe_summary"]["exit_policy"] == cli.EXIT_POLICY
    # an unknown source id is actionable, not an expected restriction
    assert cli.main(["probe", "no-such-source"]) == 3
    assert json.loads(capsys.readouterr().out.splitlines()[0])["probe"] == "NOT_READ"
    # everything unreadable except the policy-blocked source: the run still fails
    _route(monkeypatch, lambda *a: F.Response(500, {}, b""))
    assert cli.main(["probe", "--all"]) == 3
    summary = next(x for x in _lines(capsys) if "probe_summary" in x)["probe_summary"]
    reg = load_sources()
    assert summary["policy_blocked"] == 1 and summary["read"] == 0
    assert summary["not_read"] == sum(1 + len(s.probe_urls) for s in reg.values()) - 1
