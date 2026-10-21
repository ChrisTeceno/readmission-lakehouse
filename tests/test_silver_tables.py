import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from readmission.bronze import cms
from readmission.bronze.metadata import with_ingest_metadata
from readmission.ingest.cms_current import read_csv_rows
from readmission.silver.clinical_notes import SILVER_CLINICAL_NOTES_SCHEMA, silver_clinical_notes
from readmission.silver.hospital_info import SILVER_HOSPITAL_INFO_SCHEMA, silver_hospital_info
from readmission.silver.hrrp import SILVER_HRRP_SCHEMA, silver_hrrp
from readmission.silver.quarantine import SILVER_QUARANTINE_SCHEMA
from readmission.silver.unplanned_visits import (
    SILVER_UNPLANNED_VISITS_SCHEMA,
    silver_unplanned_visits,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cms_current"
INGESTED_AT = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)


def bronze(spark, rows, batch_id="20261021T143411Z"):
    return cms.bronze_cms_dataset(
        spark,
        rows,
        source="https://data.cms.gov/x.csv",
        batch_id=batch_id,
        sha256="h",
        source_modified="2026-01-26",
        ingested_at=INGESTED_AT,
    )


def fixture_rows(name):
    return read_csv_rows(FIXTURES / name)


def assert_schema(df, schema):
    assert [(f.name, f.dataType) for f in df.schema] == [(f.name, f.dataType) for f in schema]


def quarantined(df):
    assert [f.name for f in df.schema] == SILVER_QUARANTINE_SCHEMA.fieldNames()
    return {r.business_key: r for r in df.collect()}


def test_hrrp_types_suppression_and_measures(spark):
    rows, rejected = silver_hrrp(bronze(spark, fixture_rows("hrrp.csv")))

    assert_schema(rows, SILVER_HRRP_SCHEMA)
    by_measure = {r.measure: r for r in rows.collect()}
    assert set(by_measure) == {"AMI", "HF", "PN", "COPD", "HIP_KNEE", "CABG"}
    hip_knee = by_measure["HIP_KNEE"]
    # N/A discharges and Too Few to Report readmissions are suppressed, not malformed.
    assert (hip_knee.discharges, hip_knee.readmissions) == (None, None)
    assert hip_knee.excess_readmission_ratio == pytest.approx(0.9875)
    hf = by_measure["HF"]
    assert (hf.facility_id, hf.discharges, hf.readmissions) == ("010001", 653, 136)
    assert (hf.predicted_rate, hf.expected_rate) == (pytest.approx(20.5695), pytest.approx(20.101))
    assert (hf.period_start, hf.period_end) == (date(2021, 7, 1), date(2024, 6, 30))
    assert hf.footnote is None
    assert rejected.count() == 0


def test_hrrp_malformed_rows_go_to_quarantine(spark):
    good = fixture_rows("hrrp.csv")[-1]
    malformed = [
        {**good, "Facility ID": "01-0001"},
        {**good, "Facility ID": "010002", "Excess Readmission Ratio": "1,02x"},
        {**good, "Facility ID": "010003", "Excess Readmission Ratio": "0"},
        {**good, "Facility ID": "010004", "Measure Name": "READM-30-SEPSIS-HRRP"},
        {**good, "Facility ID": "010006", "Start Date": "07/01/2025"},
        {**good, "Facility ID": "010007", "End Date": "2024-06-30"},
    ]

    rows, rejected = silver_hrrp(bronze(spark, [good, *malformed]))

    assert rows.count() == 1
    q = quarantined(rejected)
    assert {k: r.rule for k, r in q.items()} == {
        "01-0001|READM-30-HF-HRRP": "ccn_format",
        "010002|READM-30-HF-HRRP": "unparseable_excess_readmission_ratio",
        "010003|READM-30-HF-HRRP": "err_positive",
        "010004|READM-30-SEPSIS-HRRP": "unknown_measure",
        "010006|READM-30-HF-HRRP": "period_order",
        "010007|READM-30-HF-HRRP": "unparseable_period_end",
    }
    row = q["01-0001|READM-30-HF-HRRP"]
    assert (row.table_name, row._batch_id) == ("silver_hrrp", "20261021T143411Z")
    assert json.loads(row.raw_row)["facility_id"] == "01-0001"
    assert json.loads(row.raw_row)["excess_readmission_ratio"] == "1.0233"


def test_hrrp_ccn_padding_and_latest_batch_wins(spark):
    row = {**fixture_rows("hrrp.csv")[-1], "Facility ID": "10001"}
    old = bronze(spark, [{**row, "Excess Readmission Ratio": "1.1"}], "20260101T000000Z")
    new = bronze(spark, [row], "20261021T143411Z")

    rows, _ = silver_hrrp(old.unionByName(new))

    (only,) = rows.collect()
    assert (only.facility_id, only._batch_id) == ("010001", "20261021T143411Z")
    assert only.excess_readmission_ratio == pytest.approx(1.0233)


def test_hospital_info_types_and_flags(spark):
    rows, rejected = silver_hospital_info(bronze(spark, fixture_rows("hospital_info.csv")))

    assert_schema(rows, SILVER_HOSPITAL_INFO_SCHEMA)
    by_id = {r.facility_id: r for r in rows.collect()}
    assert len(by_id) == 3
    first = by_id["010001"]
    assert (first.city, first.county, first.zip_code) == ("DOTHAN", "HOUSTON", "36301")
    assert (first.emergency_services, first.birthing_friendly) == (True, True)
    assert first.overall_rating == 4
    assert (first.readm_measure_count, first.readm_measures_better) == (11, 1)
    assert (first.readm_measures_no_different, first.readm_measures_worse) == (9, 1)
    assert first.overall_rating_footnote is None
    assert rejected.count() == 0


def test_hospital_info_not_available_rating_is_null(spark):
    row = {**fixture_rows("hospital_info.csv")[0], "Hospital overall rating": "Not Available"}
    row["Meets criteria for birthing friendly designation"] = ""

    rows, _ = silver_hospital_info(bronze(spark, [row]))

    only = rows.first()
    assert (only.overall_rating, only.birthing_friendly) == (None, False)


def test_hospital_info_malformed_rows_go_to_quarantine(spark):
    good = fixture_rows("hospital_info.csv")[0]
    malformed = [
        {**good, "Facility ID": "", "Facility Name": "NO CCN"},
        {**good, "Facility ID": "010002", "Hospital overall rating": "four"},
        {**good, "Facility ID": "010003", "Hospital overall rating": "6"},
        {**good, "Facility ID": "010004", "Emergency Services": "Maybe"},
    ]

    rows, rejected = silver_hospital_info(bronze(spark, [good, *malformed]))

    assert rows.count() == 1
    assert {k: r.rule for k, r in quarantined(rejected).items()} == {
        "": "missing_facility_id",
        "010002": "unparseable_overall_rating",
        "010003": "rating_out_of_range",
        "010004": "unknown_emergency_services",
    }


def test_unplanned_visits_types_and_measures(spark):
    rows, rejected = silver_unplanned_visits(bronze(spark, fixture_rows("unplanned_visits.csv")))

    assert_schema(rows, SILVER_UNPLANNED_VISITS_SCHEMA)
    by_key = {(r.facility_id, r.measure_id): r for r in rows.collect()}
    assert len(by_key) == 10
    hf = by_key[("010001", "READM_30_HF")]
    assert (hf.measure, hf.denominator, hf.score) == ("HF", 1107, pytest.approx(19.9))
    # Not Applicable patient counts and Not Available estimates are suppressed, not malformed.
    assert (hf.number_of_patients, hf.lower_estimate, hf.compared_to_national) == (None,) * 3
    assert (hf.period_start, hf.period_end) == (date(2023, 7, 1), date(2025, 6, 30))
    # Excess days in acute care are negative when a hospital beats its expected days.
    edac = by_key[("010001", "EDAC_30_AMI")]
    assert (edac.measure, edac.score, edac.number_of_patients) == (None, pytest.approx(-2.6), 235)
    op = by_key[("010005", "OP_32")]
    assert op.compared_to_national == "No Different Than the National Rate"
    assert (op.lower_estimate, op.higher_estimate) == (pytest.approx(9.9), pytest.approx(17.1))
    assert by_key[("010005", "READM_30_CABG")].footnote == "5"
    assert rejected.count() == 0


def test_unplanned_visits_malformed_rows_go_to_quarantine(spark):
    good = fixture_rows("unplanned_visits.csv")[4]
    malformed = [
        {**good, "Facility ID": "010002", "Score": "--"},
        {**good, "Facility ID": "010003", "Measure ID": " "},
        {**good, "Facility ID": "010004", "Start Date": "07/01/2026"},
    ]

    rows, rejected = silver_unplanned_visits(bronze(spark, [good, *malformed]))

    assert rows.count() == 1
    assert {k: r.rule for k, r in quarantined(rejected).items()} == {
        "010002|READM_30_HF": "unparseable_score",
        "010003": "missing_measure_id",
        "010004|READM_30_HF": "period_order",
    }


def notes_bronze(spark, rows, batch_id="20261021T200000Z"):
    schema = (
        "id int, description string, medical_specialty string, sample_name string, "
        "transcription string, keywords string"
    )
    return with_ingest_metadata(
        spark.createDataFrame(rows, schema),
        ingested_at=INGESTED_AT,
        source="/v/mtsamples_clean",
        batch_id=batch_id,
        source_modified="0",
    )


def test_clinical_notes_trim_and_quarantine(spark):
    notes = [
        (
            1,
            " Consult for chest pain. ",
            " Cardiovascular / Pulmonary",
            "Chest Pain",
            "HPI ...",
            "",
        ),
        (2, "Knee replacement.", " Orthopedic", "TKA", "PROCEDURE ...", "knee, tka"),
        (3, "Empty note.", " Surgery", "Blank", "   ", None),
        (None, "No id.", " Surgery", "Orphan", "TEXT ...", None),
    ]

    rows, rejected = silver_clinical_notes(notes_bronze(spark, notes))

    assert_schema(rows, SILVER_CLINICAL_NOTES_SCHEMA)
    by_id = {r.note_id: r for r in rows.collect()}
    assert set(by_id) == {1, 2}
    first = by_id[1]
    assert (first.medical_specialty, first.description) == (
        "Cardiovascular / Pulmonary",
        "Consult for chest pain.",
    )
    assert first.keywords is None
    q = quarantined(rejected)
    assert {k: r.rule for k, r in q.items()} == {
        "3": "missing_transcription",
        "": "missing_note_id",
    }
    assert json.loads(q["3"].raw_row)["sample_name"] == "Blank"


def test_clinical_notes_latest_batch_wins(spark):
    note = (1, "Old.", " Surgery", "S", "OLD TEXT", None)
    newer = (1, "New.", " Surgery", "S", "NEW TEXT", None)
    bronze_rows = notes_bronze(spark, [note], "20261001T000000Z").unionByName(
        notes_bronze(spark, [newer], "20261021T200000Z")
    )

    rows, _ = silver_clinical_notes(bronze_rows)

    assert [r.transcription for r in rows.collect()] == ["NEW TEXT"]
