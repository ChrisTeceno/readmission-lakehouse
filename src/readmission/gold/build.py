"""Gold build: facility-condition facts, penalty mart, Contoso scorecard, model features, and
Contoso's estimated penalty dollars and nurse-call economics.

    readmission-gold [--catalog workspace] [--schema readmission]

The `gold` task of the `readmission_pipeline` job, after `silver`. Every run rebuilds each Gold
table from Silver and re-applies comments, tags and the analyst grant the way Silver does
(`readmission.silver.build.apply_governance`), and documents the ops tables. The fact table
must hold exactly one row per `silver_hrrp_history` row; any other count fails the run before
anything is written, so the previous Gold tables stay in place.
"""

import argparse

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.gold import (
    contoso_scorecard,
    facility_condition,
    features,
    nurse_call_economics,
    penalty_dollars,
    penalty_mart,
)
from readmission.ops import RunRecorder, add_ops_arguments, track_run
from readmission.ops.tables import OPS_TABLES, create_statement
from readmission.quality.expectations import check_same_row_count
from readmission.silver.build import apply_governance
from readmission.silver.governance import TableDoc

# The share of fact rows with an anomaly flag above which the run records a failed metric
# without failing: on the 2026-10-21 data 1.2 percent of rows are flagged (3,127 of 266,159),
# most of them discharge jumps. A share several times that points at a broken input, such as
# a file whose discharge column changed meaning, rather than at hospitals.
MAX_ANOMALY_RATE = 0.05


def write_table(
    spark: SparkSession,
    name: str,
    df: DataFrame,
    schema: T.StructType,
    doc: TableDoc,
    run: RunRecorder,
) -> int:
    """Gold tables are rebuilt whole, so a table whose columns no longer match its schema is
    replaced (a Delta replace, which keeps the table's history) rather than migrated."""
    if (
        not spark.catalog.tableExists(name)
        or spark.table(name).schema.fieldNames() != schema.fieldNames()
    ):
        spark.sql(create_statement(name, schema, replace=True))
    df.writeTo(name).overwrite(F.lit(True))
    rows = spark.table(name).count()
    apply_governance(spark, name, schema, doc)
    run.wrote(name, rows)
    print(f"{name}: {rows} rows")
    return rows


def build_gold(spark: SparkSession, prefix: str, run: RunRecorder) -> None:
    def table(name: str) -> DataFrame:
        return spark.table(f"{prefix}.{name}")

    history = table("silver_hrrp_history")
    facts = facility_condition.gold_facility_condition_year(history, table("silver_hospital_info"))
    name = f"{prefix}.{facility_condition.TABLE}"
    expected = history.count()
    stats = facts.agg(
        F.count(F.lit(1)).alias("rows"), F.count_if("anomaly_flag").alias("anomalies")
    ).first()
    run.check(
        name,
        "row_count_vs_silver",
        stats["rows"],
        expected,
        lambda: check_same_row_count(name, expected, stats["rows"]),
    )
    write_table(
        spark,
        name,
        facts,
        facility_condition.GOLD_FACILITY_CONDITION_YEAR_SCHEMA,
        facility_condition.GOLD_FACILITY_CONDITION_YEAR_DOC,
        run,
    )
    anomaly_rate = stats["anomalies"] / stats["rows"] if stats["rows"] else 0.0
    run.metric(
        name, "anomaly_rate", anomaly_rate, MAX_ANOMALY_RATE, anomaly_rate <= MAX_ANOMALY_RATE
    )

    facts = table(facility_condition.TABLE)
    contoso = table("silver_contoso_facilities")
    write_table(
        spark,
        f"{prefix}.{penalty_mart.TABLE}",
        penalty_mart.gold_penalty_mart(facts, contoso),
        penalty_mart.GOLD_PENALTY_MART_SCHEMA,
        penalty_mart.GOLD_PENALTY_MART_DOC,
        run,
    )
    write_table(
        spark,
        f"{prefix}.{contoso_scorecard.TABLE}",
        contoso_scorecard.gold_contoso_scorecard(
            facts,
            contoso,
            table("silver_contoso_discharges"),
            table("silver_contoso_followup_calls"),
        ),
        contoso_scorecard.GOLD_CONTOSO_SCORECARD_SCHEMA,
        contoso_scorecard.GOLD_CONTOSO_SCORECARD_DOC,
        run,
    )
    write_table(
        spark,
        f"{prefix}.{features.TABLE}",
        features.gold_features(
            facts,
            table("silver_hospital_info"),
            table("silver_hospital_county"),
            table("silver_acs_county"),
        ),
        features.GOLD_FEATURES_SCHEMA,
        features.GOLD_FEATURES_DOC,
        run,
    )
    payment, measures = table("silver_hrrp_payment"), table("silver_hrrp_payment_measure")
    write_table(
        spark,
        f"{prefix}.{penalty_dollars.TABLE}",
        penalty_dollars.gold_penalty_dollars(
            payment, measures, table("silver_ipps_impact"), contoso
        ),
        penalty_dollars.GOLD_PENALTY_DOLLARS_SCHEMA,
        penalty_dollars.GOLD_PENALTY_DOLLARS_DOC,
        run,
    )
    write_table(
        spark,
        f"{prefix}.{penalty_dollars.CONDITION_TABLE}",
        penalty_dollars.gold_penalty_by_condition(table(penalty_dollars.TABLE), measures),
        penalty_dollars.GOLD_PENALTY_BY_CONDITION_SCHEMA,
        penalty_dollars.GOLD_PENALTY_BY_CONDITION_DOC,
        run,
    )
    write_table(
        spark,
        f"{prefix}.{penalty_dollars.SENSITIVITY_TABLE}",
        penalty_dollars.gold_penalty_sensitivity(table(penalty_dollars.TABLE), measures, payment),
        penalty_dollars.GOLD_PENALTY_SENSITIVITY_SCHEMA,
        penalty_dollars.GOLD_PENALTY_SENSITIVITY_DOC,
        run,
    )
    write_table(
        spark,
        f"{prefix}.{nurse_call_economics.TABLE}",
        nurse_call_economics.gold_nurse_call_economics(table(penalty_dollars.SENSITIVITY_TABLE)),
        nurse_call_economics.GOLD_NURSE_CALL_ECONOMICS_SCHEMA,
        nurse_call_economics.GOLD_NURSE_CALL_ECONOMICS_DOC,
        run,
    )
    for ops_table, schema, doc in OPS_TABLES:
        name = f"{prefix}.{ops_table}"
        spark.sql(create_statement(name, schema))
        apply_governance(spark, name, schema, doc)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the Gold tables from Silver.")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--schema", default="readmission")
    add_ops_arguments(parser)
    args = parser.parse_args(argv)
    with track_run("gold", args) as run:
        spark = SparkSession.builder.getOrCreate()
        build_gold(spark, f"{args.catalog}.{args.schema}", run)


if __name__ == "__main__":
    main()
