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
