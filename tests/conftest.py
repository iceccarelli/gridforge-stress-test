from __future__ import annotations

import copy
from typing import Any

import pytest

from gridforge_stress.registry import Source, load_sources


def enable(source_id: str, **overrides: Any) -> dict[str, Source]:
    """Registry copy with one source live-enabled, for exercising the fetch guards offline."""
    reg = load_sources()
    raw = copy.deepcopy(reg[source_id].raw)
    raw["live_fetch"] = "enabled"
    raw["verification"]["status"] = "publisher_page_read"
    raw.update(overrides)
    reg[source_id] = Source(raw)
    return reg


@pytest.fixture
def public_ip_resolver():
    return lambda host: ["93.184.216.34"]
