"""Bronze load of Contoso's operational database, incremental on `updated_at`.

    readmission-contoso [--catalog workspace] [--schema readmission]

The `contoso` task of the `readmission_pipeline` job. Each source table is read over JDBC from
the Lakebase compute (see engagement/adr/0001-contoso-lakebase-connector.md), keeping only rows
with `updated_at` after the watermark recorded in `ops_watermarks`, and appended to
`bronze_contoso_<table>` with the ingest metadata columns. The watermark moves to the largest
`updated_at` loaded, after the append, so a run that fails between the two re-reads the same
rows next time and Silver's dedup absorbs the repeat. Silver is then rebuilt from Bronze.
"""

import argparse
from collections.abc import Callable
from datetime import UTC, datetime

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.ingest.contoso import PG_SCHEMA, TABLES, connection_params
from readmission.ingest.landing import new_batch_id
from readmission.silver import contoso as silver

WATERMARK_TABLE = "ops_watermarks"
OPS_WATERMARKS_SCHEMA = T.StructType(
    [
        T.StructField("source", T.StringType(), nullable=False),
        T.StructField("watermark", T.TimestampType()),
        T.StructField("rows_loaded", T.LongType(), nullable=False),
        T.StructField("_batch_id", T.StringType(), nullable=False),
        T.StructField("updated_at", T.TimestampType(), nullable=False),
    ]
)
# A watermark formatted with its UTC offset, so Postgres compares instants whatever the Spark
# session time zone is.
WATERMARK_FORMAT = "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX"

# Reads one source table's rows changed after a watermark (None reads everything).
Reader = Callable[[str, str | None], DataFrame]


def source_name(table: str) -> str:
    return f"lakebase:{PG_SCHEMA}.{table}"


def incremental_query(table: str, watermark: str | None) -> str:
    query = f"SELECT * FROM {PG_SCHEMA}.{table}"
    if watermark is None:
        return query
    return f"{query} WHERE updated_at > '{watermark}'::timestamptz"


def jdbc_options(params: dict[str, str], query: str) -> dict[str, str]:
    """The token goes in `password`, never in the URL, so it stays out of query plans and logs."""
    return {
        "url": f"jdbc:postgresql://{params['host']}:5432/{params['dbname']}?sslmode=require",
        "user": params["user"],
        "password": params["password"],
        "query": query,
    }


def jdbc_reader(spark: SparkSession, params: dict[str, str]) -> Reader:
    def read(table: str, watermark: str | None) -> DataFrame:
        options = jdbc_options(params, incremental_query(table, watermark))
        return spark.read.format("jdbc").options(**options).load()

    return read


def bronze_contoso(df: DataFrame, *, table: str, batch_id: str, ingested_at: datetime) -> DataFrame:
    return df.select(
        "*",
        F.lit(ingested_at).cast("timestamp").alias("_ingested_at"),
        F.lit(source_name(table)).alias("_source"),
        F.lit(batch_id).alias("_batch_id"),
    )


def read_watermark(spark: SparkSession, watermark_table: str, source: str) -> str | None:
    if not spark.catalog.tableExists(watermark_table):
        return None
    row = (
        spark.table(watermark_table)
        .where(F.col("source") == source)
        .select(F.date_format("watermark", WATERMARK_FORMAT).alias("watermark"))
        .first()
    )
    return row["watermark"] if row else None


def save_watermark(
    spark: SparkSession,
    watermark_table: str,
    source: str,
    watermark: datetime | None,
    rows_loaded: int,
    batch_id: str,
    updated_at: datetime,
) -> None:
    """One row per source, replaced on each load that brought rows."""
    row = spark.createDataFrame(
        [(source, watermark, rows_loaded, batch_id, updated_at)], OPS_WATERMARKS_SCHEMA
    )
    if not spark.catalog.tableExists(watermark_table):
        row.write.format("delta").saveAsTable(watermark_table)
        return
    from delta.tables import DeltaTable

    (
        DeltaTable.forName(spark, watermark_table)
        .alias("t")
        .merge(row.alias("s"), "t.source = s.source")
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )


def load_table(
    spark: SparkSession,
    read: Reader,
    table: str,
    bronze_table: str,
    watermark_table: str,
    batch_id: str,
    ingested_at: datetime,
) -> int:
    """Append the rows changed since the last load; returns how many arrived.

    The source is read once, by the append; the new watermark comes from the appended batch, so
    a row committed in Postgres between two reads can never be skipped.
    """
    source = source_name(table)
    changed = read(table, read_watermark(spark, watermark_table, source))
    bronze = bronze_contoso(changed, table=table, batch_id=batch_id, ingested_at=ingested_at)
    bronze.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(
        bronze_table
    )
    stats = (
        spark.table(bronze_table)
        .where((F.col("_batch_id") == batch_id) & (F.col("_source") == source))
        .agg(F.count("*").alias("n"), F.max("updated_at").alias("max"))
        .first()
    )
    if stats["n"] == 0:
        return 0
    save_watermark(spark, watermark_table, source, stats["max"], stats["n"], batch_id, ingested_at)
    return stats["n"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Load Contoso's database into Bronze and Silver.")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--schema", default="readmission")
    args = parser.parse_args(argv)
    from databricks.sdk import WorkspaceClient

    spark = SparkSession.builder.getOrCreate()
    read = jdbc_reader(spark, connection_params(WorkspaceClient()))
    prefix = f"{args.catalog}.{args.schema}"
    ingested_at = datetime.now(UTC)
    batch_id = new_batch_id(ingested_at)
    for table in TABLES:
        name = f"{prefix}.bronze_contoso_{table}"
        appended = load_table(
            spark, read, table, name, f"{prefix}.{WATERMARK_TABLE}", batch_id, ingested_at
        )
        print(f"{name}: appended {appended} rows from {source_name(table)}")
    for table, build in silver.SILVER_TABLES.items():
        name = f"{prefix}.silver_contoso_{table}"
        df = build(spark.table(f"{prefix}.bronze_contoso_{table}"))
        # Silver is small and rebuilt from Bronze on every run.
        df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(
            name
        )
        print(f"{name}: {spark.table(name).count()} rows")


if __name__ == "__main__":
    main()
