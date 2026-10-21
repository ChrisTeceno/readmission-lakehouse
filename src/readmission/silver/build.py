"""Silver build: Bronze into typed Silver tables, with quarantine, constraints and expectations.

    readmission-silver [--catalog workspace] [--schema readmission]

The `silver` task of the `readmission_pipeline` job, after `bronze`. Every run rebuilds each
Silver table from Bronze. Rows that do not parse or would break a constraint replace that
table's rows in `silver_quarantine` and never fail the run; an expectation that fails (see
`readmission.quality.expectations`) fails the run after the other tables are built, and leaves
its own table as it was. Comments, tags and the analyst grant are applied after each write.
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.quality.constraints import add_constraint_statement
from readmission.quality.expectations import (
    ExpectationFailed,
    check_err_null_rate,
    check_row_count,
)
from readmission.silver import (
    clinical_notes,
    contoso,
    hospital_info,
    hrrp,
    hrrp_history,
    unplanned_visits,
)
from readmission.silver.governance import (
    TableDoc,
    comment_statements,
    grant_statement,
    tag_statement,
)
from readmission.silver.quarantine import (
    QUARANTINE_TABLE,
    SILVER_QUARANTINE_DOC,
    SILVER_QUARANTINE_SCHEMA,
)


@dataclass(frozen=True)
class SilverTable:
    name: str
    schema: T.StructType
    doc: TableDoc
    constraints: list[str]
    bronze: list[str]
    build: Callable[..., tuple[DataFrame, DataFrame]]

    @property
    def has_err(self) -> bool:
        return "excess_readmission_ratio" in self.schema.fieldNames()


SILVER_TABLES = [
    SilverTable(
        hrrp.TABLE,
        hrrp.SILVER_HRRP_SCHEMA,
        hrrp.SILVER_HRRP_DOC,
        hrrp.CONSTRAINTS,
        ["bronze_hrrp"],
        hrrp.silver_hrrp,
    ),
    SilverTable(
        hrrp_history.TABLE,
        hrrp_history.SILVER_HRRP_HISTORY_SCHEMA,
        hrrp_history.SILVER_HRRP_HISTORY_DOC,
        hrrp_history.CONSTRAINTS,
        ["bronze_hrrp_history_pdc", "bronze_hrrp_history_ipps"],
        hrrp_history.silver_hrrp_history_checked,
    ),
    SilverTable(
        hospital_info.TABLE,
        hospital_info.SILVER_HOSPITAL_INFO_SCHEMA,
        hospital_info.SILVER_HOSPITAL_INFO_DOC,
        hospital_info.CONSTRAINTS,
        ["bronze_hospital_info"],
        hospital_info.silver_hospital_info,
    ),
    SilverTable(
        unplanned_visits.TABLE,
        unplanned_visits.SILVER_UNPLANNED_VISITS_SCHEMA,
        unplanned_visits.SILVER_UNPLANNED_VISITS_DOC,
        unplanned_visits.CONSTRAINTS,
        ["bronze_unplanned_visits"],
        unplanned_visits.silver_unplanned_visits,
    ),
    SilverTable(
        clinical_notes.TABLE,
        clinical_notes.SILVER_CLINICAL_NOTES_SCHEMA,
        clinical_notes.SILVER_CLINICAL_NOTES_DOC,
        [],
        ["bronze_clinical_notes"],
        clinical_notes.silver_clinical_notes,
    ),
]


def ensure_table(spark: SparkSession, name: str, schema: T.StructType, constraints: list[str]):
    """Create the table from its explicit schema (NOT NULL columns and column comments included)
    and add any CHECK constraint it does not have yet."""
    if not spark.catalog.tableExists(name):
        spark.createDataFrame([], schema).write.format("delta").saveAsTable(name)
    properties = {r["key"] for r in spark.sql(f"SHOW TBLPROPERTIES {name}").collect()}
    for constraint in constraints:
        if f"delta.constraints.{constraint}" not in properties:
            spark.sql(add_constraint_statement(name, constraint))


def row_count(spark: SparkSession, name: str) -> int | None:
    return spark.table(name).count() if spark.catalog.tableExists(name) else None


def build_table(spark: SparkSession, table: SilverTable, prefix: str) -> tuple[int, int]:
    """Rebuild one Silver table and its quarantine rows; returns (rows, quarantined)."""
    name = f"{prefix}.{table.name}"
    quarantine = f"{prefix}.{QUARANTINE_TABLE}"
    rows, rejected = table.build(*[spark.table(f"{prefix}.{b}") for b in table.bronze])

    rejected.writeTo(quarantine).overwrite(F.col("table_name") == table.name)
    quarantined = spark.table(quarantine).where(F.col("table_name") == table.name).count()

    err_null = F.count_if(F.col("excess_readmission_ratio").isNull()) if table.has_err else F.lit(0)
    stats = rows.agg(F.count(F.lit(1)).alias("total"), err_null.alias("err_null")).first()
    check_row_count(name, row_count(spark, name), stats["total"])
    if table.has_err:
        check_err_null_rate(name, stats["err_null"], stats["total"])

    ensure_table(spark, name, table.schema, table.constraints)
    rows.writeTo(name).overwrite(F.lit(True))
    return stats["total"], quarantined


def quarantine_contoso(spark: SparkSession, prefix: str, table: str) -> int:
    """Quarantine the Contoso rows the `contoso` task left out of `silver_contoso_<table>`."""
    name = f"silver_contoso_{table}"
    quarantine = f"{prefix}.{QUARANTINE_TABLE}"
    rejects = contoso.contoso_rejects(spark.table(f"{prefix}.bronze_contoso_{table}"), table)
    rejects.writeTo(quarantine).overwrite(F.col("table_name") == name)
    return spark.table(quarantine).where(F.col("table_name") == name).count()


def apply_governance(spark: SparkSession, name: str, schema: T.StructType, doc: TableDoc):
    for statement in comment_statements(name, schema, doc):
        spark.sql(statement)
    spark.sql(tag_statement(name, doc))
    try:
        spark.sql(grant_statement(name))
    except Exception as e:
        # Free Edition has no account console, so there is no Unity Catalog group to grant to;
        # docs/governance.md records the statement and why it cannot run here.
        if "PRINCIPAL_DOES_NOT_EXIST" not in str(e):
            raise
        print(f"{name}: skipped grant, no Unity Catalog group named in: {grant_statement(name)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the Silver tables from Bronze.")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--schema", default="readmission")
    args = parser.parse_args(argv)
    spark = SparkSession.builder.getOrCreate()
    prefix = f"{args.catalog}.{args.schema}"
    quarantine = f"{prefix}.{QUARANTINE_TABLE}"
    ensure_table(spark, quarantine, SILVER_QUARANTINE_SCHEMA, [])
    failures = []
    for table in SILVER_TABLES:
        name = f"{prefix}.{table.name}"
        try:
            rows, quarantined = build_table(spark, table, prefix)
        except ExpectationFailed as e:
            failures.append(str(e))
            print(f"{name}: expectation failed, table left as it was: {e}")
            continue
        apply_governance(spark, name, table.schema, table.doc)
        print(f"{name}: {rows} rows, {quarantined} quarantined")
    for table in contoso.CHECKED_TABLES:
        quarantined = quarantine_contoso(spark, prefix, table)
        print(f"{prefix}.silver_contoso_{table}: {quarantined} quarantined")
    apply_governance(spark, quarantine, SILVER_QUARANTINE_SCHEMA, SILVER_QUARANTINE_DOC)
    if failures:
        raise ExpectationFailed("; ".join(failures))


if __name__ == "__main__":
    main()
