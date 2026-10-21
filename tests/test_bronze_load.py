from datetime import UTC, datetime
from pathlib import Path

import pytest
from pyspark.sql import types as T

from readmission.bronze import clinical_notes, cms, hrrp_history
from readmission.bronze.load import load_clinical_notes, load_landed
from readmission.bronze.metadata import with_ingest_metadata
from readmission.ingest.landing import LocalLander, land_manifest, sha256_hex

FIXTURES = Path(__file__).parent / "fixtures"
INGESTED_AT = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
CMS_METADATA = ["_ingested_at", "_source", "_batch_id", "_sha256", "_source_modified"]
HRRP_URL = "https://data.cms.gov/provider-data/sites/default/files/resources/x/hrrp.csv"


def land_csv(root, source: str, batch_id: str, fixture: Path, modified: str) -> None:
    lander = LocalLander(str(root))
    data = fixture.read_bytes()
    lander.land_file(source, batch_id, fixture.name, data)
    entry = {
        "name": fixture.name,
        "url": HRRP_URL,
        "dataset_id": "9n3s-kdb3",
        "modified": modified,
        "sha256": sha256_hex(data),
        "row_count": len(data.splitlines()) - 1,
    }
    land_manifest(lander, source, batch_id, [entry])


@pytest.fixture
def table_name(spark, request):
    name = f"bronze_{request.node.name}"
    yield name
    spark.sql(f"DROP TABLE IF EXISTS {name}")


def test_cms_rows_become_all_string_columns_plus_metadata(spark):
    rows = [
        {"Facility ID": "010001", "City/Town": "DOTHAN", "Footnote": ""},
        {"Facility ID": "010005", "City/Town": "BOAZ", "Footnote": "5"},
    ]

    df = cms.bronze_cms_dataset(
        spark,
        rows,
        source=HRRP_URL,
        batch_id="b1",
        sha256="h1",
        source_modified="2026-01-26",
        ingested_at=INGESTED_AT,
    )

    assert df.columns == ["facility_id", "city_town", "footnote", *CMS_METADATA]
    data_fields = [f for f in df.schema.fields if not f.name.startswith("_")]
    assert all(f.dataType == T.StringType() for f in data_fields)
    first = df.collect()[0]
    assert (first.facility_id, first.footnote) == ("010001", "")
    assert (first._source, first._batch_id, first._sha256, first._source_modified) == (
        HRRP_URL,
        "b1",
        "h1",
        "2026-01-26",
    )


def test_metadata_fields_are_shared_with_history_tables():
    history = [f.name for f in hrrp_history.METADATA_FIELDS]
    current = [f.name for f in cms.METADATA_FIELDS]

    assert history == ["fiscal_year", "_ingested_at", "_source", "_batch_id", "_sha256"]
    assert current[:4] == history[1:]


def test_with_ingest_metadata_appends_columns(spark):
    df = spark.createDataFrame([(1, "a")], "id int, text string")

    result = with_ingest_metadata(
        df, ingested_at=INGESTED_AT, source="/v/t", batch_id="b1", source_modified="2"
    )

    assert result.columns == [
        "id",
        "text",
        "_ingested_at",
        "_source",
        "_batch_id",
        "_source_modified",
    ]
    assert dict(result.dtypes)["_ingested_at"] == "timestamp"
    assert result.first()["_source_modified"] == "2"


def test_load_landed_is_idempotent(spark, tmp_path, table_name):
    fixture = FIXTURES / "cms_current" / "hrrp.csv"
    land_csv(tmp_path, "cms_hrrp", "b1", fixture, "2026-01-26")

    def load():
        return load_landed(
            spark, str(tmp_path), "cms_hrrp", table_name, cms.bronze_from_manifest, INGESTED_AT
        )

    assert load() == 6
    assert load() == 0
    # A re-fetch of byte-identical data under a new batch id is skipped too.
    land_csv(tmp_path, "cms_hrrp", "b2", fixture, "2026-01-26")
    assert load() == 0
    table = spark.table(table_name)
    assert table.count() == 6
    assert {r._batch_id for r in table.select("_batch_id").collect()} == {"b1"}
    assert table.where("facility_id = '010001'").count() == 6


def test_load_landed_appends_a_changed_batch(spark, tmp_path, table_name):
    land_csv(tmp_path, "cms_hrrp", "b1", FIXTURES / "cms_current" / "hrrp.csv", "2026-01-26")
    load_landed(spark, str(tmp_path), "cms_hrrp", table_name, cms.bronze_from_manifest, INGESTED_AT)
    changed = tmp_path / "changed" / "hrrp.csv"
    changed.parent.mkdir()
    lines = (FIXTURES / "cms_current" / "hrrp.csv").read_text().splitlines()
    changed.write_text("\n".join(lines[:3]) + "\n")
    land_csv(tmp_path, "cms_hrrp", "b2", changed, "2026-11-01")

    appended = load_landed(
        spark, str(tmp_path), "cms_hrrp", table_name, cms.bronze_from_manifest, INGESTED_AT
    )

    assert appended == 2
    counts = spark.table(table_name).groupBy("_source_modified").count().collect()
    assert {r["_source_modified"]: r["count"] for r in counts} == {
        "2026-01-26": 6,
        "2026-11-01": 2,
    }


def test_load_landed_rejects_a_row_count_mismatch(spark, tmp_path, table_name):
    fixture = FIXTURES / "cms_current" / "hrrp.csv"
    lander = LocalLander(str(tmp_path))
    lander.land_file("cms_hrrp", "b1", fixture.name, fixture.read_bytes())
    entry = {"name": fixture.name, "url": HRRP_URL, "modified": "m", "sha256": "h", "row_count": 7}
    land_manifest(lander, "cms_hrrp", "b1", [entry])

    with pytest.raises(ValueError, match="read 6 rows, manifest says 7"):
        load_landed(
            spark, str(tmp_path), "cms_hrrp", table_name, cms.bronze_from_manifest, INGESTED_AT
        )


def notes_fixture(spark, path: Path, rewrites: int = 0) -> None:
    """A tiny copy of mtsamples_clean: same schema, partitioned by medical_specialty."""
    rows = [
        (1, "Consult for chest pain.", " Cardiovascular / Pulmonary", "Chest Pain", "HPI ...", "x"),
        (2, "Knee replacement.", " Orthopedic", "TKA", "PROCEDURE ...", None),
        (3, "Discharge summary.", " Discharge Summary", "DC", "DISCHARGE ...", "y"),
    ]
    schema = (
        "id int, description string, medical_specialty string, sample_name string, "
        "transcription string, keywords string"
    )
    df = spark.createDataFrame(rows, schema)
    for _ in range(rewrites + 1):
        df.write.format("delta").mode("overwrite").partitionBy("medical_specialty").save(str(path))


def test_clinical_notes_copy_skips_an_unchanged_version(spark, tmp_path, table_name):
    source = tmp_path / "mtsamples_clean"
    notes_fixture(spark, source)

    assert load_clinical_notes(spark, str(source), table_name, INGESTED_AT) == 3
    assert load_clinical_notes(spark, str(source), table_name, INGESTED_AT) == 0

    table = spark.table(table_name)
    assert table.count() == 3
    assert table.columns[:6] == [
        "id",
        "description",
        "medical_specialty",
        "sample_name",
        "transcription",
        "keywords",
    ]
    first = table.where("id = 1").first()
    assert first["medical_specialty"] == " Cardiovascular / Pulmonary"
    assert (first["_source"], first["_source_modified"]) == (str(source), "0")
    assert first["_batch_id"] == "20261021T200000Z"


def test_clinical_notes_copy_appends_a_rewritten_source(spark, tmp_path, table_name):
    source = tmp_path / "mtsamples_clean"
    notes_fixture(spark, source)
    load_clinical_notes(spark, str(source), table_name, INGESTED_AT)
    notes_fixture(spark, source, rewrites=1)

    assert clinical_notes.delta_version(str(source)) == 2
    assert load_clinical_notes(spark, str(source), table_name, INGESTED_AT) == 3
    versions = spark.table(table_name).groupBy("_source_modified").count().collect()
    assert {r["_source_modified"]: r["count"] for r in versions} == {"0": 3, "2": 3}


def test_clinical_notes_source_is_never_modified(spark, tmp_path, table_name):
    source = tmp_path / "mtsamples_clean"
    notes_fixture(spark, source)
    before = sorted(p.relative_to(source) for p in source.rglob("*"))

    load_clinical_notes(spark, str(source), table_name, INGESTED_AT)

    assert sorted(p.relative_to(source) for p in source.rglob("*")) == before
    assert clinical_notes.delta_version(str(source)) == 0
