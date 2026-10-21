"""Bronze rows for historical HRRP: values as received, plus ingest metadata.

PDC files share one long layout, so each header becomes a snake_case string column; the old
`Provider ID` / `Hospital Name` headers (FY2019 snapshots before 2019-10) stay as their own
columns and Silver coalesces them. IPPS files are wide and their headers change every year
(measure order, wording, typos, FY2027 peer group columns), so each row keeps its header to value
pairs in one `record` map instead of a column set that would drift per year.

`_sha256` is the landed file's hash from the manifest, so the load can skip a re-fetch of
unchanged data (see `readmission.ingest.landing.batches_to_load`).
"""

import re
from datetime import datetime
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import types as T

from readmission.bronze.metadata import INGEST_FIELDS, SHA256_FIELD
from readmission.ingest.hrrp_history import PDC_SOURCE, read_ipps_rows, read_pdc_rows

METADATA_FIELDS = [
    T.StructField("fiscal_year", T.IntegerType(), nullable=False),
    *INGEST_FIELDS,
    SHA256_FIELD,
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
    sha256: str,
    ingested_at: datetime,
) -> DataFrame:
    columns = [snake_case(h) for h in rows[0]] if rows else []
    schema = T.StructType([T.StructField(c, T.StringType()) for c in columns] + METADATA_FIELDS)
    meta = (fiscal_year, ingested_at, source, batch_id, sha256)
    return spark.createDataFrame([tuple(r.values()) + meta for r in rows], schema)


def bronze_hrrp_history_ipps(
    spark: SparkSession,
    rows: list[dict[str, str]],
    *,
    fiscal_year: int,
    source: str,
    batch_id: str,
    sha256: str,
    ingested_at: datetime,
) -> DataFrame:
    meta = (fiscal_year, ingested_at, source, batch_id, sha256)
    return spark.createDataFrame([(r, *meta) for r in rows], BRONZE_HRRP_HISTORY_IPPS_SCHEMA)


def bronze_from_manifest(spark: SparkSession, manifest: dict, ingested_at: datetime) -> DataFrame:
    """Bronze rows for one landed batch, read from the files beside its manifest."""
    is_pdc = manifest["source"] == PDC_SOURCE
    frames = []
    for entry in manifest["files"]:
        path = Path(manifest["dir"], entry["name"])
        rows = read_pdc_rows(path) if is_pdc else read_ipps_rows(path)
        build = bronze_hrrp_history_pdc if is_pdc else bronze_hrrp_history_ipps
        frames.append(
            build(
                spark,
                rows,
                fiscal_year=entry["fiscal_year"],
                source=entry["source_ref"],
                batch_id=manifest["batch_id"],
                sha256=entry["sha256"],
                ingested_at=ingested_at,
            )
        )
    result = frames[0]
    for frame in frames[1:]:
        result = result.unionByName(frame, allowMissingColumns=True)
    return result
