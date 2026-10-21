from datetime import date, datetime

import pytest
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.gold import build as gold_build
from readmission.gold.contoso_scorecard import gold_contoso_scorecard
from readmission.gold.facility_condition import gold_facility_condition_year
from readmission.gold.features import FEATURE_COLUMNS, KEY, LABEL_COLUMNS, gold_features
from readmission.gold.penalty_mart import gold_penalty_mart
from readmission.gold.windows import PRIOR_WINDOW_YEARS, prior_window_year, window_months
from readmission.ops import RunRecorder
from readmission.silver.contoso import (
    SILVER_CONTOSO_DISCHARGES_SCHEMA,
    SILVER_CONTOSO_FACILITIES_SCHEMA,
    SILVER_CONTOSO_FOLLOWUP_CALLS_SCHEMA,
)
from readmission.silver.hrrp_history import HRRP_PERIODS, SILVER_HRRP_HISTORY_SCHEMA

HOSPITAL_INFO_SCHEMA = (
    "facility_id string, facility_name string, state string, county string, "
    "hospital_type string, hospital_ownership string, emergency_services boolean"
)
UPDATED_AT = datetime(2026, 10, 21, 14, 0)

# Six HF hospitals in FY2013 and their non-overlapping window FY2016. 010006's ERR jumps by 0.40
# against changes of at most 0.02 elsewhere; 010002's discharges triple; 010003 falls under 25.
ERR_2016 = {"010001": 1.00, "010002": 1.01, "010003": 0.99, "010004": 1.02, "010005": 0.98}
ERR_2016["010006"] = 1.40
DISCHARGES_2016 = {"010002": 300, "010003": 10}


def history_row(facility_id, measure, fiscal_year, err, discharges=100, peer_median=None):
    start, end = HRRP_PERIODS[fiscal_year]
    source = "pdc" if 2019 <= fiscal_year <= 2026 else "ipps"
    return (
        facility_id,
        measure,
        fiscal_year,
        discharges,
        None,
        err,
        None,
        None,
        None,
        peer_median,
        start,
        end,
        source,
    )


@pytest.fixture(scope="module")
def history(spark):
    rows = [history_row(ccn, "HF", 2013, 1.0) for ccn in ERR_2016]
    rows += [
        history_row(ccn, "HF", 2016, err, DISCHARGES_2016.get(ccn, 100))
        for ccn, err in ERR_2016.items()
    ]
    rows += [
        history_row("010001", "PN", 2013, 1.10),
        history_row("010001", "PN", 2016, 1.05),
        history_row("010001", "HF", 2020, 1.05),
        history_row("010001", "HF", 2025, 1.00),
        history_row("010001", "HF", 2027, 0.99, peer_median=0.98),
        history_row("010002", "HF", 2027, None),
    ]
    return spark.createDataFrame(rows, SILVER_HRRP_HISTORY_SCHEMA)


@pytest.fixture(scope="module")
def hospital_info(spark):
    rows = [
        ("010001", "Alpha", "AL", "Jefferson", "Acute Care Hospitals", "Proprietary", True),
        ("010002", "Beta", "AL", "Mobile", "Acute Care Hospitals", "Voluntary", False),
    ]
    return spark.createDataFrame(rows, HOSPITAL_INFO_SCHEMA)


@pytest.fixture(scope="module")
def facts(history, hospital_info):
    return gold_facility_condition_year(history, hospital_info).cache()


def by_key(df, *key):
    return {tuple(r[k] for k in key): r for r in df.collect()}


def test_prior_window_year():
    assert {y: prior_window_year(y) for y in [2013, 2015, 2016, 2019, 2022, 2024, 2025]} == {
        2013: None,
        2015: None,
        2016: 2013,
        2019: 2016,
        2022: 2019,
        2024: 2021,
        2025: 2022,
    }
    # FY2027's two-year window starts the day after FY2025's ends.
    assert PRIOR_WINDOW_YEARS[2026] == 2023
    assert PRIOR_WINDOW_YEARS[2027] == 2025
    for year, prior in PRIOR_WINDOW_YEARS.items():
        assert HRRP_PERIODS[prior][1] < HRRP_PERIODS[year][0]
    assert [window_months(y) for y in [2019, 2022, 2024, 2027]] == [36, 30, 30, 24]


def test_facility_condition_year(history, facts):
    assert facts.count() == history.count()
    rows = by_key(facts, "facility_id", "measure", "fiscal_year")

    exact = rows[("010001", "HF", 2013)]
    assert (exact.penalty_cutoff, exact.cutoff_is_approximate, exact.above_cutoff) == (
        1.0,
        False,
        False,
    )
    assert (exact.facility_name, exact.state, exact.window_months) == ("Alpha", "AL", 36)
    approximate = rows[("010001", "HF", 2020)]
    assert (approximate.penalty_cutoff, approximate.cutoff_is_approximate) == (1.0, True)
    assert approximate.above_cutoff is True
    peer = rows[("010001", "HF", 2027)]
    assert (peer.penalty_cutoff, peer.cutoff_is_approximate, peer.above_cutoff) == (
        0.98,
        False,
        True,
    )
    assert (peer.prior_window_fiscal_year, peer.err_change) == (2025, pytest.approx(-0.01))
    no_ratio = rows[("010002", "HF", 2027)]
    assert (no_ratio.cutoff_is_approximate, no_ratio.above_cutoff) == (True, None)

    outlier = rows[("010006", "HF", 2016)]
    assert outlier.prior_window_fiscal_year == 2013
    assert outlier.err_change == pytest.approx(0.40)
    assert outlier.anomaly_flag is True
    assert outlier.anomaly_reason.startswith("err_change +0.400 (robust z 17.")
    jump = rows[("010002", "HF", 2016)]
    assert (jump.discharge_ratio, jump.anomaly_flag) == (pytest.approx(3.0), True)
    assert jump.anomaly_reason == "discharge_jump x3.00"
    small = rows[("010003", "HF", 2016)]
    assert (small.discharge_ratio, small.anomaly_flag, small.anomaly_reason) == (None, False, None)
    assert rows[("010004", "HF", 2016)].anomaly_flag is False
    assert rows[("010006", "HF", 2016)].facility_name is None


def test_penalty_mart(spark, facts):
    contoso = spark.createDataFrame(
        [("CH01", "Contoso Health Alder", "010001", "AL", UPDATED_AT)],
        SILVER_CONTOSO_FACILITIES_SCHEMA,
    )
    mart = gold_penalty_mart(facts, contoso)
    rows = by_key(mart, "facility_id", "fiscal_year")

    alpha_2016 = rows[("010001", 2016)]
    assert (alpha_2016.conditions_reported, alpha_2016.conditions_above_cutoff) == (2, 1)
    assert (alpha_2016.measures_above_cutoff, alpha_2016.penalized) == ("PN", True)
    assert (alpha_2016.worst_measure, alpha_2016.worst_err) == ("PN", 1.05)
    assert alpha_2016.prior_conditions_above_cutoff == 1
    # HF 1.00 - 1.00 and PN 1.05 - 1.10: a mean change of -0.025.
    assert (alpha_2016.mean_err_change, alpha_2016.trend) == (pytest.approx(-0.025), "better")
    assert (alpha_2016.is_contoso, alpha_2016.contoso_facility_id) == (True, "CH01")
    outlier = rows[("010006", 2016)]
    assert (outlier.trend, outlier.anomalies, outlier.is_contoso) == ("worse", 1, False)
    assert rows[("010001", 2013)].trend is None
    assert rows[("010005", 2016)].measures_above_cutoff is None


def test_contoso_scorecard(spark, facts):
    facilities = spark.createDataFrame(
        [("CH01", "Contoso Health Alder", "010001", "AL", UPDATED_AT)],
        SILVER_CONTOSO_FACILITIES_SCHEMA,
    )
    discharges = spark.createDataFrame(
        [
            ("CH01", date(2023, 9, 1), "HF", 40, UPDATED_AT),
            ("CH01", date(2025, 6, 1), "HF", 60, UPDATED_AT),
            # After FY2027's window, so in no fiscal year here.
            ("CH01", date(2025, 7, 1), "HF", 50, UPDATED_AT),
            ("CH01", date(2023, 9, 1), "PN", 30, UPDATED_AT),
        ],
        SILVER_CONTOSO_DISCHARGES_SCHEMA,
    )
    calls = spark.createDataFrame(
        [
            ("CH01", date(2023, 9, 1), "HF", True, 30, 20, UPDATED_AT),
            ("CH01", date(2023, 9, 1), "HF", False, 10, 5, UPDATED_AT),
            ("CH01", date(2025, 6, 1), "HF", True, 50, 45, UPDATED_AT),
        ],
        SILVER_CONTOSO_FOLLOWUP_CALLS_SCHEMA,
    )
    scorecard = gold_contoso_scorecard(facts, facilities, discharges, calls)
    rows = by_key(scorecard, "contoso_facility_id", "measure", "fiscal_year")

    # Contoso facility 010001 has CMS rows for HF in 2013, 2016, 2020, 2025, 2027 and PN in
    # 2013 and 2016.
    assert len(rows) == 7
    hf = rows[("CH01", "HF", 2027)]
    assert (hf.facility_id, hf.cms_discharges, hf.above_cutoff) == ("010001", 100, True)
    assert (hf.contoso_months, hf.contoso_discharges) == (2, 100)
    assert (hf.calls_attempted, hf.calls_completed, hf.calls_completed_within_72h) == (90, 70, 65)
    assert hf.followup_rate == pytest.approx(0.70)
    assert hf.followup_within_72h_rate == pytest.approx(0.65)
    old = rows[("CH01", "HF", 2013)]
    assert (old.contoso_months, old.contoso_discharges, old.followup_rate) == (0, None, None)


def test_features_values(facts, hospital_info):
    features = gold_features(facts, hospital_info)
    rows = by_key(features, *KEY)

    alpha = rows[("010001", "HF", 2027)]
    assert (alpha.label_above_cutoff, alpha.label_err) == (True, 0.99)
    assert (alpha.err_lag1, alpha.err_lag2, alpha.err_lag3) == (None, 1.00, None)
    assert (alpha.years_observed, alpha.years_above_cutoff) == (4, 1)
    assert alpha.err_max_prior == 1.05
    assert (alpha.state, alpha.emergency_services) == ("AL", True)
    first = rows[("010001", "HF", 2013)]
    assert (first.years_observed, first.err_mean_prior, first.label_err) == (0, None, 1.0)
    # The next cycle gets a scoring row with no label.
    scoring = rows[("010001", "HF", 2028)]
    assert (scoring.err_lag1, scoring.label_above_cutoff) == (0.99, None)
    assert scoring.err_change_lag1 == pytest.approx(-0.01)
    assert len(rows) == facts.count() + 2


@pytest.mark.parametrize("target", [2016, 2027])
def test_features_never_use_the_target_year_or_later(facts, hospital_info, target):
    """The leakage rule: changing every fact row of the target fiscal year or later must not
    change any feature for that target. Only the label columns may move."""
    numeric = [
        "discharges",
        "excess_readmission_ratio",
        "predicted_rate",
        "expected_rate",
        "err_change",
    ]
    later = F.col("fiscal_year") >= target
    perturbed = facts
    for column in numeric:
        perturbed = perturbed.withColumn(
            column,
            F.when(later, F.coalesce(F.col(column), F.lit(1)) * 7 + 3).otherwise(F.col(column)),
        )
    for column in ["above_cutoff", "anomaly_flag", "cutoff_is_approximate"]:
        perturbed = perturbed.withColumn(
            column, F.when(later, ~F.coalesce(column, F.lit(False))).otherwise(F.col(column))
        )
    perturbed = perturbed.withColumn(
        "readmissions", F.when(later, F.lit(999)).otherwise(F.col("readmissions"))
    )

    def at_target(df):
        selected = gold_features(df, hospital_info).where(F.col("target_fiscal_year") == target)
        return by_key(selected, *KEY)

    original, changed = at_target(facts), at_target(perturbed)
    assert original.keys() == changed.keys()
    for key in original:
        for column in FEATURE_COLUMNS:
            assert original[key][column] == changed[key][column], (key, column)
    # The perturbation reaches the labels, so the comparison above can see a change.
    assert any(original[k].label_err != changed[k].label_err for k in original)
    assert set(FEATURE_COLUMNS).isdisjoint(LABEL_COLUMNS)


@pytest.fixture
def gold_db(spark):
    spark.sql("CREATE DATABASE IF NOT EXISTS gold_test")
    yield "spark_catalog.gold_test"
    spark.sql("DROP DATABASE IF EXISTS gold_test CASCADE")


def test_build_gold_writes_every_table(spark, gold_db, history, monkeypatch):
    # Unity Catalog tags and grants do not exist on local Spark.
    monkeypatch.setattr(gold_build, "apply_governance", lambda *a: None)
    history.write.format("delta").saveAsTable(f"{gold_db}.silver_hrrp_history")
    spark.createDataFrame(
        [("010001", "Alpha", "AL", "Jefferson", "Acute Care Hospitals", "Proprietary", True)],
        HOSPITAL_INFO_SCHEMA,
    ).write.format("delta").saveAsTable(f"{gold_db}.silver_hospital_info")
    for table, schema in [
        ("facilities", SILVER_CONTOSO_FACILITIES_SCHEMA),
        ("discharges", SILVER_CONTOSO_DISCHARGES_SCHEMA),
        ("followup_calls", SILVER_CONTOSO_FOLLOWUP_CALLS_SCHEMA),
    ]:
        spark.createDataFrame([], schema).write.format("delta").saveAsTable(
            f"{gold_db}.silver_contoso_{table}"
        )
    run = RunRecorder("gold")

    gold_build.build_gold(spark, gold_db, run)

    facts = f"{gold_db}.gold_facility_condition_year"
    assert run.rows[facts] == 18
    assert run.rows[f"{gold_db}.gold_contoso_scorecard"] == 0
    assert set(run.rows) == {
        facts,
        f"{gold_db}.gold_penalty_mart",
        f"{gold_db}.gold_contoso_scorecard",
        f"{gold_db}.gold_features",
    }
    check = next(m for m in run.metrics if m.metric == "row_count_vs_silver")
    assert (check.value, check.threshold, check.passed) == (18, 18, True)
    comments = {r["col_name"]: r["comment"] for r in spark.sql(f"DESCRIBE {facts}").collect()}
    assert comments["penalty_cutoff"].startswith("ERR above which")
    assert spark.catalog.tableExists(f"{gold_db}.ops_run_log")
    assert isinstance(spark.table(facts).schema["anomaly_flag"].dataType, T.BooleanType)
