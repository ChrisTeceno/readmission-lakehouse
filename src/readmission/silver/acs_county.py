"""silver_acs_county and silver_hospital_county: ACS county measures and each hospital's county.

- `silver_acs_county`: one row per county FIPS and ACS 5-year vintage, typed.
- `silver_hospital_county`: one row per hospital and ACS vintage, with the county FIPS that
  vintage's geography gives it (`readmission.silver.county_match`). Joining per vintage keeps
  Connecticut right: its hospitals sit in the old counties up to the 2021 vintage and in
  planning regions from 2022. A hospital no rule matches stays in the table with a null FIPS
  and `match_method` `unmatched`; the build prints them and fails if the latest vintage matches
  too few hospitals (`readmission.quality.expectations.MIN_COUNTY_MATCH_RATE`).

A fiscal year's features come from the vintage whose survey midpoint is nearest the midpoint of
its HRRP performance period (`ACS_VINTAGE_BY_FISCAL_YEAR`).
"""

from datetime import date

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.ingest.acs_county import LATEST_VINTAGE, VINTAGES, variables
from readmission.quality.constraints import constraint_rules
from readmission.quality.expectations import check_match_rate
from readmission.silver.county_match import CountyMatcher
from readmission.silver.governance import TableDoc, field, silver_tags
from readmission.silver.hrrp_history import HRRP_PERIODS
from readmission.silver.normalize import latest_per_key, try_cast
from readmission.silver.quarantine import first_violation, missing_rules, raw_row, split_rows

TABLE = "silver_acs_county"
KEY = ["county_fips", "vintage"]
CONSTRAINTS = ["county_fips_format", "acs_percent_range", "period_order"]

HOSPITAL_COUNTY_TABLE = "silver_hospital_county"
HOSPITAL_COUNTY_KEY = ["facility_id", "vintage"]
HOSPITAL_COUNTY_CONSTRAINTS = ["ccn_format", "county_fips_format"]

SILVER_ACS_COUNTY_SCHEMA = T.StructType(
    [
        field("county_fips", T.StringType(), "County FIPS code, state plus county", nullable=False),
        field(
            "vintage",
            T.IntegerType(),
            "ACS 5-year vintage, the last of its five survey years",
            nullable=False,
        ),
        field("county_name", T.StringType(), "County name as Census writes it in that vintage"),
        field("state_fips", T.StringType(), "Two-digit state FIPS code"),
        field("population", T.IntegerType(), "Total population"),
        field(
            "median_household_income",
            T.IntegerType(),
            "Median household income in dollars of the vintage year",
        ),
        field("pct_below_poverty", T.DoubleType(), "Percent of people below the poverty level"),
        field(
            "pct_uninsured",
            T.DoubleType(),
            "Percent of the civilian noninstitutionalized population with no health insurance; "
            "null in the 2011 vintage",
        ),
        field("pct_age_65_plus", T.DoubleType(), "Percent of the population aged 65 and over"),
        field(
            "pct_disability",
            T.DoubleType(),
            "Percent of the civilian noninstitutionalized population with a disability; null in "
            "the 2011 vintage",
        ),
        field("period_start", T.DateType(), "First day of the five survey years"),
        field("period_end", T.DateType(), "Last day of the five survey years"),
        field("_batch_id", T.StringType(), "Bronze batch the row came from", nullable=False),
    ]
)

SILVER_ACS_COUNTY_DOC = TableDoc(
    comment=(
        "Census ACS 5-year county socioeconomics, one row per county and vintage, 2011 to 2024. "
        "Source: api.census.gov data profile tables via bronze_acs_county."
    ),
    tags=silver_tags("census", dataset="acs5-profile"),
)

SILVER_HOSPITAL_COUNTY_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field("vintage", T.IntegerType(), "ACS vintage whose geography was used", nullable=False),
        field("state", T.StringType(), "Two-letter state code"),
        field("county", T.StringType(), "County or parish as CMS writes it"),
        field("city", T.StringType(), "City or town as CMS writes it"),
        field("county_fips", T.StringType(), "Matched county FIPS code; null when unmatched"),
        field("county_name", T.StringType(), "Matched county name as Census writes it"),
        field(
            "match_method",
            T.StringType(),
            "exact, alias, ct_town, town, island_area, fuzzy or unmatched",
            nullable=False,
        ),
        field("_batch_id", T.StringType(), "silver_hospital_info batch", nullable=False),
    ]
)

SILVER_HOSPITAL_COUNTY_DOC = TableDoc(
    comment=(
        "Each hospital's county FIPS code in each ACS vintage's geography, for joining "
        "silver_acs_county. Source: silver_hospital_info and the county names in "
        "bronze_acs_county."
    ),
    tags=silver_tags("cms,census", dataset="xubh-q36u,acs5-profile"),
)

MEASURE_TYPES = {
    "population": "int",
    "median_household_income": "int",
    "pct_below_poverty": "double",
    "pct_uninsured": "double",
    "pct_age_65_plus": "double",
    "pct_disability": "double",
    "pct_disability_pr": "double",
}


def acs_midpoint(vintage: int) -> date:
    """A 5-year vintage covers January of vintage - 4 to December of vintage."""
    return date(vintage - 2, 7, 2)


def vintage_for_fiscal_year(
    fiscal_year: int,
    periods: dict[int, tuple[date, date]] = HRRP_PERIODS,
    latest: int = LATEST_VINTAGE,
) -> int:
    """The vintage whose midpoint is nearest the performance period's midpoint, earlier on a
    tie, capped at the latest published vintage. A 3-year period ending June 30 has its
    midpoint near December 31 two and a half years before, so FY N maps to vintage N - 2.
    FY2026 and FY2027 both take the latest vintage until the 2025 and 2026 ones are out."""
    start, end = periods[fiscal_year]
    midpoint = start + (end - start) / 2
    candidates = range(min(VINTAGES), fiscal_year + 1)
    best = min(candidates, key=lambda v: (abs((acs_midpoint(v) - midpoint).days), v))
    return min(best, latest)


ACS_VINTAGE_BY_FISCAL_YEAR = {fy: vintage_for_fiscal_year(fy) for fy in HRRP_PERIODS}


def received_measure(name: str) -> Column:
    """The measure as received, from whichever variable code its vintage uses.

    Census writes annotation codes in the estimate itself, as large negatives: -666666666 (too
    few sample cases), -888888888 (not applicable, as for uninsured and disability in every
    county of the 2011 vintage), -999999999, -222222222 and so on. None of these measures can be
    negative, so a negative is an annotation and becomes null rather than a parse failure.
    """
    vintage, record = F.col("vintage"), F.col("record")
    value = F.trim(
        F.coalesce(*[F.when(vintage == v, record[variables(v)[name]]) for v in VINTAGES])
    )
    return F.when(~value.startswith("-"), value)


def silver_acs_county(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_acs_county rows, quarantine rows)."""
    record = F.col("record")
    df = (
        bronze.where(F.col("geography") == "county")
        .withColumn("_raw", raw_row(bronze))
        .withColumn("county_fips", F.concat(record["state"], record["county"]))
        .withColumn("county_name", F.trim(F.split(record["NAME"], ",")[0]))
        .withColumn("state_fips", record["state"])
    )
    parse_rules = []
    for name, data_type in MEASURE_TYPES.items():
        received = f"_received_{name}"
        df = df.withColumn(received, received_measure(name)).withColumn(
            name, try_cast(received, data_type)
        )
        parse_rules.append(
            (f"unparseable_{name}", F.col(received).isNotNull() & F.col(name).isNull())
        )
    df = (
        # Puerto Rico's disability percent is only in the DP02PR table.
        df.withColumn("pct_disability", F.coalesce("pct_disability", "pct_disability_pr"))
        .withColumn("period_start", F.make_date(F.col("vintage") - 4, F.lit(1), F.lit(1)))
        .withColumn("period_end", F.make_date(F.col("vintage"), F.lit(12), F.lit(31)))
    )
    rules = [*missing_rules(KEY), *parse_rules, *constraint_rules(CONSTRAINTS)]
    checked = df.withColumn("_violation", first_violation(rules))
    return split_rows(checked, table=TABLE, schema=SILVER_ACS_COUNTY_SCHEMA, key=KEY)


def match_hospitals(hospitals: list[dict], geography: list[dict]) -> list[tuple]:
    """silver_hospital_county rows: every hospital matched in every vintage present."""
    by_vintage: dict[int, tuple[list, list]] = {}
    for row in geography:
        counties, towns = by_vintage.setdefault(row["vintage"], ([], []))
        (counties if row["geography"] == "county" else towns).append(row["record"])
    rows = []
    for vintage, (counties, towns) in sorted(by_vintage.items()):
        matcher = CountyMatcher(counties, towns)
        for h in hospitals:
            m = matcher.match(h["state"] or "", h["county"] or "", h["city"] or "")
            rows.append(
                (
                    h["facility_id"],
                    vintage,
                    h["state"],
                    h["county"],
                    h["city"],
                    m.county_fips,
                    m.county_name,
                    m.method,
                    h["_batch_id"],
                )
            )
    return rows


def silver_hospital_county(
    hospital_info: DataFrame, bronze_acs: DataFrame
) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_hospital_county rows, quarantine rows). `hospital_info` is
    silver_hospital_info. Both sides are small (about 5,400 hospitals and 3,400 geography rows
    per vintage), so they are matched on the driver in plain Python."""
    columns = ["facility_id", "state", "county", "city", "_batch_id"]
    hospitals = [r.asDict() for r in hospital_info.select(*columns).collect()]
    record = F.col("record")
    geo_id = F.concat_ws("", record["state"], record["county"], record["county subdivision"])
    latest_geography = latest_per_key(
        bronze_acs.withColumn("_geo_id", geo_id), ["geography", "vintage", "_geo_id"]
    )
    geography = [
        r.asDict() for r in latest_geography.select("vintage", "geography", "record").collect()
    ]
    rows = match_hospitals(hospitals, geography)
    if rows:
        latest = [r for r in rows if r[1] == max(r[1] for r in rows)]
        matched = sum(1 for r in latest if r[5] is not None)
        check_match_rate(f"{HOSPITAL_COUNTY_TABLE} vintage {latest[0][1]}", matched, len(latest))
    df = bronze_acs.sparkSession.createDataFrame(rows, SILVER_HOSPITAL_COUNTY_SCHEMA)
    rules = [*missing_rules(HOSPITAL_COUNTY_KEY), *constraint_rules(HOSPITAL_COUNTY_CONSTRAINTS)]
    checked = df.withColumn("_raw", raw_row(df)).withColumn("_violation", first_violation(rules))
    return split_rows(
        checked,
        table=HOSPITAL_COUNTY_TABLE,
        schema=SILVER_HOSPITAL_COUNTY_SCHEMA,
        key=HOSPITAL_COUNTY_KEY,
    )


def match_report(hospital_county: DataFrame) -> list[str]:
    """Match rate per vintage and the hospitals left unmatched, for the job log."""
    counts = (
        hospital_county.groupBy("vintage")
        .agg(F.count(F.lit(1)).alias("total"), F.count("county_fips").alias("matched"))
        .orderBy("vintage")
        .collect()
    )
    lines = [
        f"{HOSPITAL_COUNTY_TABLE} vintage {r['vintage']}: {r['matched']} of {r['total']} "
        f"hospitals matched ({r['matched'] / r['total']:.2%})"
        for r in counts
    ]
    unmatched = hospital_county.where(F.col("county_fips").isNull())
    for r in unmatched.orderBy("vintage", "facility_id").collect():
        lines.append(f"  unmatched: {r['vintage']} {r['facility_id']} {r['state']} {r['county']!r}")
    return lines
