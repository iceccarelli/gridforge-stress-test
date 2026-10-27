from __future__ import annotations

import pytest

from gridforge_stress import fetch as F
from tests.conftest import enable

pytestmark = pytest.mark.unit

SRC = "caiso-pge-c16-poi"
URL = "https://www.caiso.com/documents/x.pdf"


def ok_response(body=b"%PDF-1.7 hello", ctype="application/pdf"):
    return F.Response(200, {"content-type": ctype}, body)


def run(
    reg, transport, resolver=lambda h: ["93.184.216.34"], url=None, env=None, sleep=lambda s: None
):
    return F.fetch_source(
        SRC,
        url=url,
        sources=reg,
        transport=transport,
        resolver=resolver,
        env=env or {},
        sleep=sleep,
    )


def test_success_manifest_is_checksummed_and_not_claimed_as_content_validated():
    reg = enable(SRC)
    body, man = run(reg, lambda *a: ok_response())
    assert man.retrieval_status == "fetched_unparsed" and man.response_status == 200
    assert man.byte_count == len(body) and len(man.sha256) == 64
    man.to_dict()  # schema-valid


def test_disabled_source_is_refused_before_any_io():
    from gridforge_stress.registry import load_sources

    called = []
    with pytest.raises(F.FetchBlocked):
        F.fetch_source(SRC, sources=load_sources(), transport=lambda *a: called.append(1), env={})
    assert not called


def test_unknown_source_and_arbitrary_urls_refused():
    reg = enable(SRC)
    with pytest.raises(F.FetchBlocked):
        F.fetch_source("http://169.254.169.254/", sources=reg, env={})


@pytest.mark.parametrize(
    "url",
    [
        "http://www.caiso.com/x",
        "https://user:pw@www.caiso.com/x",
        "https://www.caiso.com:8443/x",
        "https://evil.example/x",
        "https://www.caiso.com.evil.example/x",
        "file:///etc/passwd",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/x",
    ],
)
def test_hostile_urls_blocked(url):
    reg = enable(SRC)
    with pytest.raises(F.FetchBlocked):
        run(reg, lambda *a: pytest.fail("network reached"), url=url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "100.64.0.1",
        "0.0.0.0",
    ],
)
def test_non_public_resolution_blocked_including_rebinding(ip):
    reg = enable(SRC)
    with pytest.raises(F.FetchBlocked):
        run(
            reg, lambda *a: pytest.fail("network reached"), resolver=lambda h: ["93.184.216.34", ip]
        )


def test_redirect_to_unregistered_host_blocked():
    reg = enable(SRC)
    t = lambda *a: F.Response(302, {"location": "https://evil.example/x"}, b"")  # noqa: E731
    with pytest.raises(F.FetchBlocked):
        run(reg, t)


def test_redirect_downgrade_to_http_blocked():
    reg = enable(SRC)
    t = lambda *a: F.Response(301, {"location": "http://www.caiso.com/x"}, b"")  # noqa: E731
    with pytest.raises(F.FetchBlocked):
        run(reg, t)


def test_redirect_within_registered_host_followed_and_loop_bounded():
    reg = enable(SRC)
    seq = iter([F.Response(302, {"location": "/y.pdf"}, b""), ok_response()])
    _, man = run(reg, lambda *a: next(seq))
    assert man.source_url.endswith("/y.pdf")
    loop = lambda *a: F.Response(302, {"location": "/again"}, b"")  # noqa: E731
    with pytest.raises(F.FetchFailed, match="redirect"):
        run(reg, loop)


def test_content_type_and_size_enforced():
    reg = enable(SRC)
    with pytest.raises(F.FetchFailed, match="content type"):
        run(reg, lambda *a: ok_response(ctype="text/html"))
    small = enable(SRC, max_bytes=10)
    with pytest.raises(F.FetchFailed):
        run(small, lambda *a: ok_response(body=b"x" * 11))


def test_transient_errors_retry_with_backoff_then_succeed_and_are_bounded():
    reg = enable(SRC)
    seq = iter([F.Response(503, {}, b""), F.Response(429, {}, b""), ok_response()])
    sleeps: list[float] = []
    _, man = run(reg, lambda *a: next(seq), sleep=sleeps.append)
    assert man.retrieval_status == "fetched_unparsed" and sleeps == [2.0, 4.0]
    calls = []

    def always_503(*a):
        calls.append(1)
        return F.Response(503, {}, b"")

    with pytest.raises(F.FetchFailed):
        run(reg, always_503)
    assert len(calls) == F.MAX_ATTEMPTS


def test_permanent_error_not_retried():
    reg = enable(SRC)
    calls = []

    def nf(*a):
        calls.append(1)
        return F.Response(404, {}, b"")

    with pytest.raises(F.FetchFailed):
        run(reg, nf)
    assert len(calls) == 1


def test_missing_credentials_reported_not_faked():
    reg = enable("eia-930", access_status="api_key_required")
    with pytest.raises(F.FetchBlocked, match="EIA_API_KEY"):
        F.fetch_source("eia-930", sources=reg, env={})


def test_credentials_never_leak_in_errors():
    reg = enable("eia-930")
    secret = "s3cr3t-key-value"

    def boom(*a):
        raise OSError(f"connection failed for key={secret}")

    with pytest.raises(F.FetchFailed) as ei:
        F.fetch_source(
            "eia-930",
            sources=reg,
            env={"EIA_API_KEY": secret},
            transport=boom,
            resolver=lambda h: ["93.184.216.34"],
            sleep=lambda s: None,
        )
    assert secret not in str(ei.value)


def test_gated_source_cannot_be_enabled():
    import copy
    import json
    import pathlib
    import tempfile

    from gridforge_stress.registry import RegistryError, Source, load_sources

    reg = load_sources()
    raw = copy.deepcopy(reg["ercot-data-portal"].raw)
    raw["live_fetch"] = "enabled"
    doc = {"schema_version": "source-registry/1", "sources": [raw]}
    p = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(RegistryError):
        load_sources(p)
    assert isinstance(reg["ercot-data-portal"], Source)


def test_redact():
    assert F.redact("a SECRET b", {"K": "SECRET"}) == "a [REDACTED] b"
