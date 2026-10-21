"""silver_quarantine: rows a Silver table rejected, with the rule they broke.

A rejected row never fails the run. It lands here with its table, business key, the Bronze row
as JSON and its batch id, and the Silver table is built from the rows that passed.
"""

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.silver.governance import TableDoc, field, silver_tags
from readmission.silver.normalize import (
    latest_per_key,
    parse_cms_date,
    suppressed_to_null,
    try_cast,
)

QUARANTINE_TABLE = "silver_quarantine"

SILVER_QUARANTINE_SCHEMA = T.StructType(
    [
        field("table_name", T.StringType(), "Silver table that rejected the row", nullable=False),
        field("business_key", T.StringType(), "Business key values as received, joined with |"),
        field("raw_row", T.StringType(), "The Bronze row as JSON, values as received"),
        field("rule", T.StringType(), "First validation rule the row broke", nullable=False),
        field("_batch_id", T.StringType(), "Bronze batch the row came from"),
    ]
)

SILVER_QUARANTINE_DOC = TableDoc(
    comment=(
        "Rows rejected by the Silver build: values that did not parse or broke a Silver "
        "constraint. Rebuilt per table on every run, so it holds the current rejects."
    ),
    tags=silver_tags("cms,mtsamples"),
)

Rule = tuple[str, Column]


def raw_row(bronze: DataFrame) -> Column:
    return F.to_json(F.struct(*[F.col(f"`{c}`") for c in bronze.columns]))


def first_violation(rules: list[Rule]) -> Column:
    """Name of the first rule whose condition holds, or null. A null condition counts as a pass,
    the same way a Delta CHECK constraint treats it."""
    result = F.lit(None).cast("string")
    for name, broken in reversed(rules):
        result = F.when(F.coalesce(broken, F.lit(False)), F.lit(name)).otherwise(result)
    return result


def missing_rules(columns: list[str]) -> list[Rule]:
    return [(f"missing_{c}", F.col(c).isNull()) for c in columns]


def parse_failed(raw: str, parsed: str) -> Column:
    """A value that survived suppression handling but did not parse."""
    return F.col(raw).isNotNull() & F.col(parsed).isNull()


def with_parsed(df: DataFrame, columns: list[tuple[str, str, str]]) -> tuple[DataFrame, list[Rule]]:
    """Parse Bronze strings into typed columns, one (Silver name, Bronze column, type) each.

    Suppression codes become null first; a value left over that does not parse becomes null too
    and breaks the rule `unparseable_<Silver name>`. Dates use the CMS MM/dd/yyyy format.
    """
    rules = []
    for name, source, data_type in columns:
        received = f"_received_{name}"
        df = df.withColumn(received, suppressed_to_null(source))
        parsed = parse_cms_date(received) if data_type == "date" else try_cast(received, data_type)
        df = df.withColumn(name, parsed)
        rules.append((f"unparseable_{name}", parse_failed(received, name)))
    return df, rules


def split_rows(
    checked: DataFrame,
    *,
    table: str,
    schema: T.StructType,
    key: list[str],
    raw_key: list[str] | None = None,
) -> tuple[DataFrame, DataFrame]:
    """Split rows carrying `_violation` and `_raw` into (Silver rows, quarantine rows).

    Silver rows are deduplicated on `key`, keeping the latest batch, and conformed to `schema`.
    `raw_key` names the columns that make up the quarantine business key when they differ from
    `key` (for example the measure name as received, before it was mapped).
    """
    passed = latest_per_key(checked.where(F.col("_violation").isNull()), key)
    rows = passed.select(*[F.col(f.name).cast(f.dataType).alias(f.name) for f in schema])
    rejected = checked.where(F.col("_violation").isNotNull()).select(
        F.lit(table).alias("table_name"),
        F.concat_ws("|", *[F.col(c).cast("string") for c in raw_key or key]).alias("business_key"),
        F.col("_raw").alias("raw_row"),
        F.col("_violation").alias("rule"),
        F.col("_batch_id"),
    )
    return rows, rejected
