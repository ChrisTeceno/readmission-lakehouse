"""Bronze rows for Census ACS county data: values as received, plus ingest metadata.

The variable codes change between vintages (see `readmission.ingest.acs_county.variables`), so
each row keeps its header to value pairs in one `record` map, like the IPPS history table,
instead of a column set that would drift per vintage. `geography` says which response the row
came from: `county` or `county subdivision` (Connecticut towns).
"""

from datetime import datetime
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import types as T

from readmission.bronze.metadata import INGEST_FIELDS, SHA256_FIELD
from readmission.ingest.acs_county import read_census_rows

BRONZE_ACS_COUNTY_SCHEMA = T.StructType(
    [
        T.StructField("record", T.MapType(T.StringType(), T.StringType()), nullable=False),
        T.StructField("geography", T.StringType(), nullable=False),
        T.StructField("vintage", T.IntegerType(), nullable=False),
        *INGEST_FIELDS,
        SHA256_FIELD,
    ]
)


def bronze_acs_county(
    spark: SparkSession,
    rows: list[dict[str, str | None]],
    *,
    geography: str,
    vintage: int,
    source: str,
    batch_id: str,
    sha256: str,
    ingested_at: datetime,
) -> DataFrame:
    meta = (geography, vintage, ingested_at, source, batch_id, sha256)
    return spark.createDataFrame([(r, *meta) for r in rows], BRONZE_ACS_COUNTY_SCHEMA)


def bronze_from_manifest(spark: SparkSession, manifest: dict, ingested_at: datetime) -> DataFrame:
    """Bronze rows for one landed vintage, read from the responses beside its manifest."""
    frames = []
    for entry in manifest["files"]:
        rows = read_census_rows(Path(manifest["dir"], entry["name"]))
        if len(rows) != entry["row_count"]:
            raise ValueError(
                f"{manifest['dir']}/{entry['name']}: read {len(rows)} rows, manifest says "
                f"{entry['row_count']}"
            )
        frames.append(
            bronze_acs_county(
                spark,
                rows,
                geography=entry["geography"],
                vintage=entry["vintage"],
                source=entry["url"],
                batch_id=manifest["batch_id"],
                sha256=entry["sha256"],
                ingested_at=ingested_at,
            )
        )
    result = frames[0]
    for frame in frames[1:]:
        result = result.unionByName(frame)
    return result
