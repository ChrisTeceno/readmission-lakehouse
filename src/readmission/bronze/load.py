"""Bronze load: the landing volume and the MTSamples table into append-only Bronze tables.

    readmission-bronze [--catalog workspace] [--schema readmission] [--landing DIR]

The `bronze` task of the `readmission_pipeline` job. It never calls the internet. Each landed
source appends only the batches `batches_to_load` lets through (not yet loaded by batch id, and
not a re-fetch of a file already loaded), so a second run with nothing new appends nothing.
"""

import argparse
from collections.abc import Callable
from datetime import UTC, datetime
from functools import reduce

from pyspark.sql import DataFrame, SparkSession

from readmission.bronze import clinical_notes, cms, hrrp_history
from readmission.ingest import cms_current
from readmission.ingest import hrrp_history as hrrp_history_ingest
from readmission.ingest.landing import LANDING_VOLUME, batches_to_load, list_manifests, new_batch_id

Builder = Callable[[SparkSession, dict, datetime], DataFrame]

LANDED_TABLES: dict[str, tuple[str, Builder]] = {
    cms_current.HRRP_SOURCE: ("bronze_hrrp", cms.bronze_from_manifest),
    cms_current.HOSPITAL_INFO_SOURCE: ("bronze_hospital_info", cms.bronze_from_manifest),
    cms_current.UNPLANNED_VISITS_SOURCE: ("bronze_unplanned_visits", cms.bronze_from_manifest),
    hrrp_history_ingest.PDC_SOURCE: (
        "bronze_hrrp_history_pdc",
        hrrp_history.bronze_from_manifest,
    ),
    hrrp_history_ingest.IPPS_SOURCE: (
        "bronze_hrrp_history_ipps",
        hrrp_history.bronze_from_manifest,
    ),
}
NOTES_TABLE = "bronze_clinical_notes"


def loaded_keys(spark: SparkSession, table: str) -> tuple[set[str], set[str]]:
    """Batch ids and file hashes already in a Bronze table."""
    if not spark.catalog.tableExists(table):
        return set(), set()
    rows = spark.table(table).select("_batch_id", "_sha256").distinct().collect()
    return {r["_batch_id"] for r in rows}, {r["_sha256"] for r in rows}


def append(df: DataFrame, table: str) -> None:
    """Bronze keeps whatever columns arrive, so a new CMS column widens the table."""
    df.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(table)


def load_landed(
    spark: SparkSession,
    root: str,
    source: str,
    table: str,
    build: Builder,
    ingested_at: datetime,
) -> int:
    """Append the pending batches of one landed source; returns the rows appended."""
    pending = batches_to_load(list_manifests(root, source), *loaded_keys(spark, table))
    if not pending:
        return 0
    frames = [build(spark, manifest, ingested_at) for manifest in pending]
    append(reduce(lambda a, b: a.unionByName(b, allowMissingColumns=True), frames), table)
    return sum(f["row_count"] for manifest in pending for f in manifest["files"])


def load_clinical_notes(spark: SparkSession, path: str, table: str, ingested_at: datetime) -> int:
    """Copy the notes table unless this Delta version is already in Bronze."""
    version = clinical_notes.delta_version(path)
    if spark.catalog.tableExists(table):
        loaded = spark.table(table).where(f"_source = '{path}' AND _source_modified = '{version}'")
        if not loaded.isEmpty():
            return 0
    notes = clinical_notes.bronze_clinical_notes(
        clinical_notes.read_notes(spark, path, version),
        source=path,
        version=version,
        batch_id=new_batch_id(ingested_at),
        ingested_at=ingested_at,
    )
    append(notes, table)
    return notes.count()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Load the landing volume into Bronze.")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--schema", default="readmission")
    parser.add_argument("--landing", default=LANDING_VOLUME)
    parser.add_argument("--notes-path", default=clinical_notes.NOTES_PATH)
    args = parser.parse_args(argv)
    spark = SparkSession.builder.getOrCreate()
    ingested_at = datetime.now(UTC)
    for source, (table, build) in LANDED_TABLES.items():
        name = f"{args.catalog}.{args.schema}.{table}"
        appended = load_landed(spark, args.landing, source, name, build, ingested_at)
        print(f"{name}: appended {appended} rows from {source}")
    name = f"{args.catalog}.{args.schema}.{NOTES_TABLE}"
    appended = load_clinical_notes(spark, args.notes_path, name, ingested_at)
    print(f"{name}: appended {appended} rows from {args.notes_path}")


if __name__ == "__main__":
    main()
