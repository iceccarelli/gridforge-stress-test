"""Safe acquisition: registered sources only, SSRF-resistant, bounded, credential-redacting.

Network access is behind injectable `Resolver` and `Transport` so every guard is testable offline.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .manifest import Manifest, sha256_hex, utc_now
from .registry import Source, default_sources

MAX_REDIRECTS = 3
MAX_ATTEMPTS = 3
CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 20.0
TOTAL_TIMEOUT_S = 60.0
USER_AGENT = "gridforge-stress/0.1 (independent test harness)"


class FetchError(Exception):
    """Base class; messages never contain credential values."""


class FetchBlocked(FetchError):
    """Request refused before any network I/O (policy, credentials, source state)."""


class FetchFailed(FetchError):
    """Network or content validation failed after the request was allowed."""


def redact(text: str, secrets: Mapping[str, str]) -> str:
    for value in secrets.values():
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


class Resolver(Protocol):
    def __call__(self, host: str) -> list[str]: ...


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(i[4][0]) for i in infos})


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def __call__(
        self,
        ip: str,
        host: str,
        target: str,
        headers: Mapping[str, str],
        max_bytes: int,
        deadline: float,
    ) -> Response: ...


def check_declared_length(headers: Mapping[str, str], cap: int) -> None:
    """Refuse before reading the body when the server already says it is larger than the cap."""
    declared = headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise FetchFailed(f"download exceeds cap of {cap} bytes (server declares {declared} bytes)")


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Connects to a pre-validated IP while keeping SNI and certificate checks on the hostname."""

    def __init__(self, ip: str, host: str, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip
        self._host_name = host

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, 443), timeout=CONNECT_TIMEOUT_S)
        sock.settimeout(READ_TIMEOUT_S)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._host_name)  # type: ignore[attr-defined]


def default_transport(
    ip: str, host: str, target: str, headers: Mapping[str, str], max_bytes: int, deadline: float
) -> Response:
    conn = _PinnedHTTPS(ip, host, READ_TIMEOUT_S)
    try:
        conn.request("GET", target, headers={"Host": host, **headers})
        resp = conn.getresponse()
        check_declared_length({k.lower(): v for k, v in resp.getheaders()}, max_bytes)
        chunks: list[bytes] = []
        total = 0
        while True:
            if time.monotonic() > deadline:
                raise FetchFailed("total timeout exceeded")
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise FetchFailed(f"download exceeds cap of {max_bytes} bytes")
            chunks.append(chunk)
        return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, b"".join(chunks))
    finally:
        conn.close()


def _check_url(source: Source, url: str) -> SplitResult:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchBlocked("only https URLs are allowed")
    if parts.username or parts.password:
        raise FetchBlocked("credentials in URL are not allowed")
    if parts.port not in (None, 443):
        raise FetchBlocked("only port 443 is allowed")
    host = (parts.hostname or "").lower()
    if host not in source.allowed_hosts:
        raise FetchBlocked(f"host {host!r} is not registered for source {source.id}")
    return parts


def missing_credentials(source: Source, env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    return [name for name in source.credential_env if not env.get(name)]


def fetch_source(
    source_id: str,
    *,
    url: str | None = None,
    env: Mapping[str, str] | None = None,
    resolver: Resolver = system_resolver,
    transport: Transport = default_transport,
    sleep: Callable[[float], None] = time.sleep,
    sources: Mapping[str, Source] | None = None,
) -> tuple[bytes, Manifest]:
    """Fetch one registered source. Returns (body, manifest). Never raises secrets.

    The manifest says `fetched_unparsed`: transport checks passed (status 200, content type, size) but no
    parser has looked at the content. Use `manifest.promote` after content validation to reach `fetched_validated`.
    """
    registry = sources if sources is not None else default_sources()
    source = registry.get(source_id)
    if source is None:
        raise FetchBlocked(f"unknown source id {source_id!r}; arbitrary URLs are not accepted")
    if source.live_fetch != "enabled":
        raise FetchBlocked(f"live fetch for {source_id} is {source.live_fetch}")
    env_map = os.environ if env is None else env
    missing = missing_credentials(source, env_map)
    if missing:
        raise FetchBlocked(f"missing credentials: {', '.join(missing)}")
    secrets = {n: env_map[n] for n in source.credential_env}

    current = url or source.locator
    deadline = time.monotonic() + TOTAL_TIMEOUT_S
    last_error = "no attempt made"
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            sleep(min(2.0**attempt, 8.0))
        try:
            body, manifest, _headers = _follow(
                source, current, resolver, transport, deadline, secrets
            )
            return body, manifest
        except FetchBlocked:
            raise
        except (FetchFailed, OSError, http.client.HTTPException, ssl.SSLError) as exc:
            last_error = redact(str(exc), secrets)
            if isinstance(exc, FetchFailed) and not getattr(exc, "retryable", False):
                break
    raise FetchFailed(f"{source_id}: {last_error}")


class _Retryable(FetchFailed):
    retryable = True


def _follow(
    source: Source,
    url: str,
    resolver: Resolver,
    transport: Transport,
    deadline: float,
    secrets: Mapping[str, str],
    *,
    max_bytes: int | None = None,
    enforce_content_type: bool = True,
) -> tuple[bytes, Manifest, Mapping[str, str]]:
    cap = max_bytes if max_bytes is not None else source.max_bytes
    for _ in range(MAX_REDIRECTS + 1):
        parts = _check_url(source, url)
        host = (parts.hostname or "").lower()
        ips = resolver(host)
        if not ips or not all(_is_public(ip) for ip in ips):
            raise FetchBlocked(f"{host} resolves to a non-public address; refusing")
        target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        resp = _first_reachable(ips, host, target, transport, cap, deadline)
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                raise FetchFailed("redirect without Location")
            url = _join(url, location)
            continue
        if resp.status in (429, 500, 502, 503, 504):
            raise _Retryable(f"HTTP {resp.status}")
        if resp.status != 200:
            raise FetchFailed(f"HTTP {resp.status}")
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if (
            enforce_content_type
            and source.allowed_content_types
            and ctype not in source.allowed_content_types
        ):
            raise FetchFailed(f"unexpected content type {ctype!r}")
        if len(resp.body) > cap:
            raise FetchFailed("download exceeds cap")
        manifest = Manifest(
            source_id=source.id,
            source_url=url,
            origin="live_fetch",
            retrieval_status="fetched_unparsed",
            attribution=str(source.raw["license_attribution"]),
            sha256=sha256_hex(resp.body),
            byte_count=len(resp.body),
            retrieved_at=utc_now(),
            response_status=resp.status,
            limitations=list(source.raw["limitations"]),
        )
        return resp.body, manifest, resp.headers
    raise FetchFailed("too many redirects")


def _first_reachable(
    ips: list[str], host: str, target: str, transport: Transport, cap: int, deadline: float
) -> Response:
    """Try each already-validated public address, IPv4 first (runners often lack an IPv6 route)."""
    ordered = sorted(ips, key=lambda ip: ":" in ip)
    last: OSError | None = None
    for ip in ordered:
        try:
            return transport(ip, host, target, {"User-Agent": USER_AGENT}, cap, deadline)
        except OSError as exc:  # connect/route failure: try the next validated address
            last = exc
    raise last if last is not None else FetchFailed("no address to connect to")


def _join(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location)


PROBE_MAX_BYTES = 3_000_000


def probe_source(
    source_id: str,
    *,
    url: str | None = None,
    resolver: Resolver = system_resolver,
    transport: Transport = default_transport,
    sources: Mapping[str, Source] | None = None,
) -> tuple[bytes, Manifest, Mapping[str, str]]:
    """Read-only look at a registered source's own locator, to verify what the publisher says.

    Same host/IP/redirect/size guards as `fetch_source`, but: works while live fetch is still
    `disabled_until_verified`, sends no credentials, does not enforce the (unverified) content-type list,
    caps the body at PROBE_MAX_BYTES, makes one attempt, and returns headers. Gated sources stay blocked.
    The result is evidence for a human or a verifier to read; it is never stored as a snapshot.
    """
    registry = sources if sources is not None else default_sources()
    source = registry.get(source_id)
    if source is None:
        raise FetchBlocked(f"unknown source id {source_id!r}; arbitrary URLs are not accepted")
    if source.live_fetch == "blocked_until_authorized":
        raise FetchBlocked(f"{source_id} is blocked until authorized access exists")
    if url is not None and url != source.locator and url not in source.probe_urls:
        raise FetchBlocked("probe may only read the locator or a registered probe_urls entry")
    deadline = time.monotonic() + TOTAL_TIMEOUT_S
    try:
        return _follow(
            source,
            url or source.locator,
            resolver,
            transport,
            deadline,
            {},
            max_bytes=min(source.max_bytes, PROBE_MAX_BYTES),
            enforce_content_type=False,
        )
    except FetchBlocked:
        raise
    except (FetchFailed, OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise FetchFailed(f"{source_id}: {exc}") from exc
