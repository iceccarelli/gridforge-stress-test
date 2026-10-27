"""GF-005: ERCOT access status only. No live data, no access-control bypass."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .registry import default_sources


def ercot_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env_map = os.environ if env is None else env
    src = default_sources()["ercot-data-portal"]
    missing = [n for n in src.credential_env if not env_map.get(n)]
    return {
        "source_id": src.id,
        "access_status": src.access_status,
        "live_fetch": src.live_fetch,
        "credentials_missing": missing,
        "can_fetch_live": False,  # blocked until authorized access exists AND live fetch is verified/enabled
        "statement": "No ERCOT data has been retrieved by this harness; only offline contract fixtures exist.",
    }
