from datetime import UTC, date, datetime

from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.bronze.contoso import (
    OPS_WATERMARKS_SCHEMA,
    bronze_contoso,
    incremental_query,
    jdbc_options,
    load_table,
    read_watermark,
    save_watermark,
)
from readmission.silver.contoso import (
    SILVER_CONTOSO_DISCHARGES_SCHEMA,
    SILVER_CONTOSO_FACILITIES_SCHEMA,
    SILVER_CONTOSO_FOLLOWUP_CALLS_SCHEMA,
    silver_contoso_discharges,
    silver_contoso_facilities,
    silver_contoso_followup_calls,
)

# What the JDBC reader returns for contoso.discharges.
SOURCE_DISCHARGES_SCHEMA = T.StructType(
    [
        T.StructField("facility_id", T.StringType()),
        T.StructField("discharge_month", T.DateType()),
        T.StructField("condition", T.StringType()),
        T.StructField("discharges", T.IntegerType()),
        T.StructField("updated_at", T.TimestampType()),
    ]
)
SEEDED_AT = datetime(2026, 10, 21, 14, 0, 0, 123456, tzinfo=UTC)
APPENDED_AT = datetime(2026, 10, 21, 15, 0, 0, tzinfo=UTC)


def discharges(spark, rows):
    return spark.createDataFrame(rows, SOURCE_DISCHARGES_SCHEMA)


def fake_reader(spark, source):
    """Stands in for the JDBC read: the rows of `source` after the watermark."""
    seen = []

    def read(table, watermark):
        seen.append((table, watermark))
        if watermark is None:
            return source()
        return source().where(F.col("updated_at") > F.to_timestamp(F.lit(watermark)))

    return read, seen


def test_incremental_query_compares_instants():
    assert incremental_query("discharges", None) == "SELECT * FROM contoso.discharges"
    assert incremental_query("discharges", "2026-10-21T14:00:00.123456Z") == (
        "SELECT * FROM contoso.discharges WHERE updated_at > "
        "'2026-10-21T14:00:00.123456Z'::timestamptz"
    )


def test_jdbc_options_keep_the_token_out_of_the_url():
    params = {"host": "ep-1.db", "dbname": "databricks_postgres", "user": "u", "password": "tok"}
    options = jdbc_options(params, "SELECT 1")
    assert options["url"] == "jdbc:postgresql://ep-1.db:5432/databricks_postgres?sslmode=require"
    assert options["password"] == "tok"
    assert "tok" not in options["url"]
    assert options["query"] == "SELECT 1"


def test_bronze_adds_ingest_metadata(spark):
    df = discharges(spark, [("CH01", date(2024, 1, 1), "HF", 40, SEEDED_AT)])
    row = bronze_contoso(df, table="discharges", batch_id="b1", ingested_at=APPENDED_AT).first()
    assert row["_source"] == "lakebase:contoso.discharges"
    assert row["_batch_id"] == "b1"
    assert row["_ingested_at"] is not None
    assert row["discharges"] == 40


def test_watermark_round_trip_keeps_microseconds(spark):
    table = "ops_watermarks_round_trip"
    assert read_watermark(spark, table, "lakebase:contoso.discharges") is None
    save_watermark(spark, table, "lakebase:contoso.discharges", SEEDED_AT, 5, "b1", SEEDED_AT)
    save_watermark(spark, table, "lakebase:contoso.discharges", APPENDED_AT, 2, "b2", APPENDED_AT)
    save_watermark(spark, table, "lakebase:contoso.facilities", SEEDED_AT, 1, "b1", SEEDED_AT)
    assert spark.table(table).dtypes == [
        (f.name, f.dataType.simpleString()) for f in OPS_WATERMARKS_SCHEMA
    ]
    assert spark.table(table).count() == 2
    mark = read_watermark(spark, table, "lakebase:contoso.discharges")
    parsed = datetime.fromisoformat(mark)
    assert parsed == APPENDED_AT
    facilities = datetime.fromisoformat(read_watermark(spark, table, "lakebase:contoso.facilities"))
    assert facilities == SEEDED_AT


def test_second_load_brings_only_new_rows(spark):
    seeded = [
        ("CH01", date(2026, 8, 1), "HF", 40, SEEDED_AT),
        ("CH01", date(2026, 8, 1), "PN", 30, SEEDED_AT),
    ]
    appended = [("CH01", date(2026, 9, 1), "HF", 44, APPENDED_AT)]
    rows = list(seeded)
    read, seen = fake_reader(spark, lambda: discharges(spark, rows))
    bronze, marks = "bronze_contoso_discharges_incremental", "ops_watermarks_incremental"
    first = datetime(2026, 10, 21, 14, 5, tzinfo=UTC)

    assert load_table(spark, read, "discharges", bronze, marks, "b1", first) == 2
    rows.extend(appended)
    second = datetime(2026, 10, 21, 15, 5, tzinfo=UTC)
    assert load_table(spark, read, "discharges", bronze, marks, "b2", second) == 1
    assert load_table(spark, read, "discharges", bronze, marks, "b3", second) == 0

    assert seen[0] == ("discharges", None)
    assert datetime.fromisoformat(seen[1][1]) == SEEDED_AT
    assert datetime.fromisoformat(seen[2][1]) == APPENDED_AT
    counts = {
        r["_batch_id"]: r["count"]
        for r in spark.table(bronze).groupBy("_batch_id").count().collect()
    }
    assert counts == {"b1": 2, "b2": 1}
    assert spark.table(marks).first()["rows_loaded"] == 1


def test_silver_keeps_latest_version_and_drops_invalid_rows(spark):
    bronze = bronze_contoso(
        discharges(
            spark,
            [
                ("CH01", date(2024, 1, 1), "HF", 40, SEEDED_AT),
                ("CH01", date(2024, 1, 1), "HF", 42, APPENDED_AT),
                ("CH01", date(2024, 1, 1), "SEPSIS", 9, SEEDED_AT),
                ("CH02", date(2024, 1, 1), "PN", -1, SEEDED_AT),
            ],
        ),
        table="discharges",
        batch_id="b1",
        ingested_at=APPENDED_AT,
    )
    silver = silver_contoso_discharges(bronze)
    assert silver.schema.fieldNames() == SILVER_CONTOSO_DISCHARGES_SCHEMA.fieldNames()
    assert [(r.facility_id, r.condition, r.discharges) for r in silver.collect()] == [
        ("CH01", "HF", 42)
    ]


def test_silver_breaks_updated_at_ties_by_ingest_time(spark):
    rows = [("CH01", date(2024, 1, 1), "HF", 40, SEEDED_AT)]
    first = bronze_contoso(
        discharges(spark, rows), table="discharges", batch_id="b1", ingested_at=SEEDED_AT
    )
    rerun = bronze_contoso(
        discharges(spark, rows), table="discharges", batch_id="b2", ingested_at=APPENDED_AT
    )
    assert silver_contoso_discharges(first.unionByName(rerun)).count() == 1


def test_silver_facilities_trim_ccn(spark):
    schema = T.StructType(
        [
            T.StructField("facility_id", T.StringType()),
            T.StructField("facility_name", T.StringType()),
            T.StructField("ccn", T.StringType()),
            T.StructField("state", T.StringType()),
            T.StructField("updated_at", T.TimestampType()),
        ]
    )
    df = spark.createDataFrame(
        [("CH01", "Contoso Health Alder", "36008 ", "OH", SEEDED_AT)], schema
    )
    bronze = bronze_contoso(df, table="facilities", batch_id="b1", ingested_at=SEEDED_AT)
    silver = silver_contoso_facilities(bronze)
    assert silver.schema.fieldNames() == SILVER_CONTOSO_FACILITIES_SCHEMA.fieldNames()
    assert silver.first()["ccn"] == "36008"


def test_silver_followup_calls_rejects_completed_above_attempted(spark):
    schema = T.StructType(
        [
            T.StructField("facility_id", T.StringType()),
            T.StructField("discharge_month", T.DateType()),
            T.StructField("condition", T.StringType()),
            T.StructField("within_72h", T.BooleanType()),
            T.StructField("calls_attempted", T.IntegerType()),
            T.StructField("calls_completed", T.IntegerType()),
            T.StructField("updated_at", T.TimestampType()),
        ]
    )
    df = spark.createDataFrame(
        [
            ("CH01", date(2024, 1, 1), "HF", True, 20, 15, SEEDED_AT),
            ("CH01", date(2024, 1, 1), "HF", False, 5, 6, SEEDED_AT),
        ],
        schema,
    )
    bronze = bronze_contoso(df, table="followup_calls", batch_id="b1", ingested_at=SEEDED_AT)
    silver = silver_contoso_followup_calls(bronze)
    assert silver.schema.fieldNames() == SILVER_CONTOSO_FOLLOWUP_CALLS_SCHEMA.fieldNames()
    assert [r.within_72h for r in silver.collect()] == [True]
