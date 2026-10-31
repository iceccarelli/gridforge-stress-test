"""The prose that states what has been verified must not drift from the registry that records it (it had: '7 of 9' while the registry held 10)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gridforge_stress.registry import load_sources

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def test_readme_source_counts_match_the_registry():
    sources = load_sources()
    read = sum(
        1 for s in sources.values() if s.raw["verification"]["status"] == "publisher_page_read"
    )
    m = re.search(r"Publisher pages for (\d+) of (\d+) sources", (ROOT / "README.md").read_text())
    assert m, "README must state how many publisher pages have been read"
    assert (int(m.group(1)), int(m.group(2))) == (read, len(sources))


def _table_rows() -> dict[str, str]:
    rows = {}
    for line in (ROOT / "docs" / "data-sources.md").read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0] not in ("id", "---") and not cells[0].startswith("-"):
            rows[cells[0]] = cells[2]
    return rows


def test_every_registered_source_has_exactly_one_row():
    assert set(_table_rows()) == set(load_sources())


def test_a_source_is_called_read_only_if_the_registry_says_its_page_was_read():
    rows = _table_rows()
    for sid, src in load_sources().items():
        said_yes = rows[sid].lower().startswith("yes")
        assert said_yes == (src.raw["verification"]["status"] == "publisher_page_read"), (
            sid,
            rows[sid],
        )


def test_no_source_is_documented_as_live():
    text = (ROOT / "docs" / "data-sources.md").read_text()
    assert all(s.live_fetch != "enabled" for s in load_sources().values())
    assert "none is\nenabled" in text or "none is enabled" in text


def test_docs_never_tell_anyone_to_dispatch_the_product_workflow_with_a_branch():
    """product-integration refuses anything but a full 40-character SHA (a branch is not a product identity); the prose must agree with it."""
    bad = []
    for path in [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *(ROOT / "docs").glob("*.md")]:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for m in re.finditer(r"product_ref\s*=\s*`?([^\s`,.;)]+)", line):
                if not re.fullmatch(r"[0-9a-f]{40}", m.group(1)):
                    bad.append(f"{path.name}:{n}: product_ref={m.group(1)}")
    assert not bad, bad
