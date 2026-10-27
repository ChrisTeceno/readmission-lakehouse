"""Snapshot the Gold tables the public dashboard shows to site/dashboard/data/*.json.

    uv run python scripts/export_dashboard_data.py [--profile readmission]

Reads `workspace.readmission` over the SQL warehouse and writes one JSON file per dashboard
page, plus `meta.json` with the export time and the job run whose `gold` task last succeeded.
Every query aggregates in SQL and selects only the columns the page shows. Hospital names come
from Contoso's own tables only: `gold_facility_condition_year.facility_name` (the CMS name) is
never selected, and no non-Contoso row carries a name. Rerun after any pipeline run; the
output replaces the previous snapshot.
"""

import argparse
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState

ROOT = Path(__file__).parent.parent
OUT = ROOT / "site" / "dashboard" / "data"
CATALOG = "workspace"
SCHEMA = "readmission"
WAREHOUSE = "Serverless Starter Warehouse"

# Contoso facility label, as the AI/BI dashboard shows it: "CH01 Alder Regional Medical Center".
FACILITY = "concat(contoso_facility_id, ' ', contoso_facility_name)"

QUERIES = {
    "scorecard": {
        "scorecard": f"""
            SELECT {FACILITY} AS facility, contoso_facility_id, facility_id AS ccn, measure,
                   fiscal_year, round(excess_readmission_ratio, 4) AS err,
                   round(penalty_cutoff, 4) AS penalty_cutoff, cutoff_is_approximate,
                   above_cutoff, round(err_change, 4) AS err_change, anomaly_flag
            FROM gold_contoso_scorecard
            ORDER BY fiscal_year, contoso_facility_id, measure""",
        "scorecard_summary": """
            SELECT fiscal_year,
                   COUNT(DISTINCT contoso_facility_id) AS facilities,
                   COUNT(DISTINCT CASE WHEN above_cutoff THEN contoso_facility_id END)
                     AS facilities_above,
                   SUM(CASE WHEN above_cutoff THEN 1 ELSE 0 END) AS conditions_above,
                   COUNT(excess_readmission_ratio) AS pairs_reported,
                   round(AVG(excess_readmission_ratio), 3) AS mean_err,
                   bool_or(cutoff_is_approximate) AS cutoff_is_approximate
            FROM gold_contoso_scorecard
            GROUP BY fiscal_year
            ORDER BY fiscal_year""",
    },
    "landscape": {
        # Sums and counts, so the page can average over any set of conditions.
        "err_trend": """
            SELECT hospital_group, fiscal_year, measure, COUNT(*) AS pairs,
                   round(SUM(err), 4) AS err_sum
            FROM (
              SELECT 'All hospitals' AS hospital_group, fiscal_year, measure,
                     excess_readmission_ratio AS err
              FROM gold_facility_condition_year
              WHERE excess_readmission_ratio IS NOT NULL
              UNION ALL
              SELECT 'Ohio', fiscal_year, measure, excess_readmission_ratio
              FROM gold_facility_condition_year
              WHERE excess_readmission_ratio IS NOT NULL AND state = 'OH'
              UNION ALL
              SELECT 'Contoso', fiscal_year, measure, excess_readmission_ratio
              FROM gold_contoso_scorecard
              WHERE excess_readmission_ratio IS NOT NULL
            )
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3""",
        "penalized_share": """
            SELECT fiscal_year, bool_or(cutoff_is_approximate) AS cutoff_is_approximate,
                   COUNT(*) AS hospitals,
                   SUM(CASE WHEN penalized THEN 1 ELSE 0 END) AS penalized,
                   round(AVG(CASE WHEN penalized THEN 1.0 ELSE 0.0 END), 3) AS penalized_share
            FROM gold_penalty_mart
            WHERE conditions_reported > 0
            GROUP BY fiscal_year
            ORDER BY fiscal_year""",
        "penalized_by_state": """
            SELECT state, fiscal_year, COUNT(*) AS hospitals,
                   SUM(CASE WHEN penalized THEN 1 ELSE 0 END) AS penalized,
                   round(AVG(CASE WHEN penalized THEN 1.0 ELSE 0.0 END), 3) AS penalized_share,
                   round(AVG(mean_err), 3) AS mean_err
            FROM gold_penalty_mart
            WHERE conditions_reported > 0
              AND state IS NOT NULL
              AND fiscal_year = (SELECT MAX(fiscal_year) FROM gold_penalty_mart)
            GROUP BY state, fiscal_year
            ORDER BY state""",
        # Hospital-condition pairs in the latest year by ERR minus the cutoff, 0.01 bins.
        "err_distribution": """
            SELECT fiscal_year, measure,
                   round(floor((excess_readmission_ratio - penalty_cutoff) * 100) / 100, 2)
                     AS err_minus_cutoff,
                   COUNT(*) AS pairs,
                   SUM(CASE WHEN above_cutoff THEN 1 ELSE 0 END) AS above
            FROM gold_facility_condition_year
            WHERE excess_readmission_ratio IS NOT NULL
              AND penalty_cutoff IS NOT NULL
              AND fiscal_year = (SELECT MAX(fiscal_year) FROM gold_facility_condition_year)
            GROUP BY 1, 2, 3
            ORDER BY 2, 3""",
    },
    "predictions": {
        "predictions_contoso": f"""
            SELECT {FACILITY} AS facility, c.contoso_facility_id, p.facility_id AS ccn,
                   p.measure, p.target_fiscal_year,
                   round(p.penalty_probability, 3) AS penalty_probability,
                   p.predicted_above_cutoff, p.above_cutoff_lag1 AS above_cutoff_last_year,
                   p.risk_decile, p.risk_rank, p.model_name, p.model_version, p.scored_at
            FROM gold_penalty_predictions p
            JOIN (
              SELECT DISTINCT contoso_facility_id, contoso_facility_name, facility_id
              FROM gold_contoso_scorecard
            ) c ON p.facility_id = c.facility_id
            ORDER BY p.risk_rank""",
        "predictions_by_measure": """
            SELECT measure, COUNT(*) AS pairs,
                   round(AVG(penalty_probability), 3) AS mean_probability,
                   SUM(CASE WHEN predicted_above_cutoff THEN 1 ELSE 0 END) AS predicted_above
            FROM gold_penalty_predictions
            GROUP BY measure
            ORDER BY measure""",
    },
    "followup": {
        "followup": f"""
            SELECT {FACILITY} AS facility, contoso_facility_id, facility_id AS ccn, measure,
                   fiscal_year, contoso_months, contoso_discharges, calls_attempted,
                   calls_completed, round(followup_rate, 3) AS followup_rate,
                   round(followup_within_72h_rate, 3) AS followup_within_72h_rate,
                   round(calls_completed / NULLIF(calls_attempted, 0), 3)
                     AS completed_of_attempted,
                   above_cutoff, cutoff_is_approximate
            FROM gold_contoso_scorecard
            WHERE contoso_months > 0
            ORDER BY fiscal_year, contoso_facility_id, measure""",
        "followup_facility": f"""
            SELECT {FACILITY} AS facility, contoso_facility_id, facility_id AS ccn, fiscal_year,
                   MAX(contoso_months) AS contoso_months,
                   SUM(contoso_discharges) AS discharges,
                   SUM(calls_completed) AS calls_completed,
                   round(SUM(calls_completed) / NULLIF(SUM(contoso_discharges), 0), 3)
                     AS followup_rate,
                   round(SUM(calls_completed_within_72h) / NULLIF(SUM(contoso_discharges), 0), 3)
                     AS followup_within_72h_rate,
                   SUM(CASE WHEN above_cutoff THEN 1 ELSE 0 END) AS conditions_above_cutoff
            FROM gold_contoso_scorecard
            GROUP BY 1, 2, 3, 4
            HAVING MAX(contoso_months) > 0
            ORDER BY fiscal_year, followup_rate""",
    },
    "penalty": {
        "penalty_dollars": f"""
            SELECT {FACILITY} AS facility, contoso_facility_id, facility_id AS ccn, fiscal_year,
                   round(est_penalty, 0) AS est_penalty,
                   round(est_base_operating_drg_payments, 0) AS est_base_payments,
                   payment_adjustment_factor, is_estimate
            FROM gold_penalty_dollars
            ORDER BY fiscal_year, contoso_facility_id""",
        "penalty_summary": """
            SELECT
              (SELECT round(SUM(est_penalty), 0) FROM gold_penalty_dollars
               WHERE fiscal_year = 2026) AS est_penalty_fy2026,
              (SELECT round(SUM(est_penalty), 0) FROM gold_penalty_dollars
               WHERE fiscal_year = 2027) AS est_penalty_fy2027,
              (SELECT MAX(break_even_drop_points) FROM gold_nurse_call_economics
               WHERE cost_level = 'central' AND err_pass_through = 0.5) AS break_even_half,
              (SELECT MAX(break_even_drop_points) FROM gold_nurse_call_economics
               WHERE cost_level = 'central' AND err_pass_through = 1.0) AS break_even_full,
              (SELECT round(MAX(annual_program_cost), 0) FROM gold_nurse_call_economics
               WHERE cost_level = 'central') AS central_program_cost""",
        "penalty_change": f"""
            SELECT {FACILITY} AS facility, contoso_facility_id, facility_id AS ccn, measure,
                   round(SUM(CASE WHEN fiscal_year = 2026 THEN est_penalty ELSE 0 END), 0)
                     AS est_penalty_fy2026,
                   round(SUM(CASE WHEN fiscal_year = 2027 THEN est_penalty ELSE 0 END), 0)
                     AS est_penalty_fy2027,
                   round(SUM(CASE WHEN fiscal_year = 2027 THEN est_penalty
                                  ELSE -est_penalty END), 0) AS est_change
            FROM gold_penalty_by_condition
            GROUP BY 1, 2, 3, 4
            HAVING SUM(est_penalty) > 0
            ORDER BY abs(est_change) DESC""",
        # Contoso's total FY2027 penalty avoided at each drop, the break-even curve.
        "penalty_sensitivity": """
            SELECT drop_points, err_pass_through,
                   round(SUM(est_penalty_avoided), 0) AS est_penalty_avoided
            FROM gold_penalty_sensitivity
            GROUP BY drop_points, err_pass_through
            ORDER BY err_pass_through, drop_points""",
        "nurse_call_scenarios": """
            SELECT effect_scenario, drop_points, effect_basis, cost_level,
                   CASE cost_level WHEN 'low' THEN 1 WHEN 'central' THEN 2 ELSE 3 END
                     AS cost_order,
                   err_pass_through, round(annual_program_cost, 0) AS annual_program_cost,
                   round(est_penalty_avoided, 0) AS est_penalty_avoided,
                   round(hospital_net, 0) AS hospital_net, break_even_drop_points,
                   round(readmissions_avoided, 0) AS readmissions_avoided,
                   round(payer_savings, 0) AS payer_savings
            FROM gold_nurse_call_economics
            ORDER BY drop_points, cost_order, err_pass_through""",
    },
    "pipeline": {
        "run_summary": """
            WITH runs AS (
              SELECT job_run_id, MIN(started_at) AS run_started, MAX(ended_at) AS run_ended,
                     COUNT(*) AS task_attempts,
                     SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_attempts
              FROM ops_run_log
              GROUP BY job_run_id
            )
            SELECT job_run_id, run_started, run_ended,
                   CASE WHEN failed_attempts > 0 THEN 'failed' ELSE 'succeeded' END
                     AS run_status,
                   task_attempts, failed_attempts,
                   round((unix_timestamp(current_timestamp()) - unix_timestamp(run_ended))
                         / 3600, 1) AS hours_since_run_ended,
                   (SELECT COUNT(*) FROM ops_dq_metrics WHERE NOT passed) AS failed_dq_checks
            FROM runs
            ORDER BY run_started DESC
            LIMIT 1""",
        # An error keeps its first line, cut to 160 characters, with URLs, storage paths and
        # HTML tags removed.
        "task_latest": r"""
            SELECT task, status, started_at, ended_at,
                   round((unix_timestamp(ended_at) - unix_timestamp(started_at)) / 60, 1)
                     AS minutes,
                   rows_written,
                   left(trim(regexp_replace(regexp_replace(split(error, '\n')[0],
                     '[a-z0-9]+://\\S+', '(url)'), '<[^>]*>', '')), 160) AS error,
                   job_run_id
            FROM (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY task ORDER BY ended_at DESC)
                AS attempt_rank
              FROM ops_run_log
            )
            WHERE attempt_rank = 1
            ORDER BY started_at""",
        "task_durations": """
            SELECT job_run_id, MIN(started_at) OVER (PARTITION BY job_run_id) AS run_started,
                   task, status,
                   round((unix_timestamp(ended_at) - unix_timestamp(started_at)) / 60, 1)
                     AS minutes
            FROM ops_run_log
            ORDER BY run_started, task""",
        "dq_failed": """
            SELECT job_run_id, task, table_name, metric, value, threshold, measured_at
            FROM ops_dq_metrics
            WHERE NOT passed
            ORDER BY measured_at""",
    },
}

GOLD_RUN = """
    SELECT job_run_id, ended_at
    FROM ops_run_log
    WHERE task = 'gold' AND status = 'succeeded'
    ORDER BY ended_at DESC
    LIMIT 1"""

INTEGER_TYPES = {"BYTE", "SHORT", "INT", "LONG"}
FLOAT_TYPES = {"FLOAT", "DOUBLE"}


def convert(value: str | None, type_name: str):
    """A JSON_ARRAY cell (always a string) as the JSON value its column type means."""
    if value is None:
        return None
    if type_name in INTEGER_TYPES:
        return int(value)
    if type_name in FLOAT_TYPES:
        return float(value)
    if type_name == "DECIMAL":
        number = Decimal(value)
        return int(number) if number == number.to_integral_value() else float(number)
    if type_name == "BOOLEAN":
        return value == "true"
    return value


def query(client: WorkspaceClient, warehouse_id: str, sql: str) -> list[dict]:
    response = client.statement_execution.execute_statement(
        statement=sql,
        warehouse_id=warehouse_id,
        catalog=CATALOG,
        schema=SCHEMA,
        disposition=Disposition.INLINE,
        format=Format.JSON_ARRAY,
        wait_timeout="50s",
    )
    while response.status.state in (StatementState.PENDING, StatementState.RUNNING):
        response = client.statement_execution.get_statement(response.statement_id)
    if response.status.state != StatementState.SUCCEEDED:
        raise SystemExit(f"query failed: {response.status.error}\n{sql}")
    columns = [(c.name, c.type_name.value) for c in response.manifest.schema.columns]
    rows = (response.result.data_array if response.result else None) or []
    return [
        {name: convert(cell, type_name) for (name, type_name), cell in zip(columns, row)}
        for row in rows
    ]


def write(path: Path, payload: dict) -> int:
    # One row per line: small, and a refresh diffs row by row.
    lines = []
    for name, rows in payload.items():
        body = ",\n".join("  " + json.dumps(row, separators=(",", ":")) for row in rows)
        lines.append(f"{json.dumps(name)}:[\n{body}\n]")
    text = "{\n" + ",\n".join(lines) + "\n}\n"
    path.write_text(text)
    return len(text.encode())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export the public dashboard's JSON snapshot.")
    parser.add_argument("--profile", default="readmission", help="Databricks CLI profile")
    args = parser.parse_args(argv)
    client = WorkspaceClient(profile=args.profile)
    warehouse = next(w for w in client.warehouses.list() if w.name == WAREHOUSE)

    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for page, datasets in QUERIES.items():
        payload = {
            name: query(client, warehouse.id, re.sub(r"\n {12}", "\n", sql))
            for name, sql in datasets.items()
        }
        size = write(OUT / f"{page}.json", payload)
        total += size
        counts = ", ".join(f"{name} {len(rows)}" for name, rows in payload.items())
        print(f"{page}.json: {size:,} bytes ({counts})")

    gold_run = query(client, warehouse.id, GOLD_RUN)[0]
    meta = {
        "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gold_job_run_id": gold_run["job_run_id"],
        "gold_run_ended": gold_run["ended_at"],
        "source": f"{CATALOG}.{SCHEMA}",
    }
    total += write(OUT / "meta.json", {"meta": [meta]})
    print(f"meta.json: gold from job run {meta['gold_job_run_id']}; {total:,} bytes in all")


if __name__ == "__main__":
    main()
