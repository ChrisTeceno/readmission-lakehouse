import itertools
from datetime import datetime
from pathlib import Path

import pytest
from pyspark.sql import functions as F

from readmission.bronze.hrrp_history import bronze_hrrp_history_ipps
from readmission.gold.nurse_call_economics import (
    ANNUAL_DISCHARGES,
    annual_program_cost,
    gold_nurse_call_economics,
)
from readmission.gold.penalty_dollars import (
    computed_paf,
    gold_penalty_by_condition,
    gold_penalty_dollars,
    gold_penalty_sensitivity,
)
from readmission.ingest.contoso import FACILITIES
from readmission.ingest.hrrp_history import read_ipps_rows
from readmission.ingest.ipps_impact import read_impact_rows
from readmission.silver.contoso import SILVER_CONTOSO_FACILITIES_SCHEMA
from readmission.silver.hrrp_payment import (
    SILVER_HRRP_PAYMENT_MEASURE_SCHEMA,
    SILVER_HRRP_PAYMENT_SCHEMA,
    silver_hrrp_payment,
    silver_hrrp_payment_measure,
    silver_ipps_impact,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hrrp_payment"
INGESTED_AT = datetime(2026, 10, 26, 12, 0)

# The 12 Contoso CCNs' payment adjustment factors as CMS published them (FY2026 and FY2027 HRRP
# supplemental files, docs/penalty-dollars.md).
PUBLISHED_PAF = {
    2026: {
        "360085": 0.9990,
        "360155": 0.9864,
        "360068": 0.9998,
        "360051": 0.9941,
        "360027": 0.9996,
        "360039": 0.9998,
        "360072": 0.9879,
        "360040": 0.9978,
        "360054": 1.0000,
        "360218": 0.9939,
        "360125": 0.9885,
        "360029": 0.9862,
    },
    2027: {
        "360085": 0.9963,
        "360155": 0.9909,
        "360068": 0.9991,
        "360051": 0.9994,
        "360027": 0.9967,
        "360039": 1.0000,
        "360072": 0.9839,
        "360040": 0.9992,
        "360054": 1.0000,
        "360218": 1.0000,
        "360125": 0.9794,
        "360029": 0.9999,
    },
}


def bronze(spark, rows, fiscal_year):
    return bronze_hrrp_history_ipps(
        spark,
        rows,
        fiscal_year=fiscal_year,
        source="https://www.cms.gov/x.zip#x.txt",
        batch_id="b1",
        sha256=f"sha{fiscal_year}",
        ingested_at=INGESTED_AT,
    )


def landed(spark, read, prefix):
    frames = [
        bronze(spark, read(FIXTURES / f"{prefix}_fy{year}.txt"), year) for year in (2026, 2027)
    ]
    return frames[0].unionByName(frames[1])


@pytest.fixture(scope="module")
def supplemental(spark):
    return landed(spark, read_ipps_rows, "supplemental")


@pytest.fixture(scope="module")
def payment(supplemental):
    rows, rejected = silver_hrrp_payment(supplemental)
    assert rejected.count() == 0
    return rows.cache()


@pytest.fixture(scope="module")
def measures(supplemental):
    rows, rejected = silver_hrrp_payment_measure(supplemental)
    assert rejected.count() == 0
    return rows.cache()


@pytest.fixture(scope="module")
def impact(spark):
    rows, rejected = silver_ipps_impact(landed(spark, read_impact_rows, "impact"))
    assert rejected.count() == 0
    return rows.cache()


@pytest.fixture(scope="module")
def contoso(spark):
    rows = [(f.facility_id, f.facility_name, f.ccn, f.state, INGESTED_AT) for f in FACILITIES]
    return spark.createDataFrame(rows, SILVER_CONTOSO_FACILITIES_SCHEMA)


@pytest.fixture(scope="module")
def dollars(payment, measures, impact, contoso):
    return gold_penalty_dollars(payment, measures, impact, contoso).cache()


@pytest.fixture(scope="module")
def sensitivity(dollars, measures, payment):
    return gold_penalty_sensitivity(dollars, measures, payment).cache()


def by_facility(df, *key):
    return {tuple(r[k] for k in key) if len(key) > 1 else r[key[0]]: r for r in df.collect()}


def test_silver_payment_parses_factor_percent_and_peer_group(payment):
    rows = by_facility(payment, "facility_id", "fiscal_year")
    assert len(rows) == 24
    ch07 = rows[("360072", 2027)]
    assert ch07.payment_adjustment_factor == 0.9839
    assert ch07.payment_reduction_pct == pytest.approx(1.61)
    assert ch07.neutrality_modifier == 0.9628
    assert isinstance(ch07.peer_group, int)
    assert rows[("360072", 2026)].neutrality_modifier == 0.9627


def test_silver_payment_measure_unpivots_six_conditions(measures):
    rows = by_facility(measures, "facility_id", "measure", "fiscal_year")
    assert {m for _, m, _ in rows} == {"AMI", "HF", "PN", "COPD", "HIP_KNEE", "CABG"}
    hf = rows[("360072", "HF", 2027)]
    assert (hf.excess_readmission_ratio, hf.peer_median_err) == (1.1012, 0.9950)
    assert (hf.discharges, hf.drg_payment_ratio) == (587, 0.0428)
    assert hf.penalty_indicator is True
    assert rows[("360072", "PN", 2027)].penalty_indicator is False


def test_silver_ipps_impact_reads_the_years_own_grouper_columns(impact):
    rows = by_facility(impact, "facility_id", "fiscal_year")
    # FY2027 correction notice: CASETA44 and TACMIV44, not the V43 columns beside them.
    ch07 = rows[("360072", 2027)]
    assert (ch07.transfer_adjusted_cases, ch07.transfer_adjusted_cmi) == (2302.94, 1.7776)
    assert (ch07.wage_index, ch07.provider_type) == (0.9236, "0")
    assert rows[("360072", 2026)].transfer_adjusted_cases == 2258.41
    assert "name" not in [c.lower() for c in impact.columns]


def test_computed_paf_reproduces_published_fy2027_factor_to_4_decimals(measures, payment):
    computed = by_facility(
        computed_paf(measures, payment).where(F.col("fiscal_year") == 2027), "facility_id"
    )
    assert len(computed) == 12
    for ccn, published in PUBLISHED_PAF[2027].items():
        assert round(computed[ccn].computed_paf, 4) == published, ccn


def test_computed_paf_is_within_rounding_of_published_fy2026_factor(measures, payment):
    # CMS rounds its inputs to four decimals before publishing, so a factor can land on the
    # other side of a rounding edge: CH07 computes to 0.98796, published 0.9879.
    computed = by_facility(
        computed_paf(measures, payment).where(F.col("fiscal_year") == 2026), "facility_id"
    )
    for ccn, published in PUBLISHED_PAF[2026].items():
        assert computed[ccn].computed_paf == pytest.approx(published, abs=1e-4), ccn


def test_ch07_fy2027_paf_by_hand(measures, payment):
    # HF, CABG and THA/TKA are above their peer medians: (1.1012 - 0.9950) x 0.0428 +
    # (1.3017 - 0.9895) x 0.0296 + (1.2828 - 0.9936) x 0.0101, times the 0.9628 modifier.
    ratio = (1.1012 - 0.9950) * 0.0428 + (1.3017 - 0.9895) * 0.0296 + (1.2828 - 0.9936) * 0.0101
    rows = by_facility(computed_paf(measures, payment), "facility_id", "fiscal_year")
    assert rows[("360072", 2027)].computed_paf == pytest.approx(1 - 0.9628 * ratio)


def measure_rows(spark, rows, modifier=1.0):
    measures = spark.createDataFrame(
        [("010001", m, 2027, d, err, median, None, ratio) for m, d, err, median, ratio in rows],
        SILVER_HRRP_PAYMENT_MEASURE_SCHEMA,
    )
    payment = spark.createDataFrame(
        [("010001", 2027, None, None, None, 1, modifier)], SILVER_HRRP_PAYMENT_SCHEMA
    )
    return measures, payment


def paf_of(spark, rows):
    return computed_paf(*measure_rows(spark, rows)).first().computed_paf


def test_paf_floor_is_097(spark):
    assert paf_of(spark, [("HF", 500, 2.0, 1.0, 0.10)]) == 0.97


def test_conditions_under_25_discharges_do_not_count(spark):
    assert paf_of(spark, [("HF", 24, 1.5, 1.0, 0.05)]) == 1.0
    assert paf_of(spark, [("HF", 25, 1.5, 1.0, 0.05)]) == pytest.approx(0.975)
    assert paf_of(spark, [("HF", None, None, 1.0, None)]) == 1.0


def test_paf_is_one_at_or_below_the_median_and_falls_as_err_rises(spark):
    assert paf_of(spark, [("HF", 100, 0.99, 1.0, 0.05), ("PN", 100, 1.0, 1.0, 0.05)]) == 1.0
    pafs = [paf_of(spark, [("HF", 100, err, 1.0, 0.05)]) for err in (1.0, 1.1, 1.2, 1.3)]
    assert pafs == sorted(pafs, reverse=True) and pafs[0] > pafs[-1]


def test_ch07_fy2027_base_payment_by_hand(dollars):
    row = by_facility(dollars, "facility_id", "fiscal_year")[("360072", 2027)]
    expected = 2302.94 * 1.7776 * (4245.76 * 0.9236 + 2602.24)
    assert row.est_base_operating_drg_payments == pytest.approx(expected)
    assert row.est_base_operating_drg_payments == pytest.approx(26.71e6, rel=1e-3)
    assert row.standardized_amount_table == "1B"
    assert row.est_penalty == pytest.approx((1 - 0.9839) * expected)
    assert (row.contoso_facility_id, row.contoso_facility_name) == (
        "CH07",
        "Contoso Health Hawthorn",
    )
    assert row.is_estimate and row.method.startswith("Estimate.")


def test_penalty_totals_match_the_research_report(dollars):
    totals = {
        r.fiscal_year: r
        for r in dollars.groupBy("fiscal_year")
        .agg(
            F.sum("est_penalty").alias("penalty"),
            F.sum("est_base_operating_drg_payments").alias("base"),
            F.count(F.lit(1)).alias("facilities"),
        )
        .collect()
    }
    assert totals[2026].facilities == totals[2027].facilities == 12
    # FY2026 matches the report to the dollar; FY2027 uses the correction notice impact file,
    # whose wage indexes moved a few facilities, so it lands $3K above the report's final-rule
    # figure of $1,761,170.
    assert totals[2026].penalty == pytest.approx(1_959_057, abs=1)
    assert totals[2027].penalty == pytest.approx(1_764_148, abs=1)
    assert totals[2026].base == pytest.approx(525.4e6, rel=1e-3)


def test_penalty_rows_carry_no_cms_hospital_names(dollars, sensitivity):
    for df in (dollars, sensitivity):
        assert "facility_name" not in df.columns
        names = {r.contoso_facility_name for r in df.select("contoso_facility_name").collect()}
        assert all(n.startswith("Contoso Health ") for n in names)


def test_sensitivity_starts_at_the_penalty_and_never_rises(sensitivity):
    assert sensitivity.count() == 401 * 2 * 12
    totals = (
        sensitivity.groupBy("err_pass_through", "drop_points")
        .agg(F.sum("est_penalty").alias("penalty"), F.sum("est_penalty_avoided").alias("avoided"))
        .orderBy("err_pass_through", "drop_points")
        .collect()
    )
    for f in (0.5, 1.0):
        series = [r.penalty for r in totals if r.err_pass_through == f]
        assert series[0] == pytest.approx(1_753_000, rel=0.01)
        assert all(a >= b - 1e-6 for a, b in itertools.pairwise(series))
    at = {(r.err_pass_through, r.drop_points): r for r in totals}
    assert at[(0.5, 0.0)].avoided == 0
    # A full pass-through always avoids at least as much as half of one.
    assert at[(1.0, 1.0)].avoided > at[(0.5, 1.0)].avoided
    # Facilities already at a factor of 1.0 in FY2027 gain nothing.
    flat = sensitivity.where(F.col("contoso_facility_id").isin("CH06", "CH09", "CH10"))
    assert flat.agg(F.max("est_penalty_avoided")).first()[0] == 0


def test_program_cost_ranges():
    costs = {level: annual_program_cost(level) for level in ("low", "central", "high")}
    # Loaded hourly cost $60.94, $64.79 and $77.75; the report's $201K, $371K and $724K.
    assert costs["low"] == pytest.approx(200_739, abs=1)
    assert costs["central"] == pytest.approx(370_724, abs=1)
    assert costs["high"] == pytest.approx(724_342, abs=1)


def test_economics_arithmetic_and_break_even(sensitivity):
    rows = by_facility(
        gold_nurse_call_economics(sensitivity), "effect_scenario", "cost_level", "err_pass_through"
    )
    assert len(rows) == 4 * 3 * 2
    none = rows[("none", "central", 0.5)]
    assert (none.est_penalty_avoided, none.payer_savings) == (0, 0)
    assert none.hospital_net == pytest.approx(-370_724, abs=1)
    central = rows[("central", "central", 1.0)]
    assert central.readmissions_avoided == pytest.approx(ANNUAL_DISCHARGES / 100)
    assert central.payer_savings == pytest.approx(102 * 14_000)
    assert central.resource_cost_avoided == pytest.approx(102 * 18_100)
    assert central.hospital_net == pytest.approx(
        central.est_penalty_avoided - central.annual_program_cost
    )
    # The report's break-even drops, central cost: 0.70 points at a 0.5 pass-through, 0.35 at 1.0.
    assert rows[("central", "central", 0.5)].break_even_drop_points == pytest.approx(0.70, abs=0.02)
    assert rows[("central", "central", 1.0)].break_even_drop_points == pytest.approx(0.35, abs=0.02)
    assert rows[("none", "high", 0.5)].break_even_drop_points == pytest.approx(1.51, abs=0.02)
    # Contoso's upside is capped by the penalty it pays; the payer's is not.
    mde = rows[("pilot_mde", "low", 1.0)]
    assert mde.est_penalty_avoided < 1_753_000 < mde.payer_savings


def test_penalty_by_condition_sums_to_the_facility_penalty(dollars, measures):
    conditions = gold_penalty_by_condition(dollars, measures)
    sums = by_facility(
        conditions.groupBy("facility_id", "fiscal_year").agg(F.sum("est_penalty").alias("p")),
        "facility_id",
        "fiscal_year",
    )
    for key, row in by_facility(dollars, "facility_id", "fiscal_year").items():
        assert sums[key].p == pytest.approx(row.est_penalty, abs=0.01), key
    rows = by_facility(conditions, "facility_id", "measure", "fiscal_year")
    # CH07 FY2027: HF, CABG and THA/TKA count; HF's term is (1.1012 - 0.9950) x 0.0428.
    hf, pn = rows[("360072", "HF", 2027)], rows[("360072", "PN", 2027)]
    total = (1.1012 - 0.9950) * 0.0428 + (1.3017 - 0.9895) * 0.0296 + (1.2828 - 0.9936) * 0.0101
    assert hf.share_of_penalty == pytest.approx((1.1012 - 0.9950) * 0.0428 / total)
    assert (pn.counts_toward_penalty, pn.est_penalty) == (False, 0)
    assert rows[("360054", "HF", 2027)].share_of_penalty == 0
