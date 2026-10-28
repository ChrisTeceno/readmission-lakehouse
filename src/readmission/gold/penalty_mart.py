"""gold_penalty_mart: one row per hospital and fiscal year, for the dashboards.

How many conditions were above the cutoff, the worst ratio, the trend against the prior
non-overlapping window and whether the hospital is one of Contoso's.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.silver.governance import TableDoc, field, gold_tags

TABLE = "gold_penalty_mart"
KEY = ["facility_id", "fiscal_year"]

# The mean ERR change, across the conditions with a ratio in both windows, that counts as a
# trend. An ERR of 1.02 means 2 percent more readmissions than expected; smaller moves are
# within a year's normal noise for most hospitals.
TREND_THRESHOLD = 0.02

GOLD_PENALTY_MART_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field("fiscal_year", T.IntegerType(), "HRRP payment fiscal year", nullable=False),
        field("facility_name", T.StringType(), "Hospital name; null if no longer in CMS's list"),
        field("state", T.StringType(), "Two-letter state code"),
        field("hospital_type", T.StringType(), "CMS hospital type"),
        field(
            "conditions_reported",
            T.IntegerType(),
            "Conditions with an ERR this year",
            nullable=False,
        ),
        field(
            "conditions_above_cutoff",
            T.IntegerType(),
            "Conditions whose ERR was above the penalty cutoff",
            nullable=False,
        ),
        field(
            "measures_above_cutoff",
            T.StringType(),
            "The conditions above the cutoff, comma separated; null if none",
        ),
        field(
            "penalized",
            T.BooleanType(),
            "At least one condition above the cutoff, which is what triggers an HRRP payment "
            "reduction",
            nullable=False,
        ),
        field("worst_measure", T.StringType(), "Condition with the highest ERR"),
        field("worst_err", T.DoubleType(), "Highest ERR across the hospital's conditions"),
        field("mean_err", T.DoubleType(), "Mean ERR across the conditions with one"),
        field(
            "cutoff_is_approximate",
            T.BooleanType(),
            "Any condition's cutoff is the 1.0 stand-in for an unpublished peer group median",
            nullable=False,
        ),
        field(
            "prior_window_fiscal_year",
            T.IntegerType(),
            "Latest fiscal year whose performance period ends before this one starts",
        ),
        field(
            "prior_conditions_above_cutoff",
            T.IntegerType(),
            "conditions_above_cutoff in the prior window; null if the hospital was not in it",
        ),
        field(
            "mean_err_change",
            T.DoubleType(),
            "Mean ERR change from the prior window, over conditions with a ratio in both",
        ),
        field(
            "trend",
            T.StringType(),
            "worse or better when mean_err_change is beyond 0.02 either way, else flat; null "
            "without a prior window",
        ),
        field(
            "anomalies",
            T.IntegerType(),
            "Conditions flagged as anomalous this year",
            nullable=False,
        ),
        field("is_contoso", T.BooleanType(), "One of Contoso Health's facilities", nullable=False),
        field("contoso_facility_id", T.StringType(), "Contoso's own facility id, CH01 to CH12"),
        field("contoso_facility_name", T.StringType(), "Contoso's name for the facility"),
    ]
)

GOLD_PENALTY_MART_DOC = TableDoc(
    comment=(
        "HRRP penalty view per hospital and fiscal year, FY2013 to FY2027: conditions above the "
        "cutoff, worst ERR, trend against the prior non-overlapping window and the Contoso "
        "flag. Cutoffs from FY2019 are the peer group medians (see "
        "gold_facility_condition_year). Built from gold_facility_condition_year and "
        "silver_contoso_facilities."
    ),
    tags=gold_tags("cms,contoso"),
)


def gold_penalty_mart(facts: DataFrame, contoso_facilities: DataFrame) -> DataFrame:
    """`facts` is gold_facility_condition_year; `contoso_facilities` silver_contoso_facilities,
    whose `ccn` is the CMS facility id."""
    err = F.col("excess_readmission_ratio")
    hospital_year = facts.groupBy(*KEY).agg(
        F.first("facility_name", ignorenulls=True).alias("facility_name"),
        F.first("state", ignorenulls=True).alias("state"),
        F.first("hospital_type", ignorenulls=True).alias("hospital_type"),
        F.count(err).cast("int").alias("conditions_reported"),
        F.count_if(F.col("above_cutoff")).cast("int").alias("conditions_above_cutoff"),
        F.concat_ws(
            ",", F.array_sort(F.collect_list(F.when(F.col("above_cutoff"), F.col("measure"))))
        ).alias("measures_above_cutoff"),
        F.max_by("measure", err).alias("worst_measure"),
        F.max(err).alias("worst_err"),
        F.avg(err).alias("mean_err"),
        F.max("cutoff_is_approximate").alias("cutoff_is_approximate"),
        F.first("prior_window_fiscal_year").alias("prior_window_fiscal_year"),
        F.avg("err_change").alias("mean_err_change"),
        F.count_if(F.col("anomaly_flag")).cast("int").alias("anomalies"),
    )
    prior = hospital_year.select(
        "facility_id",
        F.col("fiscal_year").alias("prior_window_fiscal_year"),
        F.col("conditions_above_cutoff").alias("prior_conditions_above_cutoff"),
    )
    contoso = contoso_facilities.select(
        F.col("ccn").alias("facility_id"),
        F.col("facility_id").alias("contoso_facility_id"),
        F.col("facility_name").alias("contoso_facility_name"),
    )
    change = F.col("mean_err_change")
    df = (
        hospital_year.join(prior, ["facility_id", "prior_window_fiscal_year"], "left")
        .join(contoso, "facility_id", "left")
        .withColumn("measures_above_cutoff", F.nullif("measures_above_cutoff", F.lit("")))
        .withColumn("penalized", F.col("conditions_above_cutoff") > 0)
        .withColumn(
            "trend",
            F.when(change > TREND_THRESHOLD, "worse")
            .when(change < -TREND_THRESHOLD, "better")
            .when(change.isNotNull(), "flat"),
        )
        .withColumn("is_contoso", F.col("contoso_facility_id").isNotNull())
    )
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in GOLD_PENALTY_MART_SCHEMA]
    )
