import pytest

from gridforge_stress.fixtures import validate_fixtures
from gridforge_stress.paths import fixtures_dir
from gridforge_stress.price import (
    PriceContextError,
    PriceTableError,
    build_context,
    load_price_context,
    parse_table_5_6_a,
)

SECTORS = ["Residential", "Commercial", "Industrial", "Transportation", "All Sectors"]


def page(rows, *, cur="August 2026", prior="August 2025", title="Table 5.6.A. Average Price"):
    head = "<tr><td>&nbsp;</td>" + "".join(f'<th colspan="2">{s}</th>' for s in SECTORS) + "</tr>"
    per = "<tr><th>Census Divisionand State</th>" + f"<th>{cur}</th><th>{prior}</th>" * 5 + "</tr>"
    body = "".join(
        "<tr><td>" + n + "</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        for n, cells in rows
    )
    return f"<html><body>{title}<table>{head}{per}{body}</table>Release Date: October 23, 2026</body></html>"


def states(n=60):
    rows = [(f"State{i}", ["10.0", "9.0"] * 5) for i in range(n)]
    rows.append(
        (
            "U.S. Total",
            [
                "18.16",
                "17.57",
                "14.47",
                "13.87",
                "9.64",
                "8.98",
                "14.86",
                "14.91",
                "14.87",
                "14.15",
            ],
        )
    )
    return rows


def test_parses_by_heading_and_keeps_unpublished_as_none():
    rows = states()
    rows[0] = (
        "Connecticut",
        ["24.51", "30.17", "19.29", "23.47", "16.18", "18.91", "NM", "18.67", "--", "26.42"],
    )
    d = parse_table_5_6_a(page(rows))
    assert d["period"] == "2026-08" and d["prior_period"] == "2025-08"
    assert d["release_date"] == "October 23, 2026"
    assert d["rows"]["U.S. Total"]["industrial"] == {"current": 9.64, "prior": 8.98}
    assert d["rows"]["Connecticut"]["transportation"]["current"] is None
    assert d["rows"]["Connecticut"]["all_sectors"]["current"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.replace("Table 5.6.A", "Table 5.1"),
        lambda p: p.replace("Industrial", "Industrial plant"),
        lambda p: p.replace("August 2025", "August 2024"),
        lambda p: p.replace("<td>9.64</td>", "<td>9,6x</td>"),
        lambda p: p.replace("<td>9.64</td>", "<td>0</td>"),
        lambda p: p.replace("<td>9.64</td>", "<td>96400</td>"),
    ],
)
def test_refuses_a_page_it_does_not_understand(mutate):
    with pytest.raises(PriceTableError):
        parse_table_5_6_a(mutate(page(states())))


def test_refuses_a_table_missing_the_us_total_or_states():
    with pytest.raises(PriceTableError):
        parse_table_5_6_a(page(states()[:5]))


def test_context_keeps_only_data_centre_billing_sectors():
    d = parse_table_5_6_a(page(states()))
    doc = build_context(d, provenance={"measured_on": "2026-10-30"})
    assert set(doc["rows"]["U.S. Total"]) == {"commercial", "industrial"}
    assert "NOT the tariff" in doc["definition"]


def test_shipped_fixture_loads_and_matches_the_published_us_total():
    doc = load_price_context(fixtures_dir() / "eia_retail_price_context.json")
    assert doc["period"] == "2026-08"
    assert doc["rows"]["U.S. Total"]["industrial"]["current"] == 9.64
    assert doc["rows"]["U.S. Total"]["commercial"]["current"] == 14.47
    assert len(doc["rows"]) == 62


def test_loader_refuses_an_implausible_price(tmp_path):
    import json

    doc = json.loads((fixtures_dir() / "eia_retail_price_context.json").read_text())
    doc["rows"]["Texas"]["industrial"]["current"] = -1
    p = tmp_path / "x.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(PriceContextError):
        load_price_context(p)


def test_fixture_directory_still_validates():
    assert not [f for f in validate_fixtures(fixtures_dir()) if f.code == "bad_price_context"]


# --- corruption table: every row must be refused, and a pristine copy must still load (the loader is not simply refusing everything) ---
def _pristine():
    import json

    return json.loads((fixtures_dir() / "eia_retail_price_context.json").read_text())


def _set(path, value):
    def mutate(d):
        cur = d
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = value

    return mutate


def _drop(path):
    def mutate(d):
        cur = d
        for k in path[:-1]:
            cur = cur[k]
        del cur[path[-1]]

    return mutate


CORRUPTIONS = {
    "unit_swapped_to_per_MWh": _set(["unit"], "US dollars per MWh"),
    "wrong_source_id": _set(["source_id"], "eia-930"),
    "evidence_kind_promoted_to_tariff": _set(["evidence_kind"], "tariff"),
    "period_not_a_month": _set(["period"], "2026-13"),
    "prior_period_after_period": _set(["prior_period"], "2027-08"),
    "prior_period_equals_period": _set(["prior_period"], "2026-08"),
    "prior_period_not_twelve_months": _set(["prior_period"], "2026-07"),
    "page_hash_not_hex": _set(["provenance", "page_sha256"], "zz"),
    "page_hash_removed": _drop(["provenance", "page_sha256"]),
    "page_bytes_zero": _set(["provenance", "page_bytes"], 0),
    "page_bytes_bool": _set(["provenance", "page_bytes"], True),
    "price_is_bool": _set(["rows", "Texas", "industrial", "current"], True),
    "price_is_string": _set(["rows", "Texas", "industrial", "current"], "7.21"),
    "price_times_ten": _set(["rows", "U.S. Total", "industrial", "current"], 96.4),
    "us_total_outside_states": _set(["rows", "U.S. Total", "commercial", "current"], 5.0),
    "missing_sector": _drop(["rows", "Texas", "industrial"]),
    "missing_value_key": _drop(["rows", "Texas", "commercial", "prior"]),
    "sector_is_null": _set(["rows", "Texas", "commercial"], None),
    "rows_dropped_below_fifty": lambda d: d.__setitem__(
        "rows", dict(list(d["rows"].items())[:10] + [("U.S. Total", d["rows"]["U.S. Total"])])
    ),
    "us_total_removed": _drop(["rows", "U.S. Total"]),
    "rows_not_an_object": _set(["rows"], []),
}


def test_pristine_fixture_loads():
    import json

    p = fixtures_dir() / "eia_retail_price_context.json"
    assert json.loads(p.read_text())["rows"]["Texas"]["industrial"]["current"] == 7.21
    load_price_context(p)


@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_loader_refuses_each_corruption(tmp_path, name):
    import json

    doc = _pristine()
    CORRUPTIONS[name](doc)
    p = tmp_path / "x.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(PriceContextError):
        load_price_context(p)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_loader_refuses_non_finite_prices(tmp_path, literal):
    text = (fixtures_dir() / "eia_retail_price_context.json").read_text()
    text = text.replace('"current": 7.21', f'"current": {literal}', 1)
    p = tmp_path / "x.json"
    p.write_text(text)
    with pytest.raises(PriceContextError):
        load_price_context(p)


def test_loader_refuses_a_duplicate_state_key(tmp_path):
    text = (fixtures_dir() / "eia_retail_price_context.json").read_text()
    # repeat the Texas key: the later value would silently win in a plain json.loads
    text = text.replace(
        '"Texas":',
        '"Texas": {"commercial": {"current": 1.0, "prior": 1.0}, "industrial": {"current": 1.0, "prior": 1.0}}, "Texas":',
        1,
    )
    p = tmp_path / "x.json"
    p.write_text(text)
    with pytest.raises(PriceContextError, match="duplicate key"):
        load_price_context(p)


def test_loader_refuses_non_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{not json")
    with pytest.raises(PriceContextError):
        load_price_context(p)
