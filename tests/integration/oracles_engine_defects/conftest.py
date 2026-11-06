"""Module-scoped fixture shared by the engine-defect oracles (same pattern as test_product_undeclared_bank.py)."""

from __future__ import annotations

from typing import Any

import pytest

from ._common import reference_doc, require_product


@pytest.fixture(scope="module")
def intake() -> dict[str, Any]:
    require_product()
    return reference_doc()
