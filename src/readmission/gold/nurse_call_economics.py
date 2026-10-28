"""gold_nurse_call_economics: what a 72-hour nurse follow-up call program would cost Contoso and
what it could save, as ranges.

One row per effect scenario, program cost level and ERR pass-through. The headline is Contoso's
own ledger, `hospital_net`: the FY2027 penalty avoided (gold_penalty_sensitivity) minus the
program's annual cost. Payer savings and the resource cost of avoided readmissions are other
parties' money and are shown beside it, never added to it: under DRG payment an avoided
readmission also removes the payment Contoso would have received for it.

Every value is an estimate; the cost inputs are wage data and time assumptions, not a published
cost per call. See docs/penalty-dollars.md.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.gold.penalty_dollars import DROP_STEP
from readmission.silver.governance import TableDoc, field, gold_tags

TABLE = "gold_nurse_call_economics"
KEY = ["effect_scenario", "cost_level", "err_pass_through"]

# Absolute drops in the 30-day readmission rate, percentage points, from 16.9 percent.
EFFECT_SCENARIOS: list[tuple[str, float, str]] = [
    (
        "none",
        0.0,
        (
            "Pooled RCTs of post-discharge calls: risk difference 0.00, 95% CI -0.02 to 0.02 "
            "(Boggan 2025, 7 RCTs)"
        ),
    ),
    (
        "central",
        1.0,
        "A judgment between the pooled null estimate and its upper confidence bound",
    ),
    (
        "high",
        3.0,
        (
            "Multicomponent discharge bundles, RR 0.82 (Leppin 2014) on 16.9 percent; not a call "
            "alone"
        ),
    ),
    (
        "pilot_mde",
        3.44,
        "The pilot's minimum detectable effect; larger than the evidence supports",
    ),
]

# HRRP-condition discharges a year across Contoso, all payers: 1,366 a month, October 2025 to
# September 2026 (docs/pilot-design.md, section 2), rounded. Synthetic, like all of Contoso's data;
# 10,200 before the all-payer factor went from 2.0 to 3.2 (docs/synthetic-data.md, section 7).
ANNUAL_DISCHARGES = 16_400
# Ohio registered nurse hourly wage, BLS OEWS May 2025, SOC 29-1141: median $39.67 (series
# OEUS390000000000029114108), mean $42.18 (OEUS390000000000029114103).
RN_WAGE = {"low": 39.67, "central": 42.18, "high": 42.18}
# Benefits are 34.9 percent of total compensation for civilian hospital workers, BLS ECEC Q2
# 2026 (series CMU1036220000000P), so a wage dollar costs 1 / (1 - 0.349) loaded.
BENEFITS_SHARE = 0.349
# Supervision, telephony and space on top of loaded wages, high case only. An assumption.
OVERHEAD = {"low": 0.0, "central": 0.0, "high": 0.20}
# Nurse minutes per discharge, failed attempts, voicemail, charting and escalation included.
# Central: Harrison 2014 (UCSF), 0.5 FTE for 4,115 patients over 18 months, about 23 minutes per
# patient. Low: a 5-minute completed call plus attempts and charting. High: a structured script
# with medication reconciliation. Low and high are assumptions.
NURSE_MINUTES = {"low": 15, "central": 23, "high": 35}
# One-time setup (script, training, telephony, EHR template), counted in the first year, and
# program management in FTEs. Both are assumptions; no published figure was found.
SETUP_COST = {"low": 20_000.0, "central": 50_000.0, "high": 100_000.0}
MANAGEMENT_FTE = {"low": 0.2, "central": 0.5, "high": 1.0}
FTE_HOURS = 2080
COST_LEVELS = ("low", "central", "high")

# What Medicare pays per Contoso discharge, all DRGs: $13,986 (Medicare Inpatient Hospitals by
# Provider, DY2024), rounded. The payer's saving per avoided readmission.
PAYER_PAYMENT_PER_READMISSION = 14_000.0
# Hospital cost per Medicare readmission, $18,100 in 2020 (HCUP Statistical Brief #307). A
# societal resource cost, not Contoso's saving.
RESOURCE_COST_PER_READMISSION = 18_100.0

GOLD_NURSE_CALL_ECONOMICS_SCHEMA = T.StructType(
    [
        field(
            "effect_scenario",
            T.StringType(),
            "none, central, high or pilot_mde",
            nullable=False,
        ),
        field(
            "drop_points",
            T.DoubleType(),
            "Assumed drop in the 30-day readmission rate, percentage points",
            nullable=False,
        ),
        field("effect_basis", T.StringType(), "Where the effect size comes from", nullable=False),
        field("cost_level", T.StringType(), "low, central or high", nullable=False),
        field(
            "err_pass_through",
            T.DoubleType(),
            "Share of the relative drop that reaches each ERR: 0.5 or 1.0",
            nullable=False,
        ),
        field(
            "loaded_hourly_cost",
            T.DoubleType(),
            "Nurse wage with benefits (and overhead, high case), dollars an hour",
            nullable=False,
        ),
        field(
            "annual_program_cost",
            T.DoubleType(),
            "First-year cost: nurse time, setup and management, dollars (estimate)",
            nullable=False,
        ),
        field(
            "readmissions_avoided",
            T.DoubleType(),
            "Readmissions avoided a year across 16,400 all-payer discharges",
            nullable=False,
        ),
        field(
            "est_penalty_avoided",
            T.DoubleType(),
            "FY2027 HRRP penalty avoided, dollars (estimate)",
        ),
        field(
            "hospital_net",
            T.DoubleType(),
            "Contoso's ledger: penalty avoided minus program cost, dollars (estimate)",
        ),
        field(
            "break_even_drop_points",
            T.DoubleType(),
            "Smallest drop, to 0.01 point, at which the penalty avoided covers the program cost",
        ),
        field(
            "payer_savings",
            T.DoubleType(),
            "Payers' saving at $14,000 per avoided readmission, dollars; not Contoso's, never "
            "added to hospital_net",
            nullable=False,
        ),
        field(
            "resource_cost_avoided",
            T.DoubleType(),
            "Societal resource cost avoided at $18,100 per readmission (HCUP), dollars; not "
            "Contoso's",
            nullable=False,
        ),
        field("is_estimate", T.BooleanType(), "Always true", nullable=False),
    ]
)

GOLD_NURSE_CALL_ECONOMICS_DOC = TableDoc(
    comment=(
        "Cost and savings of a 72-hour nurse follow-up call program at Contoso, one row per "
        "effect scenario, cost level and ERR pass-through. hospital_net (FY2027 penalty "
        "avoided minus program cost) is Contoso's ledger; payer_savings and "
        "resource_cost_avoided belong to others and are never added to it. Estimates from "
        "stated assumptions. Built from gold_penalty_sensitivity."
    ),
    tags=gold_tags("cms,contoso,assumptions"),
)


def loaded_hourly_cost(level: str) -> float:
    return RN_WAGE[level] / (1 - BENEFITS_SHARE) * (1 + OVERHEAD[level])


def annual_program_cost(level: str, discharges: int = ANNUAL_DISCHARGES) -> float:
    """Nurse time for every discharge, plus setup and management at the same loaded rate."""
    hourly = loaded_hourly_cost(level)
    calls = discharges * NURSE_MINUTES[level] / 60 * hourly
    management = MANAGEMENT_FTE[level] * FTE_HOURS * hourly
    return calls + SETUP_COST[level] + management


def _assumptions(spark) -> DataFrame:
    rows = [
        (name, drop, basis, level, loaded_hourly_cost(level), annual_program_cost(level))
        for name, drop, basis in EFFECT_SCENARIOS
        for level in COST_LEVELS
    ]
    return spark.createDataFrame(
        rows,
        "effect_scenario string, drop_points double, effect_basis string, cost_level string, "
        "loaded_hourly_cost double, annual_program_cost double",
    )


def _step(column: str) -> F.Column:
    return F.round(F.col(column) / F.lit(DROP_STEP)).cast("int")


def gold_nurse_call_economics(sensitivity: DataFrame) -> DataFrame:
    """`sensitivity` is gold_penalty_sensitivity (every Contoso facility, every drop on the
    0.01-point grid, each pass-through)."""
    totals = sensitivity.groupBy("drop_points", "err_pass_through").agg(
        F.sum("est_penalty_avoided").alias("est_penalty_avoided")
    )
    totals = totals.withColumn("_step", _step("drop_points"))
    assumptions = _assumptions(sensitivity.sparkSession)
    costs = assumptions.select("cost_level", "annual_program_cost").distinct()
    break_even = (
        totals.crossJoin(costs)
        .where(F.col("est_penalty_avoided") >= F.col("annual_program_cost"))
        .groupBy("cost_level", "err_pass_through")
        .agg(F.min("drop_points").alias("break_even_drop_points"))
    )
    readmissions = F.lit(ANNUAL_DISCHARGES) * F.col("drop_points") / F.lit(100.0)
    df = (
        assumptions.withColumn("_step", _step("drop_points"))
        .join(totals.drop("drop_points"), "_step")
        .join(break_even, ["cost_level", "err_pass_through"], "left")
        .withColumn("readmissions_avoided", readmissions)
        .withColumn("hospital_net", F.col("est_penalty_avoided") - F.col("annual_program_cost"))
        .withColumn("payer_savings", readmissions * F.lit(PAYER_PAYMENT_PER_READMISSION))
        .withColumn("resource_cost_avoided", readmissions * F.lit(RESOURCE_COST_PER_READMISSION))
        .withColumn("is_estimate", F.lit(True))
    )
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in GOLD_NURSE_CALL_ECONOMICS_SCHEMA]
    )
