"""Ingest metadata columns every Bronze table carries.

`_ingested_at`, `_source` and `_batch_id` are on every row (SPEC.md, Code Style). Tables loaded
from the landing volume add `_sha256`, the landed file's hash from its manifest, so the load can
skip a re-fetch of unchanged data. `_source_modified` is the source's own version marker: the CMS
`modified` date, or the Delta version of a table copied from the workspace.
"""

from datetime import datetime

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

INGEST_FIELDS = [
    T.StructField("_ingested_at", T.TimestampType(), nullable=False),
    T.StructField("_source", T.StringType(), nullable=False),
    T.StructField("_batch_id", T.StringType(), nullable=False),
]
SHA256_FIELD = T.StructField("_sha256", T.StringType(), nullable=False)
SOURCE_MODIFIED_FIELD = T.StructField("_source_modified", T.StringType(), nullable=False)


def with_ingest_metadata(
    df: DataFrame,
    *,
    ingested_at: datetime,
    source: str,
    batch_id: str,
    source_modified: str,
) -> DataFrame:
    """Append the metadata columns to rows that are already a DataFrame."""
    return df.select(
        "*",
        F.lit(ingested_at).cast("timestamp").alias("_ingested_at"),
        F.lit(source).alias("_source"),
        F.lit(batch_id).alias("_batch_id"),
        F.lit(source_modified).alias("_source_modified"),
    )
