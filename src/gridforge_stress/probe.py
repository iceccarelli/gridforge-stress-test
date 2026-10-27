"""Summarise what a publisher page actually says, as evidence for verifying registry entries."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any

from .manifest import Manifest

_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_HEAD = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
_LINK = re.compile(r"""href=["']([^"']+\.(?:xlsx?|csv|zip|pdf|json))(?:\?[^"']*)?["']""", re.I)
SIGNALS = {
    "license": r"licen[sc]e|CC BY|Creative Commons|attribution|public domain",
    "credentials": r"API key|api_key|register|registration|subscription key|sign in|log ?in",
    "time": r"\bUTC\b|local time|Eastern|Central Prevailing|hour[- ]ending|hour[- ]beginning",
    "release": r"edition|version \d|release|data (?:through|as of)",
    "headers": r"User-Agent",
}


def _text(body: bytes) -> str:
    return html.unescape(_TAG.sub(" ", body.decode("utf-8", "replace")))


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", s)).split())


def summarize(manifest: Manifest, headers: Mapping[str, str], body: bytes) -> dict[str, Any]:
    ctype = headers.get("content-type", "").split(";")[0].strip().lower()
    out: dict[str, Any] = {
        "source_id": manifest.source_id,
        "final_url": manifest.source_url,
        "http_status": manifest.response_status,
        "content_type": ctype,
        "bytes": manifest.byte_count,
        "sha256": manifest.sha256,
        "last_modified": headers.get("last-modified"),
        "etag": headers.get("etag"),
        "retrieved_at": manifest.retrieved_at,
        "retrieval_status": manifest.retrieval_status,
    }
    if "html" in ctype or "xml" in ctype or "json" in ctype:
        raw = body.decode("utf-8", "replace")
        text = _text(body)
        m = _TITLE.search(raw)
        out["title"] = _clean(m.group(1)) if m else None
        out["headings"] = [_clean(h) for h in _HEAD.findall(raw)][:20]
        out["data_links"] = sorted(set(_LINK.findall(raw)))[:25]
        sig: dict[str, list[str]] = {}
        for name, pat in SIGNALS.items():
            hits = []
            for mm in re.finditer(pat, text, re.I):
                lo, hi = max(0, mm.start() - 110), min(len(text), mm.end() + 110)
                hits.append(" ".join(text[lo:hi].split()))
                if len(hits) == 3:
                    break
            sig[name] = hits
        out["signals"] = sig
    else:
        out["note"] = "non-text content: not parsed here (needs a dedicated, tested extractor)"
    return out
