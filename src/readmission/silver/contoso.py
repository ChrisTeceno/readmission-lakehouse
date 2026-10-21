"""silver_contoso_*: Contoso's operational tables, typed and deduplicated from Bronze.

Bronze is append-only, so a row Contoso updates (or a load that re-read a batch after a failure)
appears more than once; Silver keeps the latest version of each key by `updated_at`, then by
`_ingested_at`.

Rows that fail the checks below (unknown condition, negative counts, completed calls above
attempted) are left out of Silver; the `silver` task writes them to `silver_quarantine` with
`contoso_rejects`. Contoso's Postgres CHECK constraints already reject them at the source, so
today there are none.
"""

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.ingest.contoso import CONDITIONS
from readmission.silver.quarantine import Rule, first_violation, raw_row

SILVER_CONTOSO_FACILITIES_SCHEMA = T.StructType(
    [
        T.StructField("facility_id", T.StringType(), nullable=False),
        T.StructField("facility_name", T.StringType(), nullable=False),
        T.StructField("ccn", T.StringType(), nullable=False),
        T.StructField("state", T.StringType(), nullable=False),
        T.StructField("updated_at", T.TimestampType(), nullable=False),
    ]
)
SILVER_CONTOSO_DISCHARGES_SCHEMA = T.StructType(
    [
        T.StructField("facility_id", T.StringType(), nullable=False),
        T.StructField("discharge_month", T.DateType(), nullable=False),
        T.StructField("condition", T.StringType(), nullable=False),
        T.StructField("discharges", T.IntegerType(), nullable=False),
        T.StructField("updated_at", T.TimestampType(), nullable=False),
    ]
)
SILVER_CONTOSO_FOLLOWUP_CALLS_SCHEMA = T.StructType(
    [
        T.StructField("facility_id", T.StringType(), nullable=False),
        T.StructField("discharge_month", T.DateType(), nullable=False),
        T.StructField("condition", T.StringType(), nullable=False),
        T.StructField("within_72h", T.BooleanType(), nullable=False),
        T.StructField("calls_attempted", T.IntegerType(), nullable=False),
        T.StructField("calls_completed", T.IntegerType(), nullable=False),
        T.StructField("updated_at", T.TimestampType(), nullable=False),
    ]
)


def latest_by_key(bronze: DataFrame, keys: list[str]) -> DataFrame:
    order = Window.partitionBy(*keys).orderBy(F.desc("updated_at"), F.desc("_ingested_at"))
    return bronze.withColumn("_rank", F.row_number().over(order)).where("_rank = 1")


def _conform(df: DataFrame, schema: T.StructType) -> DataFrame:
    return df.select(*[F.col(f.name).cast(f.dataType).alias(f.name) for f in schema])


def silver_contoso_facilities(bronze: DataFrame) -> DataFrame:
    """`ccn` is Postgres char(6), which blank-pads a shorter value; trimmed so it still joins to
    CMS facility ids."""
    df = latest_by_key(bronze, ["facility_id"]).withColumn("ccn", F.trim("ccn"))
    return _conform(df, SILVER_CONTOSO_FACILITIES_SCHEMA)


DISCHARGES_KEY = ["facility_id", "discharge_month", "condition"]
FOLLOWUP_CALLS_KEY = ["facility_id", "discharge_month", "condition", "within_72h"]


def _discharges_rules() -> list[Rule]:
    return [
        ("unknown_condition", ~F.col("condition").isin(CONDITIONS)),
        ("negative_count", F.col("discharges") < 0),
    ]


def _followup_calls_rules() -> list[Rule]:
    return [
        ("unknown_condition", ~F.col("condition").isin(CONDITIONS)),
        ("negative_count", F.col("calls_completed") < 0),
        ("completed_above_attempted", F.col("calls_completed") > F.col("calls_attempted")),
    ]


def silver_contoso_discharges(bronze: DataFrame) -> DataFrame:
    df = latest_by_key(bronze, DISCHARGES_KEY)
    df = df.withColumn("_violation", first_violation(_discharges_rules()))
    return _conform(df.where(F.col("_violation").isNull()), SILVER_CONTOSO_DISCHARGES_SCHEMA)


def silver_contoso_followup_calls(bronze: DataFrame) -> DataFrame:
    df = latest_by_key(bronze, FOLLOWUP_CALLS_KEY)
    df = df.withColumn("_violation", first_violation(_followup_calls_rules()))
    return _conform(df.where(F.col("_violation").isNull()), SILVER_CONTOSO_FOLLOWUP_CALLS_SCHEMA)


CHECKED_TABLES = {
    "discharges": (DISCHARGES_KEY, _discharges_rules),
    "followup_calls": (FOLLOWUP_CALLS_KEY, _followup_calls_rules),
}


def contoso_rejects(bronze: DataFrame, table: str) -> DataFrame:
    """The latest versions of `bronze_contoso_<table>` rows that Silver leaves out, as
    `silver_quarantine` rows."""
    key, rules = CHECKED_TABLES[table]
    df = latest_by_key(bronze.withColumn("_raw", raw_row(bronze)), key)
    return (
        df.withColumn("_violation", first_violation(rules()))
        .where(F.col("_violation").isNotNull())
        .select(
            F.lit(f"silver_contoso_{table}").alias("table_name"),
            F.concat_ws("|", *[F.col(c).cast("string") for c in key]).alias("business_key"),
            F.col("_raw").alias("raw_row"),
            F.col("_violation").alias("rule"),
            F.col("_batch_id"),
        )
    )


SILVER_TABLES = {
    "facilities": silver_contoso_facilities,
    "discharges": silver_contoso_discharges,
    "followup_calls": silver_contoso_followup_calls,
}
