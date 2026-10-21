"""silver_hrrp: the current HRRP cycle, one row per facility and measure."""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.quality.constraints import constraint_rules
from readmission.silver.governance import TableDoc, field, silver_tags
from readmission.silver.normalize import canonical_measure, pad_ccn
from readmission.silver.quarantine import (
    first_violation,
    missing_rules,
    raw_row,
    split_rows,
    with_parsed,
)

TABLE = "silver_hrrp"
KEY = ["facility_id", "measure"]
CONSTRAINTS = ["ccn_format", "err_positive", "period_order"]

SILVER_HRRP_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field("facility_name", T.StringType(), "Hospital name as CMS publishes it"),
        field("state", T.StringType(), "Two-letter state code"),
        field(
            "measure",
            T.StringType(),
            "HRRP condition: AMI, HF, PN, COPD, HIP_KNEE or CABG",
            nullable=False,
        ),
        field(
            "discharges", T.IntegerType(), "Eligible discharges in the period; null if suppressed"
        ),
        field(
            "readmissions",
            T.IntegerType(),
            "Unplanned readmissions within 30 days; null if CMS reports Too Few to Report",
        ),
        field(
            "excess_readmission_ratio",
            T.DoubleType(),
            "Predicted over expected readmission rate (ratio, 1.0 is the national expectation); "
            "null when CMS computed none",
        ),
        field(
            "predicted_rate", T.DoubleType(), "Risk-adjusted predicted readmission rate, percent"
        ),
        field("expected_rate", T.DoubleType(), "Risk-adjusted expected readmission rate, percent"),
        field("footnote", T.StringType(), "CMS footnote codes for the row"),
        field("period_start", T.DateType(), "First discharge date of the performance period"),
        field("period_end", T.DateType(), "Last discharge date of the performance period"),
        field("_batch_id", T.StringType(), "Bronze batch the row came from", nullable=False),
    ]
)

SILVER_HRRP_DOC = TableDoc(
    comment=(
        "Hospital Readmissions Reduction Program, current fiscal year, one row per facility and "
        "condition. Source: CMS Provider Data dataset 9n3s-kdb3 via bronze_hrrp."
    ),
    tags=silver_tags("cms", dataset="9n3s-kdb3"),
)

NUMERIC_COLUMNS = [
    ("discharges", "number_of_discharges", "int"),
    ("readmissions", "number_of_readmissions", "int"),
    ("excess_readmission_ratio", "excess_readmission_ratio", "double"),
    ("predicted_rate", "predicted_readmission_rate", "double"),
    ("expected_rate", "expected_readmission_rate", "double"),
    ("period_start", "start_date", "date"),
    ("period_end", "end_date", "date"),
]


def silver_hrrp(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_hrrp rows, quarantine rows)."""
    df = bronze.withColumn("_raw", raw_row(bronze))
    df, parse_rules = with_parsed(df, NUMERIC_COLUMNS)
    df = (
        df.withColumn("facility_id", pad_ccn(F.col("facility_id")))
        .withColumn("facility_name", F.trim("facility_name"))
        .withColumn("state", F.trim("state"))
        .withColumn("measure", canonical_measure(F.col("measure_name")))
        .withColumn("footnote", F.nullif(F.trim("footnote"), F.lit("")))
    )
    rules = [
        *missing_rules(["facility_id"]),
        ("unknown_measure", F.col("measure").isNull()),
        *parse_rules,
        *constraint_rules(CONSTRAINTS),
    ]
    checked = df.withColumn("_violation", first_violation(rules))
    return split_rows(
        checked,
        table=TABLE,
        schema=SILVER_HRRP_SCHEMA,
        key=KEY,
        raw_key=["facility_id", "measure_name"],
    )
