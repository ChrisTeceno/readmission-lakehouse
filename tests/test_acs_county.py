import base64
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from databricks.sdk.errors import NotFound
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.bronze import acs_county as bronze
from readmission.bronze.load import load_landed
from readmission.ingest.acs_county import (
    SOURCE,
    VINTAGES,
    MissingCensusKey,
    census_get,
    county_url,
    ct_county_subdivision_url,
    fetch_acs_county,
    parse_census_rows,
    read_census_rows,
    variables,
)
from readmission.ingest.landing import LocalLander, list_manifests
from readmission.quality.expectations import ExpectationFailed
from readmission.secrets import get_secret
from readmission.silver.acs_county import (
    ACS_VINTAGE_BY_FISCAL_YEAR,
    SILVER_HOSPITAL_COUNTY_SCHEMA,
    match_report,
    silver_acs_county,
    silver_hospital_county,
    vintage_for_fiscal_year,
)

# Real ACS responses cut to a few states (see test_county_match.py), recorded 2026-10-21.
FIXTURES = Path(__file__).parent / "fixtures" / "acs_county"
INGESTED_AT = datetime(2026, 10, 21, 23, 0, tzinfo=UTC)
API_KEY = "test-key-0123"


def query(url: httpx.URL | str) -> tuple:
    url = httpx.URL(url)
    return url.path, frozenset((k, v) for k, v in url.params.multi_items() if k != "key")


def census_server(calls: list, keyless_refused: bool = True) -> httpx.MockTransport:
    """Serves the fixtures for 2011 and 2024 the way api.census.gov does, including the
    redirect to missing_key.html for a query without a key."""
    files = {
        query(county_url(2011)): FIXTURES / "county_2011.json",
        query(county_url(2024)): FIXTURES / "county_2024.json",
        query(ct_county_subdivision_url(2011)): FIXTURES / "ct_county_subdivision_2024.json",
        query(ct_county_subdivision_url(2024)): FIXTURES / "ct_county_subdivision_2024.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("missing_key.html"):
            return httpx.Response(200, text="<title>Missing Key</title>")
        key = request.url.params.get("key")
        if key is None and keyless_refused:
            return httpx.Response(302, headers={"Location": "/data/missing_key.html"})
        body = files[query(request.url)].read_bytes()
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


def client(transport: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=transport, follow_redirects=True)


def test_parse_census_rows_keeps_values_as_received():
    rows = read_census_rows(FIXTURES / "county_2024.json")

    adjuntas = next(r for r in rows if r["NAME"] == "Adjuntas Municipio, Puerto Rico")
    assert adjuntas == {
        "NAME": "Adjuntas Municipio, Puerto Rico",
        "DP05_0001E": "17960",
        "DP05_0024PE": "22.8",
        "DP03_0062E": "19549",
        "DP03_0128PE": "60.9",
        "DP03_0099PE": "4.9",
        "DP02_0072PE": None,
        "DP02PR_0072PE": "20.8",
        "state": "72",
        "county": "001",
    }
    de_baca = next(r for r in rows if r["NAME"] == "De Baca County, New Mexico")
    assert de_baca["DP03_0062E"] == "-666666666"


def test_parse_census_rows_rejects_a_ragged_row():
    with pytest.raises(ValueError, match="row has 1 values"):
        parse_census_rows('[["NAME","state"],["x"]]')


def test_variable_codes_follow_the_table_reorders():
    assert variables(2016)["pct_age_65_plus"] == "DP05_0021PE"
    assert variables(2017)["pct_age_65_plus"] == "DP05_0024PE"
    assert variables(2018)["pct_disability"] == "DP02_0071PE"
    assert variables(2019)["pct_disability"] == "DP02_0072PE"
    assert variables(2024)["pct_disability_pr"] == "DP02PR_0072PE"
    with pytest.raises(ValueError):
        variables(2010)


def test_fiscal_years_map_to_the_vintage_nearest_the_performance_period():
    assert ACS_VINTAGE_BY_FISCAL_YEAR == {
        2013: 2011,
        2014: 2012,
        2015: 2013,
        2016: 2014,
        2017: 2015,
        2018: 2016,
        2019: 2017,
        2020: 2018,
        2021: 2019,
        2022: 2020,
        2023: 2021,
        2024: 2022,
        2025: 2023,
        2026: 2024,
        2027: 2024,
    }
    assert set(ACS_VINTAGE_BY_FISCAL_YEAR.values()) == set(VINTAGES)
    # Once the 2026 vintage exists, FY2027 (07/2023 to 06/2025) takes it.
    assert vintage_for_fiscal_year(2027, latest=2026) == 2026


def test_fetch_lands_both_responses_per_vintage_without_the_key(tmp_path):
    calls: list = []
    lander = LocalLander(str(tmp_path))

    landed = fetch_acs_county(
        client(census_server(calls)),
        lander,
        "b1",
        tmp_path,
        vintages=[2011, 2024],
        read_secret=lambda scope, key: API_KEY,
    )

    assert [(e["vintage"], e["name"], e["row_count"]) for e in landed] == [
        (2011, "county_2011.json", 315),
        (2011, "ct_county_subdivision_2011.json", 174),
        (2024, "county_2024.json", 553),
        (2024, "ct_county_subdivision_2024.json", 174),
    ]
    assert all(c.url.params["key"] == API_KEY for c in calls)
    manifests = list_manifests(str(tmp_path), SOURCE)
    assert [m["partition"] for m in manifests] == ["2011", "2024"]
    for path in tmp_path.rglob("*.json"):
        assert API_KEY not in path.read_text()
    assert manifests[1]["files"][0]["variables"]["pct_disability"] == "DP02_0072PE"


def test_fetch_skips_vintages_already_landed(tmp_path):
    lander = LocalLander(str(tmp_path))
    fetch_acs_county(
        client(census_server([])), lander, "b1", tmp_path, [2011], lambda s, k: API_KEY
    )
    calls: list = []

    landed = fetch_acs_county(
        client(census_server(calls)), lander, "b2", tmp_path, [2011, 2024], lambda s, k: API_KEY
    )

    assert {e["vintage"] for e in landed} == {2024}
    assert not any("/2011/" in c.url.path for c in calls)


def test_keyless_fetch_fails_naming_the_secret(tmp_path, capsys):
    with pytest.raises(MissingCensusKey, match="scope 'readmission', key 'census_api_key'"):
        fetch_acs_county(
            client(census_server([])),
            LocalLander(str(tmp_path)),
            "b1",
            tmp_path,
            [2024],
            lambda s, k: None,
        )
    assert "calling the Census API keyless" in capsys.readouterr().out
    assert not list_manifests(str(tmp_path), SOURCE)


def test_keyless_fetch_works_where_census_still_allows_it(tmp_path):
    landed = fetch_acs_county(
        client(census_server([], keyless_refused=False)),
        LocalLander(str(tmp_path)),
        "b1",
        tmp_path,
        [2024],
        lambda s, k: None,
    )

    assert len(landed) == 2


def test_census_get_retries_a_spurious_missing_key_redirect():
    responses = iter([302, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("missing_key.html"):
            return httpx.Response(200, text="Missing Key")
        if next(responses) == 302:
            return httpx.Response(302, headers={"Location": "/data/missing_key.html"})
        return httpx.Response(200, json=[["NAME"]], headers={"content-type": "application/json"})

    response = census_get(client(httpx.MockTransport(handler)), county_url(2024), API_KEY)

    assert response.json() == [["NAME"]]


def test_census_errors_never_carry_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("invalid_key.html"):
            return httpx.Response(200, text="<title>Invalid Key</title>")
        if "bad" in request.url.params.get("key", ""):
            return httpx.Response(302, headers={"Location": "/data/invalid_key.html"})
        return httpx.Response(400, text="error: unknown variable")

    with pytest.raises(RuntimeError) as invalid:
        census_get(client(httpx.MockTransport(handler)), county_url(2024), "bad-key")
    with pytest.raises(RuntimeError) as status:
        census_get(client(httpx.MockTransport(handler)), county_url(2024), API_KEY)

    assert "invalid_key.html" in str(invalid.value) and "bad-key" not in str(invalid.value)
    assert "HTTP 400" in str(status.value) and API_KEY not in str(status.value)


def test_get_secret_decodes_the_value_and_returns_none_when_missing():
    class Secrets:
        def get_secret(self, scope, key):
            if (scope, key) != ("readmission", "census_api_key"):
                raise NotFound("no such secret")
            return SimpleNamespace(value=base64.b64encode(b"abc123").decode())

    workspace = SimpleNamespace(secrets=Secrets())

    assert get_secret("readmission", "census_api_key", workspace) == "abc123"
    assert get_secret("readmission", "other", workspace) is None


@pytest.fixture
def landed(tmp_path):
    fetch_acs_county(
        client(census_server([])),
        LocalLander(str(tmp_path)),
        "b1",
        tmp_path,
        [2011, 2024],
        lambda s, k: API_KEY,
    )
    return tmp_path


@pytest.fixture
def bronze_table(spark, request):
    name = f"bronze_{request.node.name}"
    yield name
    spark.sql(f"DROP TABLE IF EXISTS {name}")


def test_bronze_keeps_each_row_as_a_string_map_and_loads_once(spark, landed, bronze_table):
    appended = load_landed(
        spark, str(landed), SOURCE, bronze_table, bronze.bronze_from_manifest, INGESTED_AT
    )
    again = load_landed(
        spark, str(landed), SOURCE, bronze_table, bronze.bronze_from_manifest, INGESTED_AT
    )

    df = spark.table(bronze_table)
    assert (appended, again) == (315 + 174 + 553 + 174, 0)
    assert df.schema["record"].dataType == T.MapType(T.StringType(), T.StringType())
    counts = {
        (r["vintage"], r["geography"]): r["count"]
        for r in df.groupBy("vintage", "geography").count().collect()
    }
    assert counts == {
        (2011, "county"): 315,
        (2011, "county subdivision"): 174,
        (2024, "county"): 553,
        (2024, "county subdivision"): 174,
    }
    url = df.where("vintage = 2024 AND geography = 'county'").first()["_source"]
    assert url == county_url(2024) and "key=" not in url


def bronze_frame(spark, landed):
    frames = [
        bronze.bronze_from_manifest(spark, m, INGESTED_AT)
        for m in list_manifests(str(landed), SOURCE)
    ]
    return frames[0].unionByName(frames[1])


def test_silver_acs_county_types_measures_and_nulls_annotation_codes(spark, landed):
    df, rejected = silver_acs_county(bronze_frame(spark, landed))
    rows = {(r["county_fips"], r["vintage"]): r.asDict() for r in df.collect()}

    assert (df.count(), rejected.count()) == (315 + 553, 0)
    assert rows["72001", 2024] == {
        "county_fips": "72001",
        "vintage": 2024,
        "county_name": "Adjuntas Municipio",
        "state_fips": "72",
        "population": 17960,
        "median_household_income": 19549,
        "pct_below_poverty": 60.9,
        "pct_uninsured": 4.9,
        "pct_age_65_plus": 22.8,
        # Puerto Rico's disability percent is only in DP02PR.
        "pct_disability": 20.8,
        "period_start": date(2020, 1, 1),
        "period_end": date(2024, 12, 31),
        "_batch_id": "b1",
    }
    # -666666666: too few sample cases for a median.
    assert rows["35011", 2024]["median_household_income"] is None
    shannon = rows["46113", 2011]
    assert (shannon["county_name"], shannon["pct_age_65_plus"]) == ("Shannon County", 5.6)
    # The 2011 vintage has no uninsured or disability estimates (-888888888).
    assert (shannon["pct_uninsured"], shannon["pct_disability"]) == (None, None)


def test_silver_acs_county_keeps_the_latest_batch_per_county_and_vintage(spark, landed):
    once = bronze_frame(spark, landed)
    refetched = once.withColumn("_batch_id", F.lit("b2"))

    df, _ = silver_acs_county(once.unionByName(refetched))

    assert df.count() == df.select("county_fips", "vintage").distinct().count() == 315 + 553
    assert {r["_batch_id"] for r in df.select("_batch_id").distinct().collect()} == {"b2"}


def test_silver_acs_county_quarantines_unparseable_and_out_of_range_values(spark, landed):
    bronze_rows = bronze_frame(spark, landed).where("vintage = 2024 AND geography = 'county'")
    record = F.col("record")

    def replace(state: str, county: str, code: str, value: str):
        target = (record["state"] == state) & (record["county"] == county)
        return F.transform_values(
            record, lambda k, v: F.when(target & (k == code), value).otherwise(v)
        )

    broken = bronze_rows.withColumn("record", replace("72", "001", "DP05_0001E", "n/a")).withColumn(
        "record", replace("02", "013", "DP03_0128PE", "130.0")
    )

    df, rejected = silver_acs_county(broken)

    assert df.count() == 553 - 2
    assert {(r["business_key"], r["rule"]) for r in rejected.collect()} == {
        ("72001|2024", "unparseable_population"),
        ("02013|2024", "acs_percent_range"),
    }


HOSPITALS = [
    ("070002", "CT", "WINDHAM", "WILLIMANTIC", "b1"),
    ("490040", "VA", "ALEXANDRIA CITY", "ALEXANDRIA", "b1"),
    ("020017", "AK", "VALDEZ-CORDOVA", "VALDEZ", "b1"),
    ("650001", "GU", "GUAM", "TAMUNING", "b1"),
]
HOSPITAL_INFO_SCHEMA = (
    "facility_id string, state string, county string, city string, _batch_id string"
)


def test_silver_hospital_county_matches_each_vintage_geography(spark, landed):
    hospital_info = spark.createDataFrame(HOSPITALS, HOSPITAL_INFO_SCHEMA)

    df, rejected = silver_hospital_county(hospital_info, bronze_frame(spark, landed))
    rows = {(r["facility_id"], r["vintage"]): r for r in df.collect()}

    assert (df.count(), rejected.count()) == (len(HOSPITALS) * 2, 0)
    assert (rows["070002", 2011]["county_fips"], rows["070002", 2024]["county_fips"]) == (
        "09015",
        "09180",
    )
    assert rows["020017", 2011]["county_name"] == "Valdez-Cordova Census Area"
    assert rows["020017", 2024]["county_name"] == "Chugach Census Area"
    assert rows["490040", 2011]["county_fips"] == rows["490040", 2024]["county_fips"] == "51510"
    assert rows["650001", 2024]["match_method"] == "island_area"
    assert match_report(df) == [
        "silver_hospital_county vintage 2011: 4 of 4 hospitals matched (100.00%)",
        "silver_hospital_county vintage 2024: 4 of 4 hospitals matched (100.00%)",
    ]


def test_silver_hospital_county_fails_below_the_match_rate(spark, landed):
    # Alabama is not in the fixture's counties, so this hospital cannot match.
    unmatched = [("010001", "AL", "HOUSTON", "DOTHAN", "b1")]
    hospital_info = spark.createDataFrame(HOSPITALS + unmatched, HOSPITAL_INFO_SCHEMA)

    with pytest.raises(ExpectationFailed, match="4 of 5 hospitals matched a county"):
        silver_hospital_county(hospital_info, bronze_frame(spark, landed))


def test_match_report_lists_unmatched_hospitals(spark):
    rows = [
        ("010001", 2024, "AL", "HOUSTON", "DOTHAN", None, None, "unmatched", "b1"),
        ("010005", 2024, "AL", "MARSHALL", "BOAZ", "01095", "Marshall County", "exact", "b1"),
    ]
    df = spark.createDataFrame(rows, SILVER_HOSPITAL_COUNTY_SCHEMA)

    assert match_report(df) == [
        "silver_hospital_county vintage 2024: 1 of 2 hospitals matched (50.00%)",
        "  unmatched: 2024 010001 AL 'HOUSTON'",
    ]


def test_manifest_records_the_query_not_the_key(landed):
    manifest = json.loads(next(landed.rglob("_manifest.json")).read_text())

    assert {f["geography"] for f in manifest["files"]} == {"county", "county subdivision"}
    assert all("key" not in f["url"] for f in manifest["files"])


LABELS = {
    "population": r"SEX AND AGE!!Total population$",
    "pct_age_65_plus": r"^Percent.*SEX AND AGE!!(Total population!!)?65 years and over$",
    "median_household_income": r"Median household income \(dollars\)$",
    "pct_below_poverty": r"^Percent.*BELOW THE POVERTY LEVEL!!All people$",
    "pct_uninsured": r"^Percent.*HEALTH INSURANCE COVERAGE!!.*No health insurance coverage$",
    "pct_disability": r"^Percent.*DISABILITY STATUS.*!!With a disability$",
    "pct_disability_pr": r"^Percent.*DISABILITY STATUS.*!!With a disability$",
}


@pytest.mark.live
@pytest.mark.parametrize("vintage", VINTAGES)
def test_variable_codes_match_their_labels(vintage):
    """variables.json needs no key. Run with -m live."""
    url = f"https://api.census.gov/data/{vintage}/acs/acs5/profile/variables.json"
    catalog = httpx.get(url, timeout=60).json()["variables"]

    for measure, code in variables(vintage).items():
        assert re.search(LABELS[measure], catalog[code]["label"]), (measure, code)
