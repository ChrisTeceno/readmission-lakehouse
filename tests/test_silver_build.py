from datetime import UTC, datetime
from pathlib import Path

import pytest
from pyspark.sql import types as T

from readmission.bronze import cms
from readmission.bronze.hrrp_history import bronze_hrrp_history_ipps, bronze_hrrp_history_pdc
from readmission.ingest.cms_current import read_csv_rows
from readmission.ingest.hrrp_history import read_ipps_rows, read_pdc_rows
from readmission.ops import RunRecorder
from readmission.quality.expectations import (
    MAX_ERR_NULL_RATE,
    ExpectationFailed,
    check_err_null_rate,
    check_row_count,
)
from readmission.silver import hrrp
from readmission.silver.build import SilverTable, build_table, ensure_table
from readmission.silver.governance import (
    TableDoc,
    comment_statements,
    field,
    grant_statement,
    tag_statement,
)
from readmission.silver.hrrp_history import silver_hrrp_history_checked
from readmission.silver.normalize import canonical_measure, pad_ccn, suppressed_to_null
from readmission.silver.quarantine import QUARANTINE_TABLE, SILVER_QUARANTINE_SCHEMA

FIXTURES = Path(__file__).parent / "fixtures"
INGESTED_AT = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
SILVER_HRRP = SilverTable(
    hrrp.TABLE,
    hrrp.SILVER_HRRP_SCHEMA,
    hrrp.SILVER_HRRP_DOC,
    hrrp.CONSTRAINTS,
    ["bronze_hrrp"],
    hrrp.silver_hrrp,
)


def test_normalize_helpers(spark):
    df = spark.createDataFrame(
        [
            (" 10001 ", "READM_30_AMI_HRRP", "1,234"),
            ("0100011", "READM-30-HIP-KNEE-HRRP", "Not Applicable"),
            ("", "READM_30_COPD", " . "),
            ("A1234B", "EDAC_30_AMI", "Too Few to Report"),
        ],
        "ccn string, measure string, value string",
    )

    rows = df.select(
        pad_ccn(df.ccn).alias("ccn"),
        canonical_measure(df.measure).alias("measure"),
        suppressed_to_null("value").alias("value"),
    ).collect()

    assert [tuple(r) for r in rows] == [
        ("010001", "AMI", "1234"),
        ("0100011", "HIP_KNEE", None),
        (None, "COPD", None),
        ("A1234B", None, None),
    ]


def test_history_malformed_row_goes_to_quarantine(spark):
    pdc_rows = read_pdc_rows(FIXTURES / "hrrp_history" / "pdc_fy2022.csv")
    pdc_rows.append({**pdc_rows[0], "Facility ID": "01-0001"})
    pdc = bronze_hrrp_history_pdc(
        spark,
        pdc_rows,
        fiscal_year=2022,
        source="s",
        batch_id="20261021T190000Z",
        sha256="a",
        ingested_at=INGESTED_AT,
    )
    stale = bronze_hrrp_history_pdc(
        spark,
        pdc_rows[:1],
        fiscal_year=2022,
        source="s",
        batch_id="20261001T000000Z",
        sha256="b",
        ingested_at=INGESTED_AT,
    )
    ipps = bronze_hrrp_history_ipps(
        spark,
        read_ipps_rows(FIXTURES / "hrrp_history" / "ipps_fy2013.txt"),
        fiscal_year=2013,
        source="s",
        batch_id="20261021T190000Z",
        sha256="c",
        ingested_at=INGESTED_AT,
    )

    rows, rejected = silver_hrrp_history_checked(pdc.unionByName(stale), ipps)

    # The stale batch of FY2022 is ignored; the latest batch holds the year.
    assert rows.count() == 12 + 9
    (bad,) = rejected.collect()
    assert (bad.table_name, bad.rule, bad._batch_id) == (
        "silver_hrrp_history",
        "ccn_format",
        "20261021T190000Z",
    )
    assert bad.business_key.startswith("01-0001|")


def test_row_count_expectation():
    check_row_count("t", None, 10)
    check_row_count("t", 100, 80)
    with pytest.raises(ExpectationFailed, match="down from 100"):
        check_row_count("t", 100, 79)


def test_err_null_rate_expectation():
    # FY2026 HRRP: 6,610 of 18,330 ratios are null.
    check_err_null_rate("t", 6610, 18330)
    check_err_null_rate("t", 0, 0)
    with pytest.raises(ExpectationFailed, match="excess_readmission_ratio"):
        check_err_null_rate("t", int(18330 * MAX_ERR_NULL_RATE) + 1, 18330)


def test_governance_statements():
    schema = T.StructType([field("facility_id", T.StringType(), "The hospital's CCN")])
    doc = TableDoc("Hospitals", {"layer": "silver", "contains_phi": "false"})

    assert comment_statements("c.s.t", schema, doc) == [
        "COMMENT ON TABLE c.s.t IS 'Hospitals'",
        "ALTER TABLE c.s.t ALTER COLUMN `facility_id` COMMENT 'The hospital\\'s CCN'",
    ]
    assert tag_statement("c.s.t", doc) == (
        "ALTER TABLE c.s.t SET TAGS ('layer' = 'silver', 'contains_phi' = 'false')"
    )
    assert grant_statement("c.s.t") == "GRANT SELECT ON TABLE c.s.t TO `analysts`"


def test_every_silver_column_has_a_comment():
    from readmission.silver.build import SILVER_TABLES

    for table in SILVER_TABLES:
        assert table.doc.comment
        assert {"layer", "contains_phi", "source"} <= set(table.doc.tags)
        assert all(f.metadata.get("comment") for f in table.schema), table.name
    assert all(f.metadata.get("comment") for f in SILVER_QUARANTINE_SCHEMA)


@pytest.fixture
def silver_db(spark):
    spark.sql("CREATE DATABASE IF NOT EXISTS silver_test")
    yield "spark_catalog.silver_test"
    spark.sql("DROP DATABASE IF EXISTS silver_test CASCADE")


def load_bronze_hrrp(spark, prefix, rows):
    df = cms.bronze_cms_dataset(
        spark,
        rows,
        source="u",
        batch_id="20261021T143411Z",
        sha256="h",
        source_modified="m",
        ingested_at=INGESTED_AT,
    )
    df.write.format("delta").mode("overwrite").saveAsTable(f"{prefix}.bronze_hrrp")


def test_build_table_writes_constraints_and_quarantine(spark, silver_db):
    rows = read_csv_rows(FIXTURES / "cms_current" / "hrrp.csv")
    load_bronze_hrrp(spark, silver_db, [*rows, {**rows[0], "Facility ID": "BAD-01"}])
    ensure_table(spark, f"{silver_db}.{QUARANTINE_TABLE}", SILVER_QUARANTINE_SCHEMA, [])

    assert build_table(spark, SILVER_HRRP, silver_db) == (6, 1)
    # A second run replaces both the table and this table's quarantine rows.
    assert build_table(spark, SILVER_HRRP, silver_db) == (6, 1)

    table = f"{silver_db}.silver_hrrp"
    assert spark.table(table).count() == 6
    properties = {r["key"] for r in spark.sql(f"SHOW TBLPROPERTIES {table}").collect()}
    assert {f"delta.constraints.{c}" for c in hrrp.CONSTRAINTS} <= properties
    comments = {r["col_name"]: r["comment"] for r in spark.sql(f"DESCRIBE {table}").collect()}
    assert comments["facility_id"] == "CMS Certification Number (CCN)"
    with pytest.raises(Exception, match="err_positive"):
        spark.sql(
            f"INSERT INTO {table} (facility_id, measure, excess_readmission_ratio, _batch_id) "
            "VALUES ('010001', 'HF', -1.0, 'b')"
        )


def test_build_table_fails_on_a_row_count_drop(spark, silver_db):
    rows = read_csv_rows(FIXTURES / "cms_current" / "hrrp.csv")
    ensure_table(spark, f"{silver_db}.{QUARANTINE_TABLE}", SILVER_QUARANTINE_SCHEMA, [])
    load_bronze_hrrp(spark, silver_db, rows)
    build_table(spark, SILVER_HRRP, silver_db)
    load_bronze_hrrp(spark, silver_db, rows[:2])
    run = RunRecorder("silver")

    with pytest.raises(ExpectationFailed, match="2 rows, down from 6"):
        build_table(spark, SILVER_HRRP, silver_db, run)
    assert spark.table(f"{silver_db}.silver_hrrp").count() == 6
    assert [(m.metric, m.value, m.threshold, m.passed) for m in run.metrics] == [
        ("quarantined_rows", 0, None, True),
        ("row_count", 2, pytest.approx(4.8), False),
    ]
