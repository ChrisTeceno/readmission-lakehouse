# Databricks notebook source
# MAGIC %md
# MAGIC # 03 Power: a pilot of 72-hour post-discharge follow-up calls at Contoso
# MAGIC
# MAGIC Simulation-based power for the pilot designed in `docs/pilot-design.md`. Every number in that
# MAGIC document comes from this notebook, with the fixed seed below.
# MAGIC
# MAGIC Inputs:
# MAGIC - Contoso's discharges and follow-up calls for the HRRP conditions, the latest 12 months of
# MAGIC   `silver_contoso_discharges` and `silver_contoso_followup_calls`. **These are synthetic**
# MAGIC   (seeded by `scripts/seed_contoso.py` onto 12 real Ohio CCNs); they set volumes and the
# MAGIC   current call rates, nothing else.
# MAGIC - 30-day readmission rates from public CMS data for those 12 CCNs, and the between-hospital
# MAGIC   variation among all Ohio hospitals: `gold_facility_condition_year`, FY2023 and FY2026.
# MAGIC
# MAGIC On Databricks the notebook reads the tables and checks them against the committed extract
# MAGIC in `analysis/data/`; anywhere else it reads the extract, so
# MAGIC `uv run python analysis/03_power.py` reproduces the same numbers locally.
# MAGIC
# MAGIC Sections:
# MAGIC 1. Contoso's volumes and current follow-up calls
# MAGIC 2. Readmission baselines from CMS
# MAGIC 3. Intra-cluster correlation and cluster autocorrelation
# MAGIC 4. Designs compared
# MAGIC 5. The chosen design
# MAGIC 6. How the MDE moves with the ICC
# MAGIC 7. Patient-level randomization, for reference
# MAGIC 8. Numbers quoted in the pilot design

# COMMAND ----------

import sys
from pathlib import Path

import matplotlib

if "spark" not in globals():
    matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Run from a workspace copy of the repo, src/ sits next to analysis/; locally the package is
# installed by uv.
HERE = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if (HERE.parent / "src").exists():
    sys.path.insert(0, str(HERE.parent / "src"))

from readmission.analysis import power as pw

CATALOG = "workspace"
SCHEMA = "readmission"
DATA = HERE / "data"
ON_DATABRICKS = "spark" in globals()

SEED = 20261021
REPS = 2000
PERMUTATIONS = 999
EFFECTS = np.round(np.arange(0, 0.1001, 0.0025), 4)
ALPHA = pw.ALPHA
POWER_TARGET = pw.POWER_TARGET
CROSS_SECTION_YEAR = 2026
# FY2023's window (07/2018 to 06/2021) ends the day before FY2026's starts: no shared discharges.
EARLIER_WINDOW_YEAR = 2023
OPERATIONS_MONTHS = 12

# Planning values (section 3 shows where they come from) and the design the pilot commits to.
PLANNING_ICC = 0.01
PLANNING_CAC = 0.7
ICC_GRID = [0.005, 0.01, 0.02, 0.03, 0.05]
CAC_GRID = [0.5, 0.7, 0.9]
CHOSEN = pw.stepped_wedge(6)
PERIOD_MONTHS = 3
DURATIONS = [14, 21, 28]
FOLLOW_UP_MONTHS = 1

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GRAY, LIGHT_GRAY, INK, MUTED = "#a3a29c", "#e4e3df", "#0b0b0b", "#52514e"

plt.rcParams.update(
    {
        "figure.figsize": (9, 4.5),
        "figure.dpi": 90,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": GRAY,
        "axes.labelcolor": MUTED,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlecolor": INK,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": LIGHT_GRAY,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "legend.frameon": False,
        "lines.linewidth": 2,
    }
)


def show(fig) -> None:
    fig.tight_layout()
    plt.show()
    plt.close(fig)


def pp(x: float) -> str:
    """An absolute rate difference in percentage points."""
    return "not reached" if np.isnan(x) else f"{100 * x:.2f} pp"


# COMMAND ----------

HRRP_SQL = f"""
select facility_id, measure, fiscal_year, discharges, readmissions, predicted_rate, expected_rate
from {CATALOG}.{SCHEMA}.gold_facility_condition_year
where state = 'OH' and fiscal_year in ({EARLIER_WINDOW_YEAR}, {CROSS_SECTION_YEAR})
order by facility_id, measure, fiscal_year
"""

OPERATIONS_SQL = f"""
with months as (
  select * from {CATALOG}.{SCHEMA}.silver_contoso_discharges
  where discharge_month > add_months(
    (select max(discharge_month) from {CATALOG}.{SCHEMA}.silver_contoso_discharges),
    -{OPERATIONS_MONTHS}
  )
),
calls as (
  select facility_id, discharge_month, condition,
    sum(calls_attempted) calls_attempted, sum(calls_completed) calls_completed,
    sum(case when within_72h then calls_completed else 0 end) calls_completed_within_72h
  from {CATALOG}.{SCHEMA}.silver_contoso_followup_calls
  group by all
)
select f.facility_id contoso_facility_id, f.ccn facility_id, m.condition measure,
  count(*) months, min(m.discharge_month) first_month, max(m.discharge_month) last_month,
  sum(m.discharges) discharges, sum(c.calls_attempted) calls_attempted,
  sum(c.calls_completed) calls_completed,
  sum(c.calls_completed_within_72h) calls_completed_within_72h
from months m
join {CATALOG}.{SCHEMA}.silver_contoso_facilities f using (facility_id)
left join calls c using (facility_id, discharge_month, condition)
group by all
order by 1, 3
"""


def from_extract(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / name, dtype={"facility_id": str})


def same_as_extract(df: pd.DataFrame, name: str) -> bool:
    committed = from_extract(name)
    df = df.astype({c: str for c in ["first_month", "last_month"] if c in df})
    return df.shape == committed.shape and np.allclose(
        df.select_dtypes("number").astype(float).to_numpy(),
        committed.select_dtypes("number").astype(float).to_numpy(),
        equal_nan=True,
    )


if ON_DATABRICKS:
    hrrp = spark.sql(HRRP_SQL).toPandas()  # noqa: F821 (Databricks global)
    operations = spark.sql(OPERATIONS_SQL).toPandas()  # noqa: F821
    for df, name in [(hrrp, "pilot_ohio_hrrp.csv"), (operations, "pilot_contoso_operations.csv")]:
        if (DATA / name).exists():
            print(f"{name}: tables match the committed extract: {same_as_extract(df, name)}")
        else:
            print(f"{name}: no committed extract next to this notebook, reading tables only")
else:
    hrrp = from_extract("pilot_ohio_hrrp.csv")
    operations = from_extract("pilot_contoso_operations.csv")

for c in ["discharges", "readmissions", "predicted_rate", "expected_rate"]:
    hrrp[c] = pd.to_numeric(hrrp[c])
print(f"Ohio HRRP rows: {len(hrrp):,}; Contoso facility-condition rows: {len(operations)}")
print(
    f"Contoso months: {operations['first_month'].min()} to {operations['last_month'].max()}, "
    f"{int(operations['months'].max())} months"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Contoso's volumes and current follow-up calls
# MAGIC
# MAGIC Synthetic. Volumes are all-payer discharges for the six HRRP conditions; the 72-hour rate is
# MAGIC completed calls made within 72 hours over discharges. The pilot's control condition is
# MAGIC this usual care, so the intervention's contrast is the step from today's rate to the
# MAGIC structured program's.

# COMMAND ----------

facilities = (
    operations.groupby(["contoso_facility_id", "facility_id"])[
        ["discharges", "calls_attempted", "calls_completed", "calls_completed_within_72h"]
    ]
    .sum()
    .reset_index()
)
facilities["monthly_discharges"] = facilities["discharges"] / OPERATIONS_MONTHS
facilities["completed_within_72h_rate"] = (
    facilities["calls_completed_within_72h"] / facilities["discharges"]
)
facilities["attempt_rate"] = facilities["calls_attempted"] / facilities["discharges"]
network_monthly = facilities["monthly_discharges"].sum()
network_72h = facilities["calls_completed_within_72h"].sum() / facilities["discharges"].sum()
print(f"Network: {network_monthly:.0f} HRRP-condition discharges a month (synthetic)")
print(f"Completed within 72 hours today: {network_72h:.1%} of discharges (synthetic)")
print(
    "Range across facilities: "
    f"{facilities['completed_within_72h_rate'].min():.1%} to "
    f"{facilities['completed_within_72h_rate'].max():.1%}"
)
facilities[
    ["contoso_facility_id", "facility_id", "monthly_discharges", "attempt_rate"]
    + ["completed_within_72h_rate"]
].round(3)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Readmission baselines from CMS
# MAGIC
# MAGIC Each facility's usual-care readmission rate is its FY2026 CMS observed rate per condition
# MAGIC (readmissions over discharges, Medicare fee-for-service, 07/2021 to 06/2024), averaged
# MAGIC with weights from Contoso's own condition mix. All-payer rates usually run a little below
# MAGIC Medicare's; using Medicare's makes the binomial variance, and so the MDE, slightly larger.

# COMMAND ----------

cms = hrrp[hrrp["fiscal_year"] == CROSS_SECTION_YEAR].assign(
    rate=lambda d: d["readmissions"] / d["discharges"]
)
mix = operations.merge(cms[["facility_id", "measure", "rate"]], on=["facility_id", "measure"])
missing = mix["rate"].isna().sum()
print(f"Contoso facility-conditions without a CMS observed rate: {missing}")
baseline = (
    mix.groupby("contoso_facility_id")
    .apply(lambda d: np.average(d["rate"], weights=d["discharges"]))
    .rename("baseline_rate")
)
facilities = facilities.merge(baseline, left_on="contoso_facility_id", right_index=True)
network_rate = np.average(facilities["baseline_rate"], weights=facilities["discharges"])
print(f"Network usual-care 30-day readmission rate: {network_rate:.1%}")
print(
    f"Facility range: {facilities['baseline_rate'].min():.1%} to "
    f"{facilities['baseline_rate'].max():.1%}"
)
volumes = facilities["monthly_discharges"].to_numpy()
rates = facilities["baseline_rate"].to_numpy()
facilities[["contoso_facility_id", "monthly_discharges", "baseline_rate"]].round(3)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Intra-cluster correlation and cluster autocorrelation
# MAGIC
# MAGIC **ICC.** Among Ohio hospitals, per condition, the one-way ANOVA estimator on readmission
# MAGIC counts, with a bootstrap interval over hospitals. CMS suppresses counts under 11, so small
# MAGIC hospitals drop out; the estimate describes mid-size and large hospitals over three-year
# MAGIC windows. The planning ICC is set well above these estimates because a pilot measures
# MAGIC all-payer rates over months, which vary more than three-year Medicare averages.
# MAGIC
# MAGIC **CAC.** The share of between-hospital variance that is stable over time, from the
# MAGIC correlation of hospitals' true rates between FY2023 and FY2026, two windows with no
# MAGIC discharges in common, pooled over conditions. It measures drift over three years in
# MAGIC three-year averages; quarter-to-quarter shocks average out of CMS windows and cannot be
# MAGIC seen here, so the planning CAC sits below it.

# COMMAND ----------

counted = hrrp.dropna(subset=["discharges", "readmissions"])
counted = counted[counted["discharges"] > 0]
icc_rows = []
for (year, measure), g in counted.groupby(["fiscal_year", "measure"]):
    low, high = pw.bootstrap_icc(g["readmissions"], g["discharges"], 1000, SEED)
    icc_rows.append(
        {
            "fiscal_year": year,
            "measure": measure,
            "hospitals": len(g),
            "rate": g["readmissions"].sum() / g["discharges"].sum(),
            "icc": pw.anova_icc(g["readmissions"], g["discharges"]),
            "icc_low": low,
            "icc_high": high,
        }
    )
icc_table = pd.DataFrame(icc_rows)
current = icc_table[icc_table["fiscal_year"] == CROSS_SECTION_YEAR].set_index("measure")
contoso_mix = operations.groupby("measure")["discharges"].sum()
mix_icc = float(np.average(current.loc[contoso_mix.index, "icc"], weights=contoso_mix))
print(f"ICC weighted by Contoso's condition mix, FY{CROSS_SECTION_YEAR}: {mix_icc:.4f}")
print(f"Highest upper bound across conditions: {icc_table['icc_high'].max():.4f}")

pairs = counted.pivot_table(
    index=["facility_id", "measure"],
    columns="fiscal_year",
    values=["readmissions", "discharges"],
).dropna()
covariance = variance_early = variance_late = 0.0
for measure, g in pairs.groupby(level="measure"):
    if len(g) < 20:
        continue
    early = (g[("readmissions", EARLIER_WINDOW_YEAR)], g[("discharges", EARLIER_WINDOW_YEAR)])
    late = (g[("readmissions", CROSS_SECTION_YEAR)], g[("discharges", CROSS_SECTION_YEAR)])
    weight = len(g) - 1
    covariance += weight * np.cov(early[0] / early[1], late[0] / late[1])[0, 1]
    variance_early += weight * pw.between_variance(*early)
    variance_late += weight * pw.between_variance(*late)
cac_floor = covariance / np.sqrt(variance_early * variance_late)
print(
    f"Correlation of true rates, FY{EARLIER_WINDOW_YEAR} vs FY{CROSS_SECTION_YEAR}, pooled over "
    f"conditions with 20 or more hospitals: {cac_floor:.2f}"
)
icc_table.round(4)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Designs compared
# MAGIC
# MAGIC At the planning ICC and CAC, the MDE (80 percent power, two-sided alpha 0.05, permutation
# MAGIC test) of each design over three enrollment durations. Every design randomizes within size
# MAGIC strata: stepped wedges give each step facilities from different size bands, parallel
# MAGIC designs randomize within pairs of similar size. The parallel design with a baseline uses
# MAGIC one seventh of the duration as its baseline period.

# COMMAND ----------


def cell_sizes(design: pw.Design, months: float) -> np.ndarray:
    """Expected discharges per facility and design period, `months` long."""
    return np.round(volumes[:, None] * months * np.ones((1, design.periods)))


def run(design: pw.Design, duration: float, icc: float, cac: float) -> pw.PowerResult:
    sizes = cell_sizes(design, duration / design.periods)
    strata = pw.size_strata(volumes, design.sequences)
    return pw.simulate_power(
        design, sizes, rates, EFFECTS, icc, cac, strata, REPS, PERMUTATIONS, SEED, ALPHA
    )


candidates = [
    pw.stepped_wedge(4),
    pw.stepped_wedge(6),
    pw.stepped_wedge(12),
    pw.parallel_with_baseline(7),
    pw.parallel_post_only(),
]
comparison = pd.DataFrame(
    [
        {
            "design": design.name,
            "months": duration,
            "mde": pw.minimum_detectable_effect(
                EFFECTS, run(design, duration, PLANNING_ICC, PLANNING_CAC).power
            ),
        }
        for design in candidates
        for duration in DURATIONS
    ]
)
comparison_wide = comparison.pivot(index="design", columns="months", values="mde")
comparison_wide.applymap(pp)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. The chosen design
# MAGIC
# MAGIC Stepped wedge, 6 steps of 2 facilities (one from the six larger, one from the six smaller),
# MAGIC 7 periods of 3 months: 21 months of enrollment, one more month for the last discharges'
# MAGIC 30-day outcomes.

# COMMAND ----------

chosen_duration = CHOSEN.periods * PERIOD_MONTHS
chosen = run(CHOSEN, chosen_duration, PLANNING_ICC, PLANNING_CAC)
chosen_mde = pw.minimum_detectable_effect(EFFECTS, chosen.power)
sizes = cell_sizes(CHOSEN, PERIOD_MONTHS)
treated_share = CHOSEN.schedule.mean()
total_discharges = sizes.sum()
power_at = {e: float(chosen.power[np.isclose(EFFECTS, e)][0]) for e in [0.01, 0.02, 0.03, 0.04]}
print(f"Design: {CHOSEN.name}, {CHOSEN.periods} periods of {PERIOD_MONTHS} months")
print(f"Enrollment {chosen_duration} months, plus {FOLLOW_UP_MONTHS} month of follow-up")
print(f"Index discharges: {total_discharges:,.0f}; facility-periods: {sizes.size}")
print(f"Facility-periods on the intervention: {treated_share:.0%}")
print(f"Type I error at no effect: {chosen.power[0]:.3f}")
print(
    f"MDE at {POWER_TARGET:.0%} power: {pp(chosen_mde)} ({chosen_mde / network_rate:.0%} relative)"
)
for effect, p in power_at.items():
    print(f"Power for a {pp(effect)} reduction: {p:.0%}")

fig, ax = plt.subplots()
ax.plot(100 * EFFECTS, chosen.power, color=BLUE)
ax.axhline(POWER_TARGET, color=MUTED, linestyle="--", linewidth=1)
ax.axvline(100 * chosen_mde, color=ORANGE, linewidth=1)
ax.set_xlabel("Absolute reduction in 30-day readmission rate (percentage points)")
ax.set_ylabel("Power")
ax.set_title(f"The pilot detects a {100 * chosen_mde:.1f}-point reduction 80 percent of the time")
show(fig)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. How the MDE moves with the ICC
# MAGIC
# MAGIC The chosen design against the parallel designs of the same 21 months, across the ICC grid
# MAGIC and three CACs. Designs that compare a facility with itself lose only the unstable share of
# MAGIC the between-facility variance, ICC times (1 - CAC); the parallel design without a baseline
# MAGIC loses all of it.

# COMMAND ----------

grid_designs = [CHOSEN, pw.parallel_with_baseline(7), pw.parallel_post_only()]
grid = pd.DataFrame(
    [
        {
            "design": design.name,
            "icc": icc,
            "cac": cac,
            "mde": pw.minimum_detectable_effect(
                EFFECTS, run(design, chosen_duration, icc, cac).power
            ),
        }
        for design in grid_designs
        for icc in ICC_GRID
        for cac in (CAC_GRID if design.within_facility else [PLANNING_CAC])
    ]
)
chosen_grid = grid[grid["design"] == CHOSEN.name].pivot(index="cac", columns="icc", values="mde")

fig, ax = plt.subplots()
for cac, color in zip(CAC_GRID, [ORANGE, BLUE, AQUA]):
    ax.plot(ICC_GRID, 100 * chosen_grid.loc[cac], color=color, marker="o", label=f"CAC {cac}")
post = grid[grid["design"] == pw.parallel_post_only().name]
ax.plot(post["icc"], 100 * post["mde"], color=GRAY, marker="o", label="parallel, no baseline")
ax.set_xlabel("ICC")
ax.set_ylabel("MDE (percentage points)")
ax.legend()
ax.set_title("The stepped wedge's MDE grows with the ICC only through its unstable share")
show(fig)
grid_wide = grid.pivot_table(index=["design", "cac"], columns="icc", values="mde")
grid_wide.applymap(pp)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Patient-level randomization, for reference
# MAGIC
# MAGIC If patients rather than facilities were randomized, half to the structured call, the
# MAGIC normal approximation for two proportions gives the MDE below for the same 21 months. It is
# MAGIC not the chosen design (see `docs/pilot-design.md`, section 3); it shows what clustering
# MAGIC costs.

# COMMAND ----------

z = 1.959964 + 0.841621
per_arm = total_discharges / 2
individual_mde = z * np.sqrt(2 * network_rate * (1 - network_rate) / per_arm)
print(f"Patient-level randomization, {per_arm:,.0f} per arm: MDE {pp(individual_mde)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Numbers quoted in the pilot design

# COMMAND ----------

stepped = comparison.set_index(["design", "months"])["mde"]
summary = {
    "network monthly discharges (synthetic)": f"{network_monthly:.0f}",
    "completed within 72h today (synthetic)": f"{network_72h:.1%}",
    "usual-care readmission rate (CMS FY2026)": f"{network_rate:.1%}",
    "ICC, Contoso mix (FY2026)": f"{mix_icc:.4f}",
    "CAC floor (FY2023 vs FY2026)": f"{cac_floor:.2f}",
    "planning ICC, CAC": f"{PLANNING_ICC}, {PLANNING_CAC}",
    "design": f"{CHOSEN.name}, {CHOSEN.periods} periods of {PERIOD_MONTHS} months",
    "enrollment months": f"{chosen_duration}",
    "index discharges": f"{total_discharges:,.0f}",
    "type I error": f"{chosen.power[0]:.3f}",
    "MDE at 80% power": pp(chosen_mde),
    "power at 2 pp": f"{power_at[0.02]:.0%}",
    "power at 3 pp": f"{power_at[0.03]:.0%}",
    "MDE range over ICC and CAC grid": (
        f"{pp(chosen_grid.min().min())} to {pp(chosen_grid.max().max())}"
    ),
    "patient-level MDE, same months": pp(individual_mde),
}
for key, value in summary.items():
    print(f"{key}: {value}")
print()
print(comparison_wide.applymap(pp).to_string())
print()
print(grid_wide.applymap(pp).to_string())
