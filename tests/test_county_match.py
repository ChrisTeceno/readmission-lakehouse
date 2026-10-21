from pathlib import Path

import pytest

from readmission.ingest.acs_county import read_census_rows
from readmission.silver.county_match import CountyMatcher, county_key, town_key

# Cut from real ACS responses (2026-10-21): every county in AK, CT, DC, LA, MD, MO, NM, SD, VA
# and PR for 2024; AK, CT, SD, VA and PR for 2011, before the Connecticut planning regions, the
# 2015 renames and the 2019 Valdez-Cordova split.
FIXTURES = Path(__file__).parent / "fixtures" / "acs_county"


@pytest.fixture(scope="module")
def matcher_2024() -> CountyMatcher:
    return CountyMatcher(
        read_census_rows(FIXTURES / "county_2024.json"),
        read_census_rows(FIXTURES / "ct_county_subdivision_2024.json"),
    )


@pytest.fixture(scope="module")
def matcher_2011() -> CountyMatcher:
    return CountyMatcher(read_census_rows(FIXTURES / "county_2011.json"))


def test_county_key_ignores_case_punctuation_type_and_spacing():
    assert county_key("Prince George's County") == county_key("PRINCE GEORGES")
    assert county_key("St. Louis city") == "STLOUISCITY"
    assert county_key("Saint Croix Island") == county_key("ST. CROIX ISLAND")
    assert county_key("Doña Ana County") == county_key("DONA ANA")
    assert county_key("LaSalle Parish") == county_key("LASALLE") == county_key("La Salle")
    assert county_key("Juneau City and Borough") == "JUNEAU"
    assert county_key("Capitol Planning Region") == "CAPITOL"
    assert town_key("Windham town") == town_key("WINDHAM") == "WINDHAM"


# (state, CMS county name, CMS city) -> (FIPS, Census name, method), against 2024 geography.
CASES_2024 = [
    # Saint vs St.
    ("MO", "ST. CHARLES", "ST CHARLES", "29183", "St. Charles County", "exact"),
    ("MO", "STE. GENEVIEVE", "STE GENEVIEVE", "29186", "Ste. Genevieve County", "exact"),
    ("MD", "ST. MARYS", "LEONARDTOWN", "24037", "St. Mary's County", "exact"),
    ("LA", "ST. JOHN BAPTIST", "LAPLACE", "22095", "St. John the Baptist Parish", "exact"),
    # Parishes
    ("LA", "E. BATON ROUGE", "BATON ROUGE", "22033", "East Baton Rouge Parish", "exact"),
    ("LA", "LASALLE", "JENA", "22059", "LaSalle Parish", "exact"),
    ("LA", "DE SOTO", "MANSFIELD", "22031", "De Soto Parish", "exact"),
    ("LA", "JEFFRSON DAVIS", "JENNINGS", "22053", "Jefferson Davis Parish", "alias"),
    # Independent cities next to a county of the same name
    ("MD", "BALTIMORE", "TOWSON", "24005", "Baltimore County", "exact"),
    ("MD", "BALTIMORE CITY", "BALTIMORE", "24510", "Baltimore city", "exact"),
    ("MO", "ST. LOUIS", "CREVE COEUR", "29189", "St. Louis County", "exact"),
    ("MO", "ST. LOUIS CITY", "SAINT LOUIS", "29510", "St. Louis city", "exact"),
    ("VA", "FRANKLIN", "ROCKY MOUNT", "51067", "Franklin County", "exact"),
    ("VA", "FRANKLIN CITY", "FRANKLIN", "51620", "Franklin city", "exact"),
    ("VA", "ROANOKE CITY", "ROANOKE", "51770", "Roanoke city", "exact"),
    ("VA", "JAMES CITY", "WILLIAMSBURG", "51095", "James City County", "exact"),
    # Alaska boroughs, census areas, city and borough, municipality
    ("AK", "ANCHORAGE", "ANCHORAGE", "02020", "Anchorage Municipality", "exact"),
    ("AK", "NOME", "NOME", "02180", "Nome Census Area", "exact"),
    ("AK", "KODIAK ISLAND BOROUGH", "KODIAK", "02150", "Kodiak Island Borough", "exact"),
    ("AK", "WRANGELL CITY AND BOROUGH", "WRANGELL", "02275", "Wrangell City and Borough", "exact"),
    ("AK", "NORTH SLOPE BOROUH", "BARROW", "02185", "North Slope Borough", "alias"),
    ("AK", "VALDEZ-CORDOVA", "VALDEZ", "02063", "Chugach Census Area", "town"),
    # Connecticut planning regions, through the town
    ("CT", "HARTFORD", "WEST HARTFORD", "09110", "Capitol Planning Region", "ct_town"),
    ("CT", "HARTFORD", "BRISTOL", "09140", "Naugatuck Valley Planning Region", "ct_town"),
    (
        "CT",
        "WINDHAM",
        "WILLIMANTIC",
        "09180",
        "Southeastern Connecticut Planning Region",
        "ct_town",
    ),
    ("CT", "TOLLAND", "STAFFORD SPRINGS", "09110", "Capitol Planning Region", "ct_town"),
    # Territories and DC
    ("PR", "SAN JUAN", "SAN JUAN", "72127", "San Juan Municipio", "exact"),
    ("PR", "MAYAGUEZ", "MAYAGUEZ", "72097", "Mayagüez Municipio", "exact"),
    ("GU", "GUAM", "TAMUNING", "66010", "Guam", "island_area"),
    ("VI", "SAINT CROIX ISLAND", "ST CROIX", "78010", "St. Croix Island", "island_area"),
    ("MP", "SAIPAN", "GARAPAN", "69110", "Saipan Municipality", "island_area"),
    ("AS", "EASTERN DISTRICT", "PAGO PAGO", "60010", "Eastern District", "island_area"),
    ("DC", "THE DISTRICT", "WASHINGTON", "11001", "District of Columbia", "alias"),
    # Accents and a typo
    ("NM", "DONA ANA", "LAS CRUCES", "35013", "Doña Ana County", "exact"),
    ("SD", "OGLALA LAKOTA COUNTY", "PINE RIDGE", "46102", "Oglala Lakota County", "exact"),
    ("NM", "BERNALLILO", "ALBUQUERQUE", "35001", "Bernalillo County", "fuzzy"),
]


@pytest.mark.parametrize(("state", "county", "city", "fips", "name", "method"), CASES_2024)
def test_matches_tricky_names_2024(matcher_2024, state, county, city, fips, name, method):
    match = matcher_2024.match(state, county, city)

    assert (match.county_fips, match.county_name, match.method) == (fips, name, method)


CASES_2011 = [
    ("CT", "HARTFORD", "WEST HARTFORD", "09003", "Hartford County", "exact"),
    ("CT", "WINDHAM", "WILLIMANTIC", "09015", "Windham County", "exact"),
    ("AK", "VALDEZ-CORDOVA", "VALDEZ", "02261", "Valdez-Cordova Census Area", "exact"),
    ("SD", "OGLALA LAKOTA COUNTY", "PINE RIDGE", "46113", "Shannon County", "alias"),
    ("VA", "BEDFORD", "BEDFORD", "51019", "Bedford County", "exact"),
]


@pytest.mark.parametrize(("state", "county", "city", "fips", "name", "method"), CASES_2011)
def test_matches_the_geography_of_an_older_vintage(
    matcher_2011, state, county, city, fips, name, method
):
    match = matcher_2011.match(state, county, city)

    assert (match.county_fips, match.county_name, match.method) == (fips, name, method)


@pytest.mark.parametrize(
    ("state", "county", "city"),
    [
        ("ZZ", "ANYWHERE", ""),
        ("VA", "", "RICHMOND"),
        ("LA", "NOWHERE PARISH", "NOWHERE"),
    ],
)
def test_unmatched_when_no_rule_applies(matcher_2024, state, county, city):
    match = matcher_2024.match(state, county, city)

    assert (match.county_fips, match.method) == (None, "unmatched")


def test_fuzzy_never_guesses_between_two_equally_close_counties():
    matcher = CountyMatcher(
        [
            {"NAME": "Lakeview A County, Texas", "state": "48", "county": "001"},
            {"NAME": "Lakeview B County, Texas", "state": "48", "county": "003"},
        ]
    )

    assert matcher.match("TX", "LAKEVIEW C", "").method == "unmatched"
    assert matcher.match("TX", "LAKEVEW A", "").county_fips == "48001"
