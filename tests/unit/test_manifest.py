from __future__ import annotations

import pytest

from gridforge_stress.manifest import commit_snapshot, manifest_for_bytes
from gridforge_stress.registry import RegistryError

pytestmark = pytest.mark.unit


def mk(data, status="fetched_validated"):
    return manifest_for_bytes(
        data,
        source_id="eia-930",
        source_url="https://www.eia.gov/x",
        origin="live_fetch",
        attribution="EIA",
        retrieval_status=status,
        retrieved_at="2026-01-01T00:00:00Z",
        response_status=200,
    )


def test_commit_writes_data_and_manifest(tmp_path):
    t = commit_snapshot(tmp_path, "s", b"abc", mk(b"abc"))
    assert (tmp_path / "s.bin").read_bytes() == b"abc" and (tmp_path / "s.manifest.json").is_file()
    assert t.name == "s"


def test_failed_fetch_never_overwrites_valid_snapshot(tmp_path):
    commit_snapshot(tmp_path, "s", b"good", mk(b"good"))
    with pytest.raises(RegistryError):
        commit_snapshot(tmp_path, "s", b"bad", mk(b"bad", status="fetch_failed"))
    assert (tmp_path / "s.bin").read_bytes() == b"good"


def test_checksum_mismatch_rejected(tmp_path):
    with pytest.raises(RegistryError):
        commit_snapshot(tmp_path, "s", b"other", mk(b"abc"))
    assert not list(tmp_path.iterdir())


def test_no_temp_files_left(tmp_path):
    commit_snapshot(tmp_path, "s", b"abc", mk(b"abc"))
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
