"""Bronze rows for the current CMS datasets: every column a string as received, plus metadata.

Headers become snake_case column names (`City/Town` is `city_town`; the datastore API writes
`citytown`), the same rule the historical PDC Bronze table uses.
"""

from datetime import datetime
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import types as T

from readmission.bronze.hrrp_history import snake_case
from readmission.bronze.metadata import INGEST_FIELDS, SHA256_FIELD, SOURCE_MODIFIED_FIELD
from readmission.ingest.cms_current import read_csv_rows

METADATA_FIELDS = [*INGEST_FIELDS, SHA256_FIELD, SOURCE_MODIFIED_FIELD]


def bronze_cms_dataset(
    spark: SparkSession,
    rows: list[dict[str, str]],
    *,
    source: str,
    batch_id: str,
    sha256: str,
    source_modified: str,
    ingested_at: datetime,
) -> DataFrame:
    columns = [snake_case(h) for h in rows[0]] if rows else []
    schema = T.StructType([T.StructField(c, T.StringType()) for c in columns] + METADATA_FIELDS)
    meta = (ingested_at, source, batch_id, sha256, source_modified)
    return spark.createDataFrame([tuple(r.values()) + meta for r in rows], schema)


def bronze_from_manifest(spark: SparkSession, manifest: dict, ingested_at: datetime) -> DataFrame:
    """Bronze rows for one landed batch, read from the CSV beside its manifest."""
    (entry,) = manifest["files"]
    rows = read_csv_rows(Path(manifest["dir"], entry["name"]))
    if len(rows) != entry["row_count"]:
        raise ValueError(
            f"{manifest['dir']}/{entry['name']}: read {len(rows)} rows, manifest says "
            f"{entry['row_count']}"
        )
    return bronze_cms_dataset(
        spark,
        rows,
        source=entry["url"],
        batch_id=manifest["batch_id"],
        sha256=entry["sha256"],
        source_modified=entry["modified"],
        ingested_at=ingested_at,
    )
