from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from gridforge_stress.paths import configs_dir, fixtures_dir
from gridforge_stress.registry import RegistryError, load_scenarios, load_sources

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def test_registry_and_catalogue_valid():
    assert len(load_sources()) == 9
    assert {s["id"] for s in load_scenarios()} == {f"GF-00{i}" for i in range(1, 9)}


def test_no_source_claims_unread_pages_as_verified():
    for s in load_sources().values():
        assert s.raw["verification"]["status"] != "publisher_page_read", s.id
        assert s.live_fetch != "enabled", s.id


def test_ercot_blocked_and_registration_gated():
    e = load_sources()["ercot-data-portal"]
    assert e.live_fetch == "blocked_until_authorized" and e.access_status == "registration_required"


def test_key_sources_declare_credentials_by_env_name_only():
    reg = load_sources()
    assert reg["eia-930"].credential_env == ("EIA_API_KEY",)
    assert reg["openei-urdb"].credential_env == ("OPENEI_API_KEY",)


def test_every_scenario_forbids_an_overclaim():
    assert all(s["must_not_claim"] for s in load_scenarios())


def _mutate(tmp_path, fn):
    doc = json.loads((configs_dir() / "sources.json").read_text())
    fn(doc)
    p = tmp_path / "s.json"
    p.write_text(json.dumps(doc))
    return p


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["sources"].append(copy.deepcopy(d["sources"][0])),
        lambda d: d["sources"][0].pop("limitations"),
        lambda d: d["sources"][0].update(locator="http://emp.lbl.gov/queues"),
        lambda d: d["sources"][0].update(allowed_hosts=["other.example"]),
        lambda d: d["sources"][0].update(live_fetch="enabled"),
        lambda d: d["sources"][0].update(access_status="made_up"),
        lambda d: d["sources"][0].update(unexpected="x"),
    ],
)
def test_bad_registries_rejected(tmp_path, mutation):
    with pytest.raises(RegistryError):
        load_sources(_mutate(tmp_path, mutation))


def test_scenario_with_unknown_source_rejected(tmp_path):
    doc = json.loads((configs_dir() / "scenarios.json").read_text())
    doc["scenarios"][0]["source_ids"] = ["nope"]
    p = tmp_path / "sc.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(RegistryError):
        load_scenarios(p)


SECRET = re.compile(r"(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9]{16,}", re.I)


def test_no_secret_like_strings_in_tracked_content():
    for base in (configs_dir(), fixtures_dir(), ROOT / "src"):
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in {".json", ".py", ".csv", ".md"}:
                assert not SECRET.search(p.read_text()), p


def test_gitignore_excludes_raw_data_credentials_and_generated():
    gi = (ROOT / ".gitignore").read_text()
    for needle in (".env", "data/raw/", "data/generated/", ".venv/"):
        assert needle in gi
