"""Module-scoped guard for the round-3 oracles: a named product checkout that is missing fails, an unnamed absent one skips."""

from __future__ import annotations

import pytest

from ..oracles_engine_defects._common import require_product


@pytest.fixture(scope="module", autouse=True)
def _product() -> None:
    require_product()
