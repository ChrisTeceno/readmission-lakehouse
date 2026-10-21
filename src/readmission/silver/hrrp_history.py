"""silver_hrrp_history: one row per facility, measure and fiscal year, FY2013 to FY2027."""

from datetime import date
from itertools import chain

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

SILVER_HRRP_HISTORY_SCHEMA = T.StructType(
    [
        T.StructField("facility_id", T.StringType(), nullable=False),
        T.StructField("measure", T.StringType(), nullable=False),
        T.StructField("fiscal_year", T.IntegerType(), nullable=False),
        T.StructField("discharges", T.IntegerType()),
        T.StructField("readmissions", T.IntegerType()),
        T.StructField("excess_readmission_ratio", T.DoubleType()),
        T.StructField("predicted_rate", T.DoubleType()),
        T.StructField("expected_rate", T.DoubleType()),
        T.StructField("peer_group", T.IntegerType()),
        T.StructField("peer_median_err", T.DoubleType()),
        T.StructField("period_start", T.DateType()),
        T.StructField("period_end", T.DateType()),
        T.StructField("source", T.StringType(), nullable=False),
    ]
)

# Performance periods. FY2019 to FY2026 follow the PDC table in SPEC.md ("Historical HRRP: how to
# fetch it"); FY2013 to FY2018 and FY2027 come from the variable description file inside each
# IPPS zip. Two COVID exceptions: FY2022 stops at 2019-12-01 (CMS's own end date, also in the
# file), and FY2024 spans 2019-07-01 to 2022-06-30 but excludes January to June 2020, so its
# window holds 2.5 years of discharges. FY2027 is two years, as its variable description states.
HRRP_PERIODS: dict[int, tuple[date, date]] = {
    2013: (date(2008, 7, 1), date(2011, 6, 30)),
    2014: (date(2009, 7, 1), date(2012, 6, 30)),
    2015: (date(2010, 7, 1), date(2013, 6, 30)),
    2016: (date(2011, 7, 1), date(2014, 6, 30)),
    2017: (date(2012, 7, 1), date(2015, 6, 30)),
    2018: (date(2013, 7, 1), date(2016, 6, 30)),
    2019: (date(2014, 7, 1), date(2017, 6, 30)),
    2020: (date(2015, 7, 1), date(2018, 6, 30)),
    2021: (date(2016, 7, 1), date(2019, 6, 30)),
    2022: (date(2017, 7, 1), date(2019, 12, 1)),
    2023: (date(2018, 7, 1), date(2021, 6, 30)),
    2024: (date(2019, 7, 1), date(2022, 6, 30)),
    2025: (date(2020, 7, 1), date(2023, 6, 30)),
    2026: (date(2021, 7, 1), date(2024, 6, 30)),
    2027: (date(2023, 7, 1), date(2025, 6, 30)),
}

# CMS writes suppressed or unavailable values as text; '.' is the IPPS files' missing marker.
SUPPRESSION_CODES = ["N/A", "Not Available", "Too Few to Report", "."]

MEASURES = ["AMI", "HF", "PN", "COPD", "HIP_KNEE", "CABG"]

# IPPS wide headers name the measure in words until FY2018 and by abbreviation in FY2027
# ("Number of Pneumonia Cases", "ERR for pneumonia", "Hip/Knee Arthroplasty ...", "THA/TKA").
IPPS_MEASURE_PATTERNS = [
    ("PN", r"pneumon"),
    ("HF", r"heart failure|\bhf\b"),
    ("AMI", r"myocardial|\bami\b"),
    ("HIP_KNEE", r"hip/knee|tha/tka"),
    ("COPD", r"pulmonary|\bcopd\b"),
    ("CABG", r"bypass|\bcabg\b"),
]
IPPS_FIELD_PATTERNS = [
    ("peer_median_err", r"^peer group median err"),
    ("discharges", r"^number of"),
    ("excess_readmission_ratio", r"excess readmission ratio|^err for"),
]
IPPS_CCN_KEYS = ["PROV", "Provider", "Hospital CCN"]


def suppressed_to_null(name: str) -> Column:
    """Suppression codes and blanks become null; thousands separators are stripped."""
    value = F.trim(F.col(name))
    return F.when(value.isin(SUPPRESSION_CODES) | (value == ""), None).otherwise(
        F.regexp_replace(value, ",", "")
    )


def pad_ccn(column: Column) -> Column:
    """CCNs are 6 characters; spreadsheet round trips drop the leading zero (010001 -> 10001)."""
    return F.lpad(F.trim(column), 6, "0")


def pdc_measure(column: Column) -> Column:
    """READM_30_AMI_HRRP (2019) and READM-30-AMI-HRRP (2020 on) both become AMI."""
    normalized = F.regexp_replace(F.upper(F.trim(column)), "-", "_")
    return F.regexp_extract(normalized, r"^READM_30_(.+)_HRRP$", 1)


def _first_match(column: Column, patterns: list[tuple[str, str]]) -> Column:
    result = F.lit(None).cast("string")
    for label, pattern in reversed(patterns):
        result = F.when(column.rlike(pattern), F.lit(label)).otherwise(result)
    return result


def _with_period(df: DataFrame) -> DataFrame:
    starts = F.create_map(
        *chain.from_iterable((F.lit(y), F.lit(s)) for y, (s, _) in HRRP_PERIODS.items())
    )
    ends = F.create_map(
        *chain.from_iterable((F.lit(y), F.lit(e)) for y, (_, e) in HRRP_PERIODS.items())
    )
    return df.withColumn("period_start", starts[F.col("fiscal_year")]).withColumn(
        "period_end", ends[F.col("fiscal_year")]
    )


def _conform(df: DataFrame) -> DataFrame:
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in SILVER_HRRP_HISTORY_SCHEMA]
    )


def silver_from_pdc(bronze: DataFrame) -> DataFrame:
    """FY2019 snapshots before 2019-10 say `Provider ID`, later ones `Facility ID`."""
    id_columns = [F.col(c) for c in ("facility_id", "provider_id") if c in bronze.columns]
    df = bronze.select(
        pad_ccn(F.coalesce(*id_columns)).alias("facility_id"),
        pdc_measure(F.col("measure_name")).alias("measure"),
        "fiscal_year",
        suppressed_to_null("number_of_discharges").alias("discharges"),
        suppressed_to_null("number_of_readmissions").alias("readmissions"),
        suppressed_to_null("excess_readmission_ratio").alias("excess_readmission_ratio"),
        suppressed_to_null("predicted_readmission_rate").alias("predicted_rate"),
        suppressed_to_null("expected_readmission_rate").alias("expected_rate"),
        F.lit(None).alias("peer_group"),
        F.lit(None).alias("peer_median_err"),
        F.lit("pdc").alias("source"),
    )
    return _conform(_with_period(df))


def silver_from_ipps(bronze: DataFrame) -> DataFrame:
    """Unpivot the wide IPPS layout into one row per measure.

    IPPS files carry no readmission counts and no predicted or expected rates. FY2013 to FY2017
    write an ERR of 0 when a hospital had fewer than 25 cases and CMS computed no ratio; a real
    ERR is always positive, so 0 becomes null. Only FY2027 carries the dual-eligibility peer
    group and the peer group median ERR (peer grouping began in FY2019, after the last IPPS year
    before 2027).
    """
    record = F.col("record")
    long = bronze.select(
        pad_ccn(F.coalesce(*[record[k] for k in IPPS_CCN_KEYS])).alias("facility_id"),
        "fiscal_year",
        record["Peer group assignment"].alias("peer_group_raw"),
        F.explode(record).alias("key", "value"),
    )
    key = F.lower(F.col("key"))
    classified = (
        long.withColumn("measure", _first_match(key, IPPS_MEASURE_PATTERNS))
        .withColumn("field", _first_match(key, IPPS_FIELD_PATTERNS))
        .where(F.col("measure").isNotNull() & F.col("field").isNotNull())
        .withColumn("value", suppressed_to_null("value"))
    )
    wide = (
        classified.groupBy("facility_id", "fiscal_year", "measure", "peer_group_raw")
        .pivot("field", [field for field, _ in IPPS_FIELD_PATTERNS])
        .agg(F.first("value"))
    )
    err = F.col("excess_readmission_ratio").cast("double")
    df = wide.select(
        "facility_id",
        "measure",
        "fiscal_year",
        "discharges",
        F.lit(None).alias("readmissions"),
        F.when(err > 0, err).alias("excess_readmission_ratio"),
        F.lit(None).alias("predicted_rate"),
        F.lit(None).alias("expected_rate"),
        suppressed_to_null("peer_group_raw").alias("peer_group"),
        "peer_median_err",
        F.lit("ipps").alias("source"),
    )
    return _conform(_with_period(df))


def silver_hrrp_history(bronze_pdc: DataFrame, bronze_ipps: DataFrame) -> DataFrame:
    return silver_from_pdc(bronze_pdc).unionByName(silver_from_ipps(bronze_ipps))
