"""Bronze rows for the IPPS impact files: each row's header to value pairs in one `record` map.

The impact file's columns are named after the year (`FY 2027 Wage Index`, `CASETA44`), so a
map keeps one schema across years, as `bronze_hrrp_history_ipps` does.
"""

from datetime import datetime
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession

from readmission.bronze.hrrp_history import bronze_hrrp_history_ipps
from readmission.ingest.ipps_impact import read_impact_rows


def bronze_from_manifest(spark: SparkSession, manifest: dict, ingested_at: datetime) -> DataFrame:
    """Bronze rows for one landed batch, read from the files beside its manifest."""
    frames = [
        bronze_hrrp_history_ipps(
            spark,
            read_impact_rows(Path(manifest["dir"], entry["name"])),
            fiscal_year=entry["fiscal_year"],
            source=entry["source_ref"],
            batch_id=manifest["batch_id"],
            sha256=entry["sha256"],
            ingested_at=ingested_at,
        )
        for entry in manifest["files"]
    ]
    result = frames[0]
    for frame in frames[1:]:
        result = result.unionByName(frame)
    return result
