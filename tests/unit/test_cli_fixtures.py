from __future__ import annotations

import json
import shutil

import pytest

from gridforge_stress.cli import main
from gridforge_stress.fixtures import ok, validate_fixtures
from gridforge_stress.paths import fixtures_dir

pytestmark = pytest.mark.unit


def test_cli_commands(capsys):
    for argv in (["doctor"], ["sources", "list"], ["scenarios", "list"], ["validate-fixtures"]):
        assert main(argv) == 0, argv
    out = capsys.readouterr().out
    assert "ercot-data-portal" in out and "GF-008" in out


def test_cli_fetch_refuses_when_nothing_verified(capsys):
    assert main(["fetch", "eia-930"]) == 2
    assert "NOT FETCHED" in capsys.readouterr().err


def copy_fx(tmp_path):
    d = tmp_path / "fx"
    shutil.copytree(fixtures_dir(), d)
    return d


def test_tampered_fixture_detected(tmp_path):
    d = copy_fx(tmp_path)
    (d / "queue_synthetic.csv").write_text(
        (d / "queue_synthetic.csv").read_text() + "X,Active,2020-01-01,,,1,s\n"
    )
    assert "checksum" in {f.code for f in validate_fixtures(d)}


def test_missing_manifest_and_unlabelled_synthetic_and_live_download_detected(tmp_path):
    d = copy_fx(tmp_path)
    (d / "poi_synthetic.json.manifest.json").unlink()
    assert "no_manifest" in {f.code for f in validate_fixtures(d)}
    d = copy_fx(tmp_path / "b")
    mp = d / "tariff_synthetic.json.manifest.json"
    m = json.loads(mp.read_text())
    m["limitations"] = ["fine"]
    mp.write_text(json.dumps(m))
    assert "unlabelled_synthetic" in {f.code for f in validate_fixtures(d)}
    m["limitations"] = ["SYNTHETIC"]
    m["origin"] = "live_fetch"
    mp.write_text(json.dumps(m))
    assert "raw_download_in_fixtures" in {f.code for f in validate_fixtures(d)}


def test_content_defect_in_fixture_detected(tmp_path):
    d = copy_fx(tmp_path)
    t = json.loads((d / "tariff_synthetic.json").read_text())
    t["customer_class"] = ""
    data = json.dumps(t).encode()
    (d / "tariff_synthetic.json").write_bytes(data)
    import hashlib

    mp = d / "tariff_synthetic.json.manifest.json"
    m = json.loads(mp.read_text())
    m["sha256"] = hashlib.sha256(data).hexdigest()
    m["byte_count"] = len(data)
    mp.write_text(json.dumps(m))
    assert not ok(validate_fixtures(d))
