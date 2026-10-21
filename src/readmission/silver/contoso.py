"""silver_contoso_*: Contoso's operational tables, typed and deduplicated from Bronze.

Bronze is append-only, so a row Contoso updates (or a load that re-read a batch after a failure)
appears more than once; Silver keeps the latest version of each key by `updated_at`, then by
`_ingested_at`.

TODO(quarantine): route rows that fail the checks below (unknown condition, negative counts,
completed calls above attempted) to the shared Silver quarantine table once that pattern lands
(pf:3ba50230). Until then they are dropped. Contoso's Postgres CHECK constraints already reject
them at the source, so today nothing is dropped.
"""

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.ingest.contoso import CONDITIONS

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


def silver_contoso_discharges(bronze: DataFrame) -> DataFrame:
    df = latest_by_key(bronze, ["facility_id", "discharge_month", "condition"])
    invalid = ~F.col("condition").isin(CONDITIONS) | (F.col("discharges") < 0)
    return _conform(df.where(~invalid), SILVER_CONTOSO_DISCHARGES_SCHEMA)


def silver_contoso_followup_calls(bronze: DataFrame) -> DataFrame:
    df = latest_by_key(bronze, ["facility_id", "discharge_month", "condition", "within_72h"])
    invalid = (
        ~F.col("condition").isin(CONDITIONS)
        | (F.col("calls_completed") < 0)
        | (F.col("calls_completed") > F.col("calls_attempted"))
    )
    return _conform(df.where(~invalid), SILVER_CONTOSO_FOLLOWUP_CALLS_SCHEMA)


SILVER_TABLES = {
    "facilities": silver_contoso_facilities,
    "discharges": silver_contoso_discharges,
    "followup_calls": silver_contoso_followup_calls,
}
