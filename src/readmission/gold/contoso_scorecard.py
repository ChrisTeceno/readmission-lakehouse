"""gold_contoso_scorecard: Contoso's facilities, their CMS results and their own operations.

One row per Contoso facility, HRRP condition and fiscal year. The CMS columns come from
gold_facility_condition_year through the facility's CCN; the operational columns add up
Contoso's monthly discharges and follow-up calls (synthetic, from its Lakebase database) over
the months of that fiscal year's performance window. Contoso's data starts in September 2023,
so only FY2026 and FY2027 windows overlap it; earlier years have no operational months.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.silver.governance import TableDoc, field, gold_tags

TABLE = "gold_contoso_scorecard"
KEY = ["contoso_facility_id", "measure", "fiscal_year"]

GOLD_CONTOSO_SCORECARD_SCHEMA = T.StructType(
    [
        field(
            "contoso_facility_id",
            T.StringType(),
            "Contoso's own facility id, CH01 to CH12",
            nullable=False,
        ),
        field("contoso_facility_name", T.StringType(), "Contoso's name for the facility"),
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field(
            "measure",
            T.StringType(),
            "HRRP condition: AMI, HF, PN, COPD, HIP_KNEE or CABG",
            nullable=False,
        ),
        field("fiscal_year", T.IntegerType(), "HRRP payment fiscal year", nullable=False),
        field("period_start", T.DateType(), "First discharge date of the performance period"),
        field("period_end", T.DateType(), "Last discharge date of the performance period"),
        field(
            "cms_discharges",
            T.IntegerType(),
            "Medicare fee-for-service discharges CMS counted; null if suppressed",
        ),
        field("excess_readmission_ratio", T.DoubleType(), "CMS excess readmission ratio"),
        field("penalty_cutoff", T.DoubleType(), "ERR above which the condition was penalized"),
        field(
            "cutoff_is_approximate",
            T.BooleanType(),
            "The cutoff is the 1.0 stand-in for a peer group median (FY2019 to FY2026)",
        ),
        field("above_cutoff", T.BooleanType(), "ERR above penalty_cutoff"),
        field("err_change", T.DoubleType(), "ERR change from the prior non-overlapping window"),
        field("anomaly_flag", T.BooleanType(), "Flagged in gold_facility_condition_year"),
        field(
            "contoso_months",
            T.IntegerType(),
            "Months of Contoso operational data inside the performance window",
            nullable=False,
        ),
        field(
            "contoso_discharges",
            T.LongType(),
            "All-payer discharges in Contoso's own records over those months",
        ),
        field("calls_attempted", T.LongType(), "Post-discharge follow-up calls attempted"),
        field("calls_completed", T.LongType(), "Post-discharge follow-up calls completed"),
        field(
            "calls_completed_within_72h",
            T.LongType(),
            "Completed follow-up calls made within 72 hours of discharge",
        ),
        field(
            "followup_rate",
            T.DoubleType(),
            "calls_completed over contoso_discharges (ratio)",
        ),
        field(
            "followup_within_72h_rate",
            T.DoubleType(),
            "calls_completed_within_72h over contoso_discharges (ratio)",
        ),
    ]
)

GOLD_CONTOSO_SCORECARD_DOC = TableDoc(
    comment=(
        "Contoso Health's 12 facilities (fictional names on real CCNs) per HRRP condition and "
        "fiscal year: CMS results beside Contoso's synthetic discharges and follow-up calls "
        "over the same performance window. Built from gold_facility_condition_year and the "
        "silver_contoso_* tables."
    ),
    tags=gold_tags("cms,contoso"),
)


def monthly_operations(discharges: DataFrame, followup_calls: DataFrame) -> DataFrame:
    """One row per Contoso facility, month and condition."""
    keys = ["facility_id", "discharge_month", "condition"]
    calls = followup_calls.groupBy(*keys).agg(
        F.sum("calls_attempted").alias("calls_attempted"),
        F.sum("calls_completed").alias("calls_completed"),
        F.sum(F.when(F.col("within_72h"), F.col("calls_completed")).otherwise(0)).alias(
            "calls_completed_within_72h"
        ),
    )
    return discharges.select(*keys, "discharges").join(calls, keys, "full")


def gold_contoso_scorecard(
    facts: DataFrame,
    facilities: DataFrame,
    discharges: DataFrame,
    followup_calls: DataFrame,
) -> DataFrame:
    """`facts` is gold_facility_condition_year; the rest are the silver_contoso_* tables."""
    contoso = facilities.select(
        F.col("facility_id").alias("contoso_facility_id"),
        F.col("facility_name").alias("contoso_facility_name"),
        F.col("ccn").alias("facility_id"),
    )
    cms = facts.join(contoso, "facility_id").withColumnRenamed("discharges", "cms_discharges")
    months = monthly_operations(discharges, followup_calls).select(
        F.col("facility_id").alias("_month_facility_id"),
        F.col("condition").alias("_month_measure"),
        "discharge_month",
        F.col("discharges").alias("contoso_discharges"),
        "calls_attempted",
        "calls_completed",
        "calls_completed_within_72h",
    )
    in_window = (
        (F.col("contoso_facility_id") == F.col("_month_facility_id"))
        & (F.col("measure") == F.col("_month_measure"))
        & (F.col("discharge_month") >= F.trunc("period_start", "month"))
        & (F.col("discharge_month") <= F.col("period_end"))
    )
    joined = cms.join(months, in_window, "left")
    cms_columns = [
        "contoso_facility_name",
        "facility_id",
        "period_start",
        "period_end",
        "cms_discharges",
        "excess_readmission_ratio",
        "penalty_cutoff",
        "cutoff_is_approximate",
        "above_cutoff",
        "err_change",
        "anomaly_flag",
    ]
    df = (
        joined.groupBy(*KEY, *cms_columns)
        .agg(
            F.count_distinct("discharge_month").alias("contoso_months"),
            F.sum("contoso_discharges").alias("contoso_discharges"),
            F.sum("calls_attempted").alias("calls_attempted"),
            F.sum("calls_completed").alias("calls_completed"),
            F.sum("calls_completed_within_72h").alias("calls_completed_within_72h"),
        )
        .withColumn("followup_rate", F.try_divide("calls_completed", "contoso_discharges"))
        .withColumn(
            "followup_within_72h_rate",
            F.try_divide("calls_completed_within_72h", "contoso_discharges"),
        )
    )
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in GOLD_CONTOSO_SCORECARD_SCHEMA]
    )
