"""bronze_clinical_notes: the MTSamples notes copied from the workspace, plus ingest metadata.

The source is a Delta table already in the workspace (`mtsamples_clean`, partitioned by
`medical_specialty`), so there is no fetch. It is read only, at a pinned version, and never
modified. Columns keep their source types; `_source_modified` holds the Delta version copied, so a
load skips when the source has not been rewritten since.
"""

from datetime import datetime
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession

from readmission.bronze.metadata import with_ingest_metadata

NOTES_PATH = "/Volumes/workspace/default/raw-data/mtsamples_clean"


def delta_version(path: str) -> int:
    """Latest committed version, from the commit files in `_delta_log`. Volume paths are plain
    file paths on Databricks compute, so this needs no Spark."""
    return max(int(p.stem) for p in Path(path, "_delta_log").glob("*.json"))


def read_notes(spark: SparkSession, path: str, version: int) -> DataFrame:
    return spark.read.format("delta").option("versionAsOf", version).load(path)


def bronze_clinical_notes(
    notes: DataFrame, *, source: str, version: int, batch_id: str, ingested_at: datetime
) -> DataFrame:
    return with_ingest_metadata(
        notes,
        ingested_at=ingested_at,
        source=source,
        batch_id=batch_id,
        source_modified=str(version),
    )
