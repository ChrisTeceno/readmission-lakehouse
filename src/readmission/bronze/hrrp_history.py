"""Bronze rows for historical HRRP: values as received, plus ingest metadata.

PDC files share one long layout, so each header becomes a snake_case string column; the old
`Provider ID` / `Hospital Name` headers (FY2019 snapshots before 2019-10) stay as their own
columns and Silver coalesces them. IPPS files are wide and their headers change every year
(measure order, wording, typos, FY2027 peer group columns), so each row keeps its header to value
pairs in one `record` map instead of a column set that would drift per year.
"""

import re
from datetime import datetime

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import types as T

METADATA_FIELDS = [
    T.StructField("fiscal_year", T.IntegerType(), nullable=False),
    T.StructField("_ingested_at", T.TimestampType(), nullable=False),
    T.StructField("_source", T.StringType(), nullable=False),
    T.StructField("_batch_id", T.StringType(), nullable=False),
]

BRONZE_HRRP_HISTORY_IPPS_SCHEMA = T.StructType(
    [T.StructField("record", T.MapType(T.StringType(), T.StringType()), nullable=False)]
    + METADATA_FIELDS
)


def snake_case(header: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", header.strip().lower()).strip("_")


def bronze_hrrp_history_pdc(
    spark: SparkSession,
    rows: list[dict[str, str]],
    *,
    fiscal_year: int,
    source: str,
    batch_id: str,
    ingested_at: datetime,
) -> DataFrame:
    columns = [snake_case(h) for h in rows[0]] if rows else []
    schema = T.StructType([T.StructField(c, T.StringType()) for c in columns] + METADATA_FIELDS)
    meta = (fiscal_year, ingested_at, source, batch_id)
    return spark.createDataFrame([tuple(r.values()) + meta for r in rows], schema)


def bronze_hrrp_history_ipps(
    spark: SparkSession,
    rows: list[dict[str, str]],
    *,
    fiscal_year: int,
    source: str,
    batch_id: str,
    ingested_at: datetime,
) -> DataFrame:
    meta = (fiscal_year, ingested_at, source, batch_id)
    return spark.createDataFrame([(r, *meta) for r in rows], BRONZE_HRRP_HISTORY_IPPS_SCHEMA)
