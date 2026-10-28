"""Estimated HRRP penalty dollars for Contoso's facilities, FY2026 and FY2027, and how the
penalty would move if readmissions fell.

- gold_penalty_dollars: one row per Contoso facility and fiscal year. The penalty is
  (1 - payment adjustment factor) x base operating DRG payments (42 CFR 412.154). CMS publishes
  the factor but not the base, so the base is an estimate from the IPPS impact file:
  transfer-adjusted cases x transfer-adjusted case mix index x (labor amount x wage index +
  nonlabor amount), with the year's national standardized amounts (Tables 1A and 1B). Every
  dollar figure is an estimate.
- gold_penalty_by_condition: one row per Contoso facility, condition and fiscal year: each
  condition's share of the facility's estimated penalty, from its term in the PAF formula, so the
  two years can be compared by facility and condition.
- gold_penalty_sensitivity: one row per Contoso facility, readmission drop and ERR pass-through,
  FY2027. The PAF is recomputed from each condition's ERR, peer median and DRG payment ratio
  (`computed_paf`), never scaled, because it is zero below the median and capped at 3 percent.

See docs/penalty-dollars.md for every source and assumption.
"""

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.silver.governance import TableDoc, field, gold_tags

TABLE = "gold_penalty_dollars"
KEY = ["facility_id", "fiscal_year"]
SENSITIVITY_TABLE = "gold_penalty_sensitivity"
CONDITION_TABLE = "gold_penalty_by_condition"
CONDITION_KEY = ["facility_id", "measure", "fiscal_year"]
SENSITIVITY_KEY = ["facility_id", "fiscal_year", "drop_points", "err_pass_through"]

# National adjusted operating standardized amounts (labor-related, nonlabor-related), for a
# hospital that submitted quality data and is a meaningful EHR user; every Contoso facility is
# (Proxy Quality Reduction and Proxy EHR Reduction are 0 in both impact files). Table 1A applies
# when the wage index is above 1, Table 1B otherwise; all 12 Contoso facilities use 1B. FY2026:
# CMS-1833-F Tables 1A-1E; FY2027: the correction notice, CMS-1849-CN. Zips at
# https://www.cms.gov/files/zip/fy<year>-ipps-fr-table-1a-1e.zip, checked 2026-10-26.
STANDARDIZED_AMOUNTS: dict[int, dict[str, tuple[float, float]]] = {
    2026: {"1A": (4456.72, 2295.89), "1B": (4186.62, 2565.99)},
    2027: {"1A": (4519.68, 2328.32), "1B": (4245.76, 2602.24)},
}

# 42 CFR 412.154(c): the PAF is at least 0.97 from FY2015 (a 3 percent maximum reduction), and a
# condition counts only with 25 or more eligible discharges.
PAF_FLOOR = 0.97
MIN_DISCHARGES = 25

# The year the sensitivity holds fixed: the latest, and the first since FY2018 whose peer medians
# are in silver_hrrp_history too.
SENSITIVITY_FISCAL_YEAR = 2027
# Contoso's usual-care 30-day readmission rate, percent: CMS FY2026 observed rates for its CCNs,
# weighted by Contoso's mix (docs/pilot-design.md, section 2). A drop of d points is a relative
# drop of d / 16.9.
BASELINE_RATE = 16.9
# How much of a relative drop in readmissions reaches the ERR. CMS shrinks each hospital's
# predicted rate toward the mean, so less than all of it does, more so at low volume; CMS
# publishes no closed form, so 0.5 and 1.0 are the bounds.
ERR_PASS_THROUGH = (0.5, 1.0)
# Drops from 0 to 4 points in steps of 0.01, which covers the pilot's 3.44-point MDE and gives
# break-even to the nearest 0.01 point.
DROP_STEPS = 400
DROP_STEP = 0.01

METHOD = (
    "Estimate. Penalty = (1 - payment adjustment factor) x estimated base operating DRG "
    "payments; base = transfer-adjusted cases x transfer-adjusted case mix index x (labor "
    "standardized amount x wage index + nonlabor standardized amount), from the CMS IPPS final "
    "rule impact file (FY2027: correction notice) and Table 1A or 1B. Excludes IME, DSH, "
    "outliers and capital, as the HRRP base does."
)

CONTOSO_FIELDS = [
    field("contoso_facility_id", T.StringType(), "Contoso's own facility id, CH01 to CH12"),
    field("contoso_facility_name", T.StringType(), "Contoso's name for the facility"),
]

GOLD_PENALTY_DOLLARS_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field("fiscal_year", T.IntegerType(), "HRRP payment fiscal year", nullable=False),
        *CONTOSO_FIELDS,
        field("provider_type", T.StringType(), "CMS provider type from the IPPS impact file"),
        field("wage_index", T.DoubleType(), "The fiscal year's wage index (ratio)"),
        field(
            "transfer_adjusted_cases",
            T.DoubleType(),
            "Transfer-adjusted Medicare cases CMS models the year from",
        ),
        field("transfer_adjusted_cmi", T.DoubleType(), "Transfer-adjusted case mix index"),
        field(
            "standardized_amount_table",
            T.StringType(),
            "1A (wage index above 1) or 1B (at or below 1)",
        ),
        field("labor_amount", T.DoubleType(), "Labor-related standardized amount, dollars"),
        field("nonlabor_amount", T.DoubleType(), "Nonlabor-related standardized amount, dollars"),
        field(
            "est_base_operating_drg_payments",
            T.DoubleType(),
            "Estimated base operating DRG payments for the year, dollars (estimate)",
        ),
        field(
            "payment_adjustment_factor",
            T.DoubleType(),
            "HRRP payment adjustment factor CMS published for the year",
        ),
        field(
            "computed_payment_adjustment_factor",
            T.DoubleType(),
            "The factor recomputed from the supplemental file's per-condition terms, unrounded",
        ),
        field(
            "est_penalty",
            T.DoubleType(),
            "Estimated HRRP payment reduction, dollars: (1 - published factor) x estimated base",
        ),
        field("is_estimate", T.BooleanType(), "Always true: the base is estimated", nullable=False),
        field("method", T.StringType(), "How the estimate is computed", nullable=False),
    ]
)

GOLD_PENALTY_DOLLARS_DOC = TableDoc(
    comment=(
        "Estimated HRRP penalty dollars per Contoso facility, FY2026 and FY2027: the published "
        "payment adjustment factor applied to base operating DRG payments estimated from the "
        "IPPS impact file and Tables 1A/1B. Every dollar figure is an estimate. Built from "
        "silver_hrrp_payment, silver_hrrp_payment_measure, silver_ipps_impact and "
        "silver_contoso_facilities."
    ),
    tags=gold_tags("cms,contoso"),
)

GOLD_PENALTY_BY_CONDITION_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field(
            "measure",
            T.StringType(),
            "HRRP condition: AMI, HF, PN, COPD, HIP_KNEE or CABG",
            nullable=False,
        ),
        field("fiscal_year", T.IntegerType(), "HRRP payment fiscal year", nullable=False),
        *CONTOSO_FIELDS,
        field("discharges", T.IntegerType(), "Eligible discharges in the period"),
        field("excess_readmission_ratio", T.DoubleType(), "ERR (ratio)"),
        field("peer_median_err", T.DoubleType(), "Peer group median ERR for the condition"),
        field("drg_payment_ratio", T.DoubleType(), "The condition's DRG payment ratio"),
        field(
            "counts_toward_penalty",
            T.BooleanType(),
            "25 or more eligible discharges and an ERR above the peer median",
            nullable=False,
        ),
        field(
            "share_of_penalty",
            T.DoubleType(),
            "The condition's term over the facility's sum of terms in the PAF formula; 0 if none",
            nullable=False,
        ),
        field(
            "est_penalty",
            T.DoubleType(),
            "share_of_penalty x the facility's estimated penalty, dollars (estimate)",
        ),
        field("is_estimate", T.BooleanType(), "Always true", nullable=False),
    ]
)

GOLD_PENALTY_BY_CONDITION_DOC = TableDoc(
    comment=(
        "Estimated HRRP penalty per Contoso facility, condition and fiscal year, FY2026 and "
        "FY2027: the facility's estimated penalty split by each condition's term in the payment "
        "adjustment factor formula (DRG payment ratio x ERR above the peer median, 25 or more "
        "discharges). Sums to gold_penalty_dollars. Estimates. Built from gold_penalty_dollars "
        "and silver_hrrp_payment_measure."
    ),
    tags=gold_tags("cms,contoso"),
)

GOLD_PENALTY_SENSITIVITY_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field("fiscal_year", T.IntegerType(), "Fiscal year held fixed", nullable=False),
        *CONTOSO_FIELDS,
        field(
            "drop_points",
            T.DoubleType(),
            "Drop in the 30-day readmission rate, percentage points, from 16.9 percent",
            nullable=False,
        ),
        field("relative_drop", T.DoubleType(), "drop_points / 16.9", nullable=False),
        field(
            "err_pass_through",
            T.DoubleType(),
            "Share of the relative drop that reaches each ERR: 0.5 or 1.0",
            nullable=False,
        ),
        field("computed_paf", T.DoubleType(), "Recomputed payment adjustment factor, unrounded"),
        field(
            "est_penalty",
            T.DoubleType(),
            "Estimated penalty at this drop, dollars (estimate)",
        ),
        field(
            "est_penalty_avoided",
            T.DoubleType(),
            "Estimated penalty at no drop minus at this drop, dollars (estimate)",
        ),
        field("is_estimate", T.BooleanType(), "Always true", nullable=False),
    ]
)

GOLD_PENALTY_SENSITIVITY_DOC = TableDoc(
    comment=(
        "How each Contoso facility's estimated FY2027 HRRP penalty changes if its 30-day "
        "readmission rate fell 0 to 4 points, with 0.5 or 1.0 of the relative drop reaching "
        "each ERR. Peer medians, the neutrality modifier, DRG payment ratios and base payments "
        "stay at FY2027 values. Estimates. Built from gold_penalty_dollars and "
        "silver_hrrp_payment_measure."
    ),
    tags=gold_tags("cms,contoso"),
)


def contoso_ccns(contoso_facilities: DataFrame) -> DataFrame:
    """silver_contoso_facilities keyed by CCN, so CMS rows join on facility_id."""
    return contoso_facilities.select(
        F.col("ccn").alias("facility_id"),
        F.col("facility_id").alias("contoso_facility_id"),
        F.col("facility_name").alias("contoso_facility_name"),
    )


def _paf_term(err: Column) -> Column:
    """One condition's term in the PAF sum, before the neutrality modifier."""
    counted = (F.col("discharges") >= MIN_DISCHARGES) & F.col(
        "excess_readmission_ratio"
    ).isNotNull()
    excess = F.greatest(err - F.col("peer_median_err"), F.lit(0.0))
    return F.when(counted, F.col("drg_payment_ratio") * excess).otherwise(F.lit(0.0))


def computed_paf(
    measures: DataFrame, payment: DataFrame, err_scale: Column | None = None, by=()
) -> DataFrame:
    """The payment adjustment factor from its per-condition terms (42 CFR 412.154, FY2019 on):

        PAF = max(0.97, 1 - neutrality modifier x sum of DRG payment ratio x max(0, ERR - peer
        median ERR)), over conditions with 25 or more eligible discharges.

    `err_scale` multiplies every ERR first (a column of `measures`, for the sensitivity
    scenarios); `by` names extra grouping columns, such as the scenario. Unrounded; CMS publishes
    four decimals.
    """
    err = F.col("excess_readmission_ratio") * (err_scale if err_scale is not None else F.lit(1.0))
    sums = measures.groupBy("facility_id", "fiscal_year", *by).agg(
        F.sum(_paf_term(err)).alias("_ratio")
    )
    modifier = payment.select("facility_id", "fiscal_year", "neutrality_modifier")
    return sums.join(modifier, ["facility_id", "fiscal_year"]).select(
        "facility_id",
        "fiscal_year",
        *by,
        F.greatest(
            F.lit(PAF_FLOOR), F.lit(1.0) - F.col("neutrality_modifier") * F.col("_ratio")
        ).alias("computed_paf"),
    )


def _standardized_amounts(spark) -> DataFrame:
    rows = [
        (year, table, labor, nonlabor)
        for year, tables in STANDARDIZED_AMOUNTS.items()
        for table, (labor, nonlabor) in tables.items()
    ]
    return spark.createDataFrame(
        rows,
        "fiscal_year int, standardized_amount_table string, labor_amount double, "
        "nonlabor_amount double",
    )


def gold_penalty_dollars(
    payment: DataFrame, measures: DataFrame, impact: DataFrame, contoso_facilities: DataFrame
) -> DataFrame:
    """`payment`, `measures` and `impact` are silver_hrrp_payment, silver_hrrp_payment_measure
    and silver_ipps_impact; one row per Contoso facility and each fiscal year with standardized
    amounts. Hospital names come only from Contoso's roster."""
    contoso = contoso_ccns(contoso_facilities)
    table = F.when(F.col("wage_index") > 1, "1A").otherwise("1B")
    base = (
        F.col("transfer_adjusted_cases")
        * F.col("transfer_adjusted_cmi")
        * (F.col("labor_amount") * F.col("wage_index") + F.col("nonlabor_amount"))
    )
    df = (
        impact.where(F.col("fiscal_year").isin(*STANDARDIZED_AMOUNTS))
        .join(contoso, "facility_id")
        .withColumn("standardized_amount_table", table)
        .join(
            _standardized_amounts(impact.sparkSession),
            ["fiscal_year", "standardized_amount_table"],
        )
        .withColumn("est_base_operating_drg_payments", base)
        .join(
            payment.select("facility_id", "fiscal_year", "payment_adjustment_factor"),
            KEY,
            "left",
        )
        .join(
            computed_paf(measures, payment).withColumnRenamed(
                "computed_paf", "computed_payment_adjustment_factor"
            ),
            KEY,
            "left",
        )
        .withColumn(
            "est_penalty",
            (F.lit(1.0) - F.col("payment_adjustment_factor"))
            * F.col("est_base_operating_drg_payments"),
        )
        .withColumn("is_estimate", F.lit(True))
        .withColumn("method", F.lit(METHOD))
    )
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in GOLD_PENALTY_DOLLARS_SCHEMA]
    )


def gold_penalty_by_condition(penalty_dollars: DataFrame, measures: DataFrame) -> DataFrame:
    """`penalty_dollars` is gold_penalty_dollars, `measures` silver_hrrp_payment_measure. The
    neutrality modifier is common to a facility's terms, so the shares need only the terms; the
    0.97 floor, if it bound, would scale every term alike."""
    facility = penalty_dollars.select(
        *KEY, "contoso_facility_id", "contoso_facility_name", F.col("est_penalty").alias("_total")
    )
    facility_sum = F.sum("_term").over(Window.partitionBy(*KEY))
    df = (
        measures.join(facility, KEY)
        .withColumn("_term", _paf_term(F.col("excess_readmission_ratio")))
        .withColumn("counts_toward_penalty", F.col("_term") > 0)
        .withColumn(
            "share_of_penalty",
            F.when(facility_sum > 0, F.col("_term") / facility_sum).otherwise(F.lit(0.0)),
        )
        .withColumn("est_penalty", F.col("share_of_penalty") * F.col("_total"))
        .withColumn("is_estimate", F.lit(True))
    )
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in GOLD_PENALTY_BY_CONDITION_SCHEMA]
    )


def drop_scenarios(spark) -> DataFrame:
    """Every drop on the 0.01-point grid with each pass-through; `drop_step` is the grid index."""
    drops = spark.range(DROP_STEPS + 1).select(F.col("id").cast("int").alias("drop_step"))
    pass_through = spark.createDataFrame(
        [(f,) for f in ERR_PASS_THROUGH], "err_pass_through double"
    )
    return (
        drops.crossJoin(pass_through)
        .withColumn("drop_points", F.round(F.col("drop_step") * F.lit(DROP_STEP), 2))
        .withColumn("relative_drop", F.col("drop_points") / F.lit(BASELINE_RATE))
    )


def gold_penalty_sensitivity(
    penalty_dollars: DataFrame,
    measures: DataFrame,
    payment: DataFrame,
    fiscal_year: int = SENSITIVITY_FISCAL_YEAR,
) -> DataFrame:
    """`penalty_dollars` is gold_penalty_dollars; `measures` and `payment` silver_hrrp_payment_measure
    and silver_hrrp_payment. Each ERR is multiplied by 1 - pass-through x relative drop."""
    year = F.col("fiscal_year") == fiscal_year
    base = penalty_dollars.where(year).select(
        "facility_id",
        "fiscal_year",
        "contoso_facility_id",
        "contoso_facility_name",
        "est_base_operating_drg_payments",
    )
    scenarios = drop_scenarios(penalty_dollars.sparkSession)
    scaled = (
        measures.where(year).join(base.select("facility_id"), "facility_id").crossJoin(scenarios)
    )
    by = ["drop_step", "drop_points", "relative_drop", "err_pass_through"]
    paf = computed_paf(
        scaled,
        payment.where(year),
        F.lit(1.0) - F.col("err_pass_through") * F.col("relative_drop"),
        by,
    )
    penalty = (F.lit(1.0) - F.col("computed_paf")) * F.col("est_base_operating_drg_payments")
    df = base.join(paf, ["facility_id", "fiscal_year"]).withColumn("est_penalty", penalty)
    at_zero = df.where(F.col("drop_step") == 0).select(
        "facility_id", "err_pass_through", F.col("est_penalty").alias("_at_zero")
    )
    df = (
        df.join(at_zero, ["facility_id", "err_pass_through"])
        .withColumn("est_penalty_avoided", F.col("_at_zero") - F.col("est_penalty"))
        .withColumn("is_estimate", F.lit(True))
    )
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in GOLD_PENALTY_SENSITIVITY_SCHEMA]
    )
