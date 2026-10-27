from datetime import date, datetime
from pathlib import Path

import pytest

from readmission.bronze.hrrp_history import (
    bronze_from_manifest,
    bronze_hrrp_history_ipps,
    bronze_hrrp_history_pdc,
    snake_case,
)
from readmission.ingest.hrrp_history import read_ipps_rows, read_pdc_rows
from readmission.ingest.landing import LocalLander, land_manifest, list_manifests, sha256_hex
from readmission.silver.hrrp_history import (
    SILVER_HRRP_HISTORY_SCHEMA,
    silver_from_ipps,
    silver_from_pdc,
    silver_hrrp_history,
)
from readmission.silver.hrrp_payment import silver_hrrp_payment, silver_hrrp_payment_measure

FIXTURES = Path(__file__).parent / "fixtures" / "hrrp_history"
INGESTED_AT = datetime(2026, 10, 21, 8, 0, 0)


def pdc_bronze(spark, name, fiscal_year):
    rows = read_pdc_rows(FIXTURES / name)
    return bronze_hrrp_history_pdc(
        spark,
        rows,
        fiscal_year=fiscal_year,
        source=f"https://data.cms.gov/x.zip#{name}",
        batch_id="b1",
        sha256="abc",
        ingested_at=INGESTED_AT,
    )


def ipps_bronze(spark, name, fiscal_year):
    rows = read_ipps_rows(FIXTURES / name)
    return bronze_hrrp_history_ipps(
        spark,
        rows,
        fiscal_year=fiscal_year,
        source=f"https://www.cms.gov/x.zip#{name}",
        batch_id="b1",
        sha256="abc",
        ingested_at=INGESTED_AT,
    )


def by_key(df):
    return {(r.facility_id, r.measure): r for r in df.collect()}


def peer_tables(ipps):
    """(silver_hrrp_payment, silver_hrrp_payment_measure) rows from IPPS Bronze."""
    (payment, _), (measures, _) = silver_hrrp_payment(ipps), silver_hrrp_payment_measure(ipps)
    return payment, measures


def test_snake_case_headers():
    assert snake_case("Provider ID") == "provider_id"
    assert snake_case(" Number of Discharges ") == "number_of_discharges"


def test_bronze_keeps_strings_and_metadata(spark):
    pdc = pdc_bronze(spark, "pdc_fy2022.csv", 2022)
    ipps = ipps_bronze(spark, "ipps_fy2027.txt", 2027)

    assert pdc.columns[:3] == ["facility_name", "facility_id", "state"]
    assert pdc.columns[-5:] == ["fiscal_year", "_ingested_at", "_source", "_batch_id", "_sha256"]
    assert {f.dataType.simpleString() for f in pdc.schema if not f.name.startswith("_")} == {
        "string",
        "int",
    }
    first = ipps.where("record['Hospital CCN'] = '010001'").first()
    assert first.record["Number of eligible discharges for HF"] == "1,110"
    assert first._source == "https://www.cms.gov/x.zip#ipps_fy2027.txt"
    assert first._ingested_at == INGESTED_AT


def test_pdc_old_header_and_suppression(spark):
    rows = by_key(silver_from_pdc(pdc_bronze(spark, "pdc_fy2019_provider_id.csv", 2019)))

    ami = rows[("190164", "AMI")]
    assert ami.discharges is None
    assert ami.excess_readmission_ratio is None
    assert ami.readmissions is None
    hf = rows[("190164", "HF")]
    assert (hf.discharges, hf.readmissions) == (259, 67)
    assert hf.excess_readmission_ratio == pytest.approx(1.0773)
    assert (hf.predicted_rate, hf.expected_rate) == (pytest.approx(23.9788), pytest.approx(22.2578))
    assert (hf.period_start, hf.period_end) == (date(2014, 7, 1), date(2017, 6, 30))
    assert hf.source == "pdc"
    assert {m for _, m in rows} == {"AMI", "CABG", "COPD", "HF", "HIP_KNEE", "PN"}


def test_pdc_hyphen_measures_and_too_few_to_report(spark):
    rows = by_key(silver_from_pdc(pdc_bronze(spark, "pdc_fy2022.csv", 2022)))

    hip_knee = rows[("010005", "HIP_KNEE")]
    assert hip_knee.discharges is None
    assert hip_knee.readmissions is None
    assert hip_knee.excess_readmission_ratio == pytest.approx(0.9779)
    assert rows[("010001", "HIP_KNEE")].discharges == 165
    # FY2022's window stops early because of the COVID exclusion.
    assert rows[("010001", "HF")].period_end == date(2019, 12, 1)
    assert len(rows) == 12


def test_ccn_padding(spark):
    rows = [
        {
            "Facility Name": "X",
            "Facility ID": "10001",
            "Measure Name": "READM-30-HF-HRRP",
            "Number of Discharges": "1,234",
            "Excess Readmission Ratio": " 1.01 ",
            "Predicted Readmission Rate": "",
            "Expected Readmission Rate": "N/A",
            "Number of Readmissions": "Too Few to Report",
        }
    ]
    bronze = bronze_hrrp_history_pdc(
        spark,
        rows,
        fiscal_year=2026,
        source="s",
        batch_id="b1",
        sha256="abc",
        ingested_at=INGESTED_AT,
    )

    row = silver_from_pdc(bronze).first()

    assert row.facility_id == "010001"
    assert row.discharges == 1234
    assert row.excess_readmission_ratio == pytest.approx(1.01)
    assert (row.predicted_rate, row.expected_rate, row.readmissions) == (None, None, None)


def test_ipps_fy2013_unpivot_and_zero_err(spark):
    rows = by_key(silver_from_ipps(ipps_bronze(spark, "ipps_fy2013.txt", 2013)))

    assert len(rows) == 9
    assert {m for _, m in rows} == {"AMI", "HF", "PN"}
    pn = rows[("010001", "PN")]
    assert pn.discharges == 400
    assert pn.excess_readmission_ratio == pytest.approx(0.9137)
    assert (pn.readmissions, pn.predicted_rate, pn.expected_rate) == (None, None, None)
    assert (pn.period_start, pn.period_end) == (date(2008, 7, 1), date(2011, 6, 30))
    assert pn.source == "ipps"
    # 21 AMI cases is under the 25-case minimum, so the file's 0.0000 means no ratio.
    small = rows[("010005", "AMI")]
    assert (small.discharges, small.excess_readmission_ratio) == (21, None)


def test_ipps_fy2017_six_measures_and_header_typo(spark):
    rows = by_key(silver_from_ipps(ipps_bronze(spark, "ipps_fy2017.txt", 2017)))

    assert {m for _, m in rows} == {"AMI", "HF", "PN", "COPD", "HIP_KNEE", "CABG"}
    # The FY2017 header reads "Excess Readmission Ratio for Pneumoniuia".
    assert rows[("010001", "PN")].excess_readmission_ratio == pytest.approx(1.089249865)
    assert rows[("010001", "CABG")].discharges == 273
    assert rows[("010005", "CABG")].excess_readmission_ratio is None


def test_ipps_fy2027_thousands_separator(spark):
    rows = by_key(silver_from_ipps(ipps_bronze(spark, "ipps_fy2027.txt", 2027)))

    hf = rows[("010001", "HF")]
    assert hf.discharges == 1110
    assert hf.excess_readmission_ratio == pytest.approx(0.9362)
    # Peer terms come from the payment tables (with_peer_terms), not this unpivot.
    assert (hf.peer_group, hf.peer_median_err) == (None, None)
    cabg = rows[("010005", "CABG")]
    assert (cabg.discharges, cabg.excess_readmission_ratio) == (None, None)
    assert rows[("010005", "HIP_KNEE")].excess_readmission_ratio == pytest.approx(1.0291)
    assert len(rows) == 18


def test_history_fy2027_peer_terms_from_payment_tables(spark):
    ipps = ipps_bronze(spark, "ipps_fy2027.txt", 2027)
    pdc = pdc_bronze(spark, "pdc_fy2022.csv", 2022)

    rows = by_key(silver_hrrp_history(pdc, ipps, *peer_tables(ipps)).where("fiscal_year = 2027"))

    hf = rows[("010001", "HF")]
    assert hf.peer_group == 2
    assert hf.peer_median_err == pytest.approx(0.9940)
    # CMS publishes the median for a condition the hospital has no ERR for.
    cabg = rows[("010005", "CABG")]
    assert cabg.excess_readmission_ratio is None
    assert cabg.peer_median_err == pytest.approx(0.9837)


def test_silver_hrrp_history_schema_and_key(spark):
    pdc = pdc_bronze(spark, "pdc_fy2022.csv", 2022).unionByName(
        pdc_bronze(spark, "pdc_fy2019_provider_id.csv", 2019), allowMissingColumns=True
    )
    ipps = ipps_bronze(spark, "ipps_fy2013.txt", 2013).unionByName(
        ipps_bronze(spark, "ipps_fy2027.txt", 2027)
    )

    silver = silver_hrrp_history(pdc, ipps, *peer_tables(ipps))

    assert [(f.name, f.dataType) for f in silver.schema] == [
        (f.name, f.dataType) for f in SILVER_HRRP_HISTORY_SCHEMA
    ]
    assert silver.count() == 12 + 8 + 9 + 18
    assert (
        silver.groupBy("facility_id", "measure", "fiscal_year").count().where("count > 1").count()
        == 0
    )


def test_history_keeps_pdc_rows_and_takes_peer_terms_from_the_supplemental_file(spark):
    # A PDC year's supplemental file adds only the peer group and median: the rows, ERRs and
    # readmission counts stay PDC's. The FY2027 file stands in for FY2022's here.
    ipps = ipps_bronze(spark, "ipps_fy2027.txt", 2022).unionByName(
        ipps_bronze(spark, "ipps_fy2027.txt", 2027)
    )
    pdc = pdc_bronze(spark, "pdc_fy2022.csv", 2022)

    silver = silver_hrrp_history(pdc, ipps, *peer_tables(ipps))

    years = {r.fiscal_year for r in silver.where("source = 'ipps'").collect()}
    assert years == {2027}
    rows = by_key(silver.where("fiscal_year = 2022"))
    assert len(rows) == 12
    hf = rows[("010001", "HF")]
    assert (hf.source, hf.readmissions) == ("pdc", 211)
    assert hf.excess_readmission_ratio == pytest.approx(1.0399)
    assert (hf.peer_group, hf.peer_median_err) == (2, pytest.approx(0.9940))


def test_history_peer_terms_null_without_a_supplemental_row(spark):
    # A hospital the year's supplemental file does not list keeps null peer terms, and Gold
    # falls back to 1.0 for it (readmission.gold.facility_condition.with_penalty_cutoff).
    ipps = ipps_bronze(spark, "ipps_fy2027.txt", 2022).where("record['Hospital CCN'] = '010001'")
    pdc = pdc_bronze(spark, "pdc_fy2022.csv", 2022)

    rows = by_key(silver_hrrp_history(pdc, ipps, *peer_tables(ipps)))

    assert rows[("010001", "AMI")].peer_median_err == pytest.approx(0.9968)
    assert (rows[("010005", "AMI")].peer_group, rows[("010005", "AMI")].peer_median_err) == (
        None,
        None,
    )


def test_bronze_from_manifest_reads_landed_batch(spark, tmp_path):
    lander = LocalLander(str(tmp_path))
    data = (FIXTURES / "ipps_fy2017.txt").read_bytes()
    lander.land_file("hrrp_history_ipps", "b7", "ipps_fy2017.txt", data, "2017")
    entry = {
        "name": "ipps_fy2017.txt",
        "fiscal_year": 2017,
        "source_ref": "https://www.cms.gov/fy2017.zip#FY 2017 IPPS Final Rule Readmissions PUF (FY17 data).txt",
        "sha256": sha256_hex(data),
    }
    land_manifest(lander, "hrrp_history_ipps", "b7", [entry], "2017")
    (manifest,) = list_manifests(str(tmp_path), "hrrp_history_ipps")

    bronze = bronze_from_manifest(spark, manifest, INGESTED_AT)

    assert bronze.count() == 3
    row = bronze.first()
    assert (row.fiscal_year, row._batch_id, row._sha256) == (2017, "b7", sha256_hex(data))
    assert row._source == entry["source_ref"]
