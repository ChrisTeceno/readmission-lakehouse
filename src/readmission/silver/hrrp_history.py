"""silver_hrrp_history: one row per facility, measure and fiscal year, FY2013 to FY2027."""

from datetime import date
from itertools import chain

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.quality.constraints import constraint_rules
from readmission.silver.governance import TableDoc, field, silver_tags
from readmission.silver.normalize import canonical_measure, pad_ccn, suppressed_to_null, try_cast
from readmission.silver.quarantine import first_violation, missing_rules, split_rows

TABLE = "silver_hrrp_history"
KEY = ["facility_id", "measure", "fiscal_year"]
CONSTRAINTS = ["ccn_format", "err_positive", "period_order"]

SILVER_HRRP_HISTORY_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field(
            "measure",
            T.StringType(),
            "HRRP condition: AMI, HF, PN, COPD, HIP_KNEE or CABG",
            nullable=False,
        ),
        field("fiscal_year", T.IntegerType(), "HRRP payment fiscal year", nullable=False),
        field(
            "discharges", T.IntegerType(), "Eligible discharges in the period; null if suppressed"
        ),
        field(
            "readmissions",
            T.IntegerType(),
            "Unplanned readmissions within 30 days; null if suppressed and for IPPS years",
        ),
        field(
            "excess_readmission_ratio",
            T.DoubleType(),
            "Predicted over expected readmission rate (ratio); null when CMS computed none",
        ),
        field(
            "predicted_rate",
            T.DoubleType(),
            "Risk-adjusted predicted readmission rate, percent; null for IPPS years",
        ),
        field(
            "expected_rate",
            T.DoubleType(),
            "Risk-adjusted expected readmission rate, percent; null for IPPS years",
        ),
        field(
            "peer_group",
            T.IntegerType(),
            "Dual-eligibility peer group quintile, 1 to 5; FY2027 only",
        ),
        field(
            "peer_median_err",
            T.DoubleType(),
            "Median ERR of the hospital's peer group for the measure (ratio); FY2027 only",
        ),
        field("period_start", T.DateType(), "First discharge date of the performance period"),
        field("period_end", T.DateType(), "Last discharge date of the performance period"),
        field(
            "source",
            T.StringType(),
            "pdc (Provider Data Catalog archive) or ipps (CMS.gov IPPS supplemental file)",
            nullable=False,
        ),
    ]
)

SILVER_HRRP_HISTORY_DOC = TableDoc(
    comment=(
        "HRRP results by fiscal year, FY2013 to FY2027, one row per facility, condition and "
        "fiscal year. Sources: Provider Data Catalog archive snapshots (FY2019 to FY2026) and "
        "CMS.gov IPPS final rule supplemental files (FY2013 to FY2018, FY2027) via "
        "bronze_hrrp_history_pdc and bronze_hrrp_history_ipps."
    ),
    tags=silver_tags("cms", dataset="9n3s-kdb3,ipps-supplemental"),
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
        *[
            try_cast(f.name, f.dataType.simpleString()).alias(f.name)
            for f in SILVER_HRRP_HISTORY_SCHEMA
        ]
    )


def silver_from_pdc(bronze: DataFrame) -> DataFrame:
    """FY2019 snapshots before 2019-10 say `Provider ID`, later ones `Facility ID`."""
    id_columns = [F.col(c) for c in ("facility_id", "provider_id") if c in bronze.columns]
    df = bronze.select(
        pad_ccn(F.coalesce(*id_columns)).alias("facility_id"),
        canonical_measure(F.col("measure_name")).alias("measure"),
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


def latest_batch_per_year(bronze: DataFrame) -> DataFrame:
    """Each fiscal year's rows from its most recent batch. A fiscal year lands again only when
    CMS changes its file, and the new file replaces the old one."""
    latest = F.max("_batch_id").over(Window.partitionBy("fiscal_year"))
    return (
        bronze.withColumn("_latest", latest)
        .where(F.col("_batch_id") == F.col("_latest"))
        .drop("_latest")
    )


def silver_hrrp_history_checked(
    bronze_pdc: DataFrame, bronze_ipps: DataFrame
) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_hrrp_history rows, quarantine rows).

    IPPS rows are unpivoted from one wide row per hospital, so a rejected row's `raw_row` is the
    normalized history row as JSON rather than a single Bronze row.
    """
    pdc = latest_batch_per_year(bronze_pdc)
    ipps = latest_batch_per_year(bronze_ipps)
    batches = pdc.select("fiscal_year", "_batch_id").union(ipps.select("fiscal_year", "_batch_id"))
    df = silver_hrrp_history(pdc, ipps)
    df = df.withColumn("_raw", F.to_json(F.struct(*df.columns))).join(
        batches.distinct(), "fiscal_year", "left"
    )
    rules = [
        *missing_rules(["facility_id"]),
        ("unknown_measure", F.col("measure").isNull()),
        *constraint_rules(CONSTRAINTS),
    ]
    checked = df.withColumn("_violation", first_violation(rules))
    return split_rows(checked, table=TABLE, schema=SILVER_HRRP_HISTORY_SCHEMA, key=KEY)
