"""The ops tables `track_run` appends to, and their Unity Catalog documentation.

Both are append-only, one row per task run and one per metric, so a job's history stays in
place. Tasks run in parallel (`fetch` beside `contoso`), so the tables are created with
`CREATE TABLE IF NOT EXISTS` and written by blind appends, which Delta never conflicts on.
"""

from pyspark.sql import SparkSession
from pyspark.sql import types as T

from readmission.ops import Metric, RunResult
from readmission.silver.governance import TableDoc, _quote, field

RUN_LOG_TABLE = "ops_run_log"
DQ_METRICS_TABLE = "ops_dq_metrics"


def ops_tags() -> dict[str, str]:
    return {"layer": "ops", "contains_phi": "false", "source": "readmission_pipeline"}


OPS_RUN_LOG_SCHEMA = T.StructType(
    [
        field("job_run_id", T.StringType(), "Databricks job run id", nullable=False),
        field(
            "task",
            T.StringType(),
            "Job task: fetch, bronze, contoso, silver, gold, train or score",
            nullable=False,
        ),
        field("started_at", T.TimestampType(), "When the task's run started (UTC)", nullable=False),
        field("ended_at", T.TimestampType(), "When the task's run ended (UTC)", nullable=False),
        field("status", T.StringType(), "succeeded or failed", nullable=False),
        field(
            "rows_written",
            T.LongType(),
            "Rows the run wrote to tables, or landed for fetch; counted up to a failure",
            nullable=False,
        ),
        field("error", T.StringType(), "Exception type and message of a failed run"),
    ]
)
OPS_RUN_LOG_DOC = TableDoc(
    comment=(
        "One row per readmission_pipeline task run, appended when the task ends. A retried "
        "task has one row per attempt. Written by readmission.ops.track_run."
    ),
    tags=ops_tags(),
)

OPS_DQ_METRICS_SCHEMA = T.StructType(
    [
        field("job_run_id", T.StringType(), "Databricks job run id", nullable=False),
        field("task", T.StringType(), "Job task that measured the metric", nullable=False),
        field(
            "table_name",
            T.StringType(),
            "Table the metric is about, or landing/<source> for fetch",
            nullable=False,
        ),
        field(
            "metric",
            T.StringType(),
            "rows_written, or an expectation such as row_count or err_null_rate",
            nullable=False,
        ),
        field("value", T.DoubleType(), "Measured value"),
        field("threshold", T.DoubleType(), "The limit the value is checked against; null if none"),
        field(
            "passed", T.BooleanType(), "Whether the value is within the threshold", nullable=False
        ),
        field("measured_at", T.TimestampType(), "When the run ended (UTC)", nullable=False),
    ]
)
OPS_DQ_METRICS_DOC = TableDoc(
    comment=(
        "Data quality metrics per readmission_pipeline task run: rows written per table and "
        "each expectation's value, threshold and result. Written by readmission.ops.track_run."
    ),
    tags=ops_tags(),
)

OPS_TABLES = [
    (RUN_LOG_TABLE, OPS_RUN_LOG_SCHEMA, OPS_RUN_LOG_DOC),
    (DQ_METRICS_TABLE, OPS_DQ_METRICS_SCHEMA, OPS_DQ_METRICS_DOC),
]


def create_statement(name: str, schema: T.StructType, *, replace: bool = False) -> str:
    columns = ", ".join(
        f"`{f.name}` {f.dataType.simpleString()}"
        + ("" if f.nullable else " NOT NULL")
        + f" COMMENT {_quote(f.metadata['comment'])}"
        for f in schema
    )
    create = "CREATE OR REPLACE TABLE" if replace else "CREATE TABLE IF NOT EXISTS"
    return f"{create} {name} ({columns}) USING DELTA"


def run_rows(result: RunResult, metrics: list[Metric]) -> tuple[list[tuple], list[tuple]]:
    run = [
        (
            result.job_run_id,
            result.task,
            result.started_at,
            result.ended_at,
            result.status,
            result.rows_written,
            result.error,
        )
    ]
    dq = [
        (
            result.job_run_id,
            result.task,
            m.table_name,
            m.metric,
            None if m.value is None else float(m.value),
            None if m.threshold is None else float(m.threshold),
            m.passed,
            result.ended_at,
        )
        for m in metrics
    ]
    return run, dq


def write_run(
    prefix: str,
    result: RunResult,
    metrics: list[Metric],
    spark: SparkSession | None = None,
) -> None:
    spark = spark or SparkSession.builder.getOrCreate()
    for (table, schema, _), rows in zip(OPS_TABLES, run_rows(result, metrics), strict=True):
        name = f"{prefix}.{table}"
        spark.sql(create_statement(name, schema))
        if rows:
            spark.createDataFrame(rows, schema).write.format("delta").mode("append").saveAsTable(
                name
            )
