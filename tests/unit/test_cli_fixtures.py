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


def _copy_fixtures(tmp_path):
    import shutil

    from gridforge_stress.paths import fixtures_dir

    dst = tmp_path / "fx"
    shutil.copytree(fixtures_dir(), dst)
    return dst


def _edit_manifest(root, name, mutate):
    import json

    p = root / f"{name}.manifest.json"
    d = json.loads(p.read_text())
    mutate(d)
    p.write_text(json.dumps(d))


def test_shipped_fixtures_have_no_errors_and_surface_the_unverified_source():
    from gridforge_stress.findings import errors
    from gridforge_stress.fixtures import validate_fixtures
    from gridforge_stress.paths import fixtures_dir

    found = validate_fixtures(fixtures_dir())
    assert not errors(found), errors(found)
    assert any(f.code == "source_not_verified" and "eia_retail" in f.where for f in found)


def test_product_subdirectory_fixtures_are_scanned(tmp_path):
    from gridforge_stress.fixtures import validate_fixtures

    root = _copy_fixtures(tmp_path)
    (root / "product" / "stray_intake.json").write_text("{}")
    found = validate_fixtures(root)
    assert any(f.code == "no_manifest" and "stray_intake" in f.where for f in found)


@pytest.mark.parametrize(
    "field,value",
    [("retrieved_at", None), ("upstream_release", ""), ("units", {}), ("transformations", [])],
)
def test_source_derived_fixture_missing_provenance_is_an_error(tmp_path, field, value):
    from gridforge_stress.findings import errors
    from gridforge_stress.fixtures import validate_fixtures

    root = _copy_fixtures(tmp_path)
    _edit_manifest(root, "lbnl_ir_to_cod_context.json", lambda d: d.__setitem__(field, value))
    codes = {f.code for f in errors(validate_fixtures(root))}
    assert codes & {"incomplete_provenance", "manifest_schema"}, codes


def test_eia_page_hash_must_match_the_manifest(tmp_path):
    from gridforge_stress.findings import errors
    from gridforge_stress.fixtures import validate_fixtures

    root = _copy_fixtures(tmp_path)
    _edit_manifest(
        root,
        "eia_retail_price_context.json",
        lambda d: d.__setitem__(
            "upstream_release", "EIA Electric Power Monthly, release of October 23, 2026"
        ),
    )
    assert "provenance_mismatch" in {f.code for f in errors(validate_fixtures(root))}


def test_intake_fixture_missing_a_core_block_is_an_error(tmp_path):
    import hashlib
    import json

    from gridforge_stress.findings import errors
    from gridforge_stress.fixtures import validate_fixtures

    root = _copy_fixtures(tmp_path)
    p = root / "product" / "reference_hall_intake.json"
    d = json.loads(p.read_text())
    del d["grid"]
    raw = json.dumps(d).encode()
    p.write_bytes(raw)
    _edit_manifest(
        root / "product",
        "reference_hall_intake.json",
        lambda m: m.update(sha256=hashlib.sha256(raw).hexdigest(), byte_count=len(raw)),
    )
    assert "bad_intake_fixture" in {f.code for f in errors(validate_fixtures(root))}
