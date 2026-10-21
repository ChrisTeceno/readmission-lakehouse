"""Run monitoring every job task shares: `ops_run_log` and `ops_dq_metrics`.

Each entry point wraps its work in `track_run`. When the task ends, succeeded or failed, it
appends one `ops_run_log` row (job run id, task, start, end, status, rows written, error) and
the data quality metrics the task recorded to `ops_dq_metrics`. The job passes
`--job-run-id={{job.run_id}}`; a run without a job run id (a local fetch, a test) writes
nothing, and this module stays free of Spark so fetch can run without it.

`--fail-task=<task>` makes that task raise as soon as its run starts, so a forced failure shows
up as a failed `ops_run_log` row and triggers the job's failure email:

    databricks bundle run readmission_pipeline -t dev --params fail_task=gold
"""

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime


class ForcedFailure(Exception):
    pass


def add_ops_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--job-run-id", default="", help="Databricks job run id; empty: no log")
    parser.add_argument("--fail-task", default="", help="raise in this task, to test alerts")


@dataclass(frozen=True)
class Metric:
    table_name: str
    metric: str
    value: float | None
    threshold: float | None
    passed: bool


@dataclass
class RunRecorder:
    task: str
    rows: dict[str, int] = field(default_factory=dict)
    metrics: list[Metric] = field(default_factory=list)

    def wrote(self, table: str, rows: int) -> None:
        """Rows a task wrote to one table (or landed for one source); also a metric row."""
        self.rows[table] = self.rows.get(table, 0) + rows
        self.metric(table, "rows_written", rows)

    def metric(
        self,
        table: str,
        metric: str,
        value: float | None,
        threshold: float | None = None,
        passed: bool = True,
    ) -> None:
        self.metrics.append(Metric(table, metric, value, threshold, passed))

    def check(
        self,
        table: str,
        metric: str,
        value: float | None,
        threshold: float | None,
        check: Callable[[], None],
    ) -> None:
        """Run an expectation and record whether it passed; a failing one still raises."""
        try:
            check()
        except Exception:
            self.metric(table, metric, value, threshold, passed=False)
            raise
        self.metric(table, metric, value, threshold)

    @property
    def rows_written(self) -> int:
        return sum(self.rows.values())


@dataclass(frozen=True)
class RunResult:
    job_run_id: str
    task: str
    started_at: datetime
    ended_at: datetime
    status: str
    rows_written: int
    error: str | None


@contextmanager
def track_run(
    task: str,
    args: argparse.Namespace,
    write: Callable[[str, RunResult, list[Metric]], None] | None = None,
) -> Iterator[RunRecorder]:
    """`args` carries `job_run_id`, `fail_task`, `catalog` and `schema`. `write` defaults to
    appending to the ops tables in `<catalog>.<schema>`; tests pass their own."""
    recorder = RunRecorder(task)
    started_at = datetime.now(UTC)
    error = None
    try:
        if args.fail_task == task:
            raise ForcedFailure(f"{task}: forced failure (--fail-task={task})")
        yield recorder
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        if args.job_run_id:
            result = RunResult(
                job_run_id=args.job_run_id,
                task=task,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                status="failed" if error else "succeeded",
                rows_written=recorder.rows_written,
                error=error,
            )
            if write is None:
                from readmission.ops.tables import write_run as write
            write(f"{args.catalog}.{args.schema}", result, recorder.metrics)
            print(f"{task}: logged {result.status} run to ops_run_log")
