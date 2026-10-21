import argparse

import pytest

from readmission.ops import ForcedFailure, RunRecorder, add_ops_arguments, track_run
from readmission.ops.tables import create_statement, write_run
from readmission.quality.expectations import ExpectationFailed, check_row_count


def ops_args(*argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="c")
    parser.add_argument("--schema", default="s")
    add_ops_arguments(parser)
    return parser.parse_args(list(argv))


def recording():
    written = []
    return written, lambda prefix, result, metrics: written.append((prefix, result, metrics))


def test_track_run_logs_a_succeeded_run():
    written, write = recording()
    with track_run("bronze", ops_args("--job-run-id=42", "--fail-task="), write) as run:
        run.wrote("c.s.bronze_hrrp", 10)
        run.wrote("c.s.bronze_hrrp", 5)

    ((prefix, result, metrics),) = written
    assert prefix == "c.s"
    assert (result.job_run_id, result.task, result.status) == ("42", "bronze", "succeeded")
    assert (result.rows_written, result.error) == (15, None)
    assert result.ended_at >= result.started_at
    assert [(m.metric, m.value) for m in metrics] == [("rows_written", 10), ("rows_written", 5)]


def test_track_run_logs_a_failure_and_reraises():
    written, write = recording()
    with (
        pytest.raises(ExpectationFailed),
        track_run("silver", ops_args("--job-run-id=42"), write) as run,
    ):
        run.check("t", "row_count", 79, 80, lambda: check_row_count("t", 100, 79))

    ((_, result, metrics),) = written
    assert result.status == "failed"
    assert result.error.startswith("ExpectationFailed: t: 79 rows, down from 100")
    assert [(m.metric, m.value, m.threshold, m.passed) for m in metrics] == [
        ("row_count", 79, 80, False)
    ]


def test_forced_failure_only_in_the_named_task():
    written, write = recording()
    with (
        pytest.raises(ForcedFailure, match="gold: forced failure"),
        track_run("gold", ops_args("--job-run-id=7", "--fail-task=gold"), write),
    ):
        pytest.fail("the task body must not run")
    with track_run("silver", ops_args("--job-run-id=7", "--fail-task=gold"), write):
        pass

    assert [(r.task, r.status) for _, r, _ in written] == [
        ("gold", "failed"),
        ("silver", "succeeded"),
    ]
    assert written[0][1].error == "ForcedFailure: gold: forced failure (--fail-task=gold)"


def test_no_job_run_id_writes_nothing():
    written, write = recording()
    with track_run("fetch", ops_args(), write) as run:
        run.wrote("landing/cms_hrrp", 3)
    assert written == []


def test_write_run_appends_to_the_ops_tables(spark):
    spark.sql("CREATE DATABASE IF NOT EXISTS ops_test")
    prefix = "spark_catalog.ops_test"
    try:
        for status in ["succeeded", "failed"]:
            written, write = recording()
            fail = "--fail-task=gold" if status == "failed" else "--fail-task="
            try:
                with track_run("gold", ops_args("--job-run-id=9", fail), write) as run:
                    run.wrote(f"{prefix}.gold_penalty_mart", 4)
                    run.metric(
                        f"{prefix}.gold_facility_condition_year", "anomaly_rate", 0.1, 0.05, False
                    )
            except ForcedFailure:
                pass
            ((_, result, metrics),) = written
            write_run(prefix, result, metrics, spark)

        log = spark.table(f"{prefix}.ops_run_log").orderBy("status").collect()
        assert [(r.task, r.status, r.rows_written) for r in log] == [
            ("gold", "failed", 0),
            ("gold", "succeeded", 4),
        ]
        assert log[0].error.startswith("ForcedFailure")
        metrics = spark.table(f"{prefix}.ops_dq_metrics").collect()
        assert sorted((m.metric, m.value, m.threshold, m.passed) for m in metrics) == [
            ("anomaly_rate", 0.1, 0.05, False),
            ("rows_written", 4.0, None, True),
        ]
    finally:
        spark.sql("DROP DATABASE IF EXISTS ops_test CASCADE")


def test_create_statement():
    from pyspark.sql import types as T

    from readmission.silver.governance import field

    schema = T.StructType(
        [
            field("task", T.StringType(), "Job task", nullable=False),
            field("n", T.LongType(), "It's n"),
        ]
    )
    assert create_statement("c.s.t", schema) == (
        "CREATE TABLE IF NOT EXISTS c.s.t (`task` string NOT NULL COMMENT 'Job task', "
        "`n` bigint COMMENT 'It\\'s n') USING DELTA"
    )


def test_recorder_counts_rows_per_table():
    run = RunRecorder("contoso")
    run.wrote("a", 2)
    run.wrote("b", 3)
    assert (run.rows, run.rows_written) == ({"a": 2, "b": 3}, 5)
