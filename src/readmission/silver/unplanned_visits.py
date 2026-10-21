"""silver_unplanned_visits: CMS Unplanned Hospital Visits, one row per facility and measure.

The dataset mixes 30-day readmission rates (READM_30_*), excess days in acute care (EDAC_30_*,
days per 100 discharges, negative when below expected) and outpatient return visits (OP_*).
`measure` maps the readmission rates to the HRRP condition names and is null for the rest.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.quality.constraints import constraint_rules
from readmission.silver.governance import TableDoc, field, silver_tags
from readmission.silver.normalize import canonical_measure, pad_ccn, suppressed_to_null
from readmission.silver.quarantine import (
    first_violation,
    missing_rules,
    raw_row,
    split_rows,
    with_parsed,
)

TABLE = "silver_unplanned_visits"
KEY = ["facility_id", "measure_id"]
CONSTRAINTS = ["ccn_format", "period_order"]

SILVER_UNPLANNED_VISITS_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field(
            "measure_id",
            T.StringType(),
            "CMS measure id, for example READM_30_HF, EDAC_30_AMI or OP_32",
            nullable=False,
        ),
        field(
            "measure",
            T.StringType(),
            "HRRP condition for READM_30_* measures (AMI, HF, PN, COPD, HIP_KNEE, CABG); "
            "null for other measures",
        ),
        field("measure_name", T.StringType(), "Measure description as CMS publishes it"),
        field(
            "compared_to_national",
            T.StringType(),
            "CMS comparison to the national rate or expectation; null if not available",
        ),
        field("denominator", T.IntegerType(), "Eligible discharges or patients in the measure"),
        field(
            "score",
            T.DoubleType(),
            "Measure value: percent for readmission and visit rates, days per 100 discharges "
            "for EDAC measures",
        ),
        field(
            "lower_estimate", T.DoubleType(), "Lower bound of the interval estimate, score units"
        ),
        field(
            "higher_estimate", T.DoubleType(), "Upper bound of the interval estimate, score units"
        ),
        field(
            "number_of_patients", T.IntegerType(), "Patients in the measure, where CMS reports it"
        ),
        field(
            "number_of_patients_returned",
            T.IntegerType(),
            "Patients who returned, where CMS reports it",
        ),
        field("footnote", T.StringType(), "CMS footnote codes for the row"),
        field("period_start", T.DateType(), "First date of the measurement period"),
        field("period_end", T.DateType(), "Last date of the measurement period"),
        field("_batch_id", T.StringType(), "Bronze batch the row came from", nullable=False),
    ]
)

SILVER_UNPLANNED_VISITS_DOC = TableDoc(
    comment=(
        "Unplanned Hospital Visits: readmission rates, excess days in acute care and "
        "outpatient return visits, one row per facility and measure. Source: CMS Provider Data "
        "dataset 632h-zaca via bronze_unplanned_visits."
    ),
    tags=silver_tags("cms", dataset="632h-zaca"),
)

NUMERIC_COLUMNS = [
    ("denominator", "denominator", "int"),
    ("score", "score", "double"),
    ("lower_estimate", "lower_estimate", "double"),
    ("higher_estimate", "higher_estimate", "double"),
    ("number_of_patients", "number_of_patients", "int"),
    ("number_of_patients_returned", "number_of_patients_returned", "int"),
    ("period_start", "start_date", "date"),
    ("period_end", "end_date", "date"),
]


def silver_unplanned_visits(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_unplanned_visits rows, quarantine rows)."""
    df = bronze.withColumn("_raw", raw_row(bronze))
    df, parse_rules = with_parsed(df, NUMERIC_COLUMNS)
    df = (
        df.withColumn("facility_id", pad_ccn(F.col("facility_id")))
        .withColumn("measure_id", F.nullif(F.trim("measure_id"), F.lit("")))
        .withColumn("measure", canonical_measure(F.col("measure_id")))
        .withColumn("measure_name", F.trim("measure_name"))
        .withColumn("compared_to_national", suppressed_to_null("compared_to_national"))
        .withColumn("footnote", F.nullif(F.trim("footnote"), F.lit("")))
    )
    rules = [*missing_rules(KEY), *parse_rules, *constraint_rules(CONSTRAINTS)]
    checked = df.withColumn("_violation", first_violation(rules))
    return split_rows(checked, table=TABLE, schema=SILVER_UNPLANNED_VISITS_SCHEMA, key=KEY)
