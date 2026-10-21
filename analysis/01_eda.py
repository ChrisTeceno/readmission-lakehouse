# Databricks notebook source
# MAGIC %md
# MAGIC # 01 EDA: HRRP readmission ratios, penalties and Contoso
# MAGIC
# MAGIC Exploratory analysis of the Gold tables in `workspace.readmission`, run after the
# MAGIC pre-registered plan in `docs/analysis-plan.md` was committed. Section 5 of the plan fixes
# MAGIC the twelve descriptive hypotheses tested here (D1 to D12), each test, its effect size and
# MAGIC practical threshold, and Benjamini-Hochberg correction across the family at q = 0.05.
# MAGIC
# MAGIC To keep the causal analysis (task 2.3) blind, this notebook never plots or tabulates ERR
# MAGIC change against the prior window's ERR near the cutoff, and never splits ERR change by
# MAGIC `above_cutoff`.
# MAGIC
# MAGIC Sections:
# MAGIC 1. ERR by condition and year
# MAGIC 2. Penalty rate over time
# MAGIC 3. Hospital size, ownership and county socioeconomics
# MAGIC 4. Anomaly flag review
# MAGIC 5. Contoso against its Ohio peers
# MAGIC 6. The hypothesis family, corrected
# MAGIC 7. What the data can and cannot answer
# MAGIC
# MAGIC Runs top to bottom on serverless; the HTML export is in `docs/eda/`.

# COMMAND ----------

import math

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from pyspark.sql import functions as F
from scipy import stats

CATALOG = "workspace"
SCHEMA = "readmission"
ALPHA = 0.05
FDR_Q = 0.05
CROSS_SECTION_YEAR = 2026
EXACT_YEARS = range(2013, 2019)
MEASURES = ["AMI", "HF", "PN", "COPD", "HIP_KNEE", "CABG"]
# FY2022 to FY2025 windows are shortened by or include the COVID period (docs/gold.md).
COVID_YEARS = range(2022, 2026)

# One palette for every chart: the main series in blue, a highlight in orange, a third series
# in aqua, context in gray. Text never takes a series color.
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


def table(name: str):
    return spark.table(f"{CATALOG}.{SCHEMA}.{name}")  # noqa: F821 (Databricks global)


def show(fig) -> None:
    fig.tight_layout()
    plt.show()
    plt.close(fig)


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


# COMMAND ----------

facts = (
    table("gold_facility_condition_year")
    .select(
        "facility_id",
        "measure",
        "fiscal_year",
        "state",
        "hospital_ownership",
        "discharges",
        "excess_readmission_ratio",
        "cutoff_is_approximate",
        "above_cutoff",
        "err_change_robust_z",
        "discharge_ratio_vs_median",
        "anomaly_flag",
        "anomaly_reason",
    )
    .toPandas()
)
mart = table("gold_penalty_mart").toPandas()
acs = (
    table("gold_features")
    .where(F.col("target_fiscal_year") == CROSS_SECTION_YEAR)
    .groupBy("facility_id")
    .agg(
        F.first("acs_vintage").alias("acs_vintage"),
        *[
            F.first(c).alias(c)
            for c in [
                "acs_median_household_income",
                "acs_pct_below_poverty",
                "acs_pct_uninsured",
                "acs_pct_age_65_plus",
                "acs_pct_disability",
            ]
        ],
    )
    .toPandas()
)
scorecard = table("gold_contoso_scorecard").toPandas()

facts["err"] = facts["excess_readmission_ratio"]
facts["measure"] = pd.Categorical(facts["measure"], MEASURES, ordered=True)
print(f"gold_facility_condition_year: {len(facts):,} rows, {facts['err'].notna().sum():,} with ERR")
print(f"gold_penalty_mart: {len(mart):,} rows; gold_contoso_scorecard: {len(scorecard):,} rows")
print(
    f"ACS rows for FY{CROSS_SECTION_YEAR}: {len(acs):,} hospitals, vintage {int(acs['acs_vintage'].max())}"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. ERR by condition and year
# MAGIC
# MAGIC The excess readmission ratio (ERR) is CMS's risk-adjusted predicted over expected 30-day
# MAGIC readmissions for a hospital and condition over a three-year window. 1.0 is the national
# MAGIC expectation; above the cutoff (1.0 until FY2018) the condition counts toward the penalty.

# COMMAND ----------

results = []


def record(hid, hypothesis, test, p, effect_name, effect, threshold, n):
    results.append(
        {
            "id": hid,
            "hypothesis": hypothesis,
            "test": test,
            "n": int(n),
            "p": float(p),
            "effect": effect_name,
            "value": float(effect),
            "threshold": threshold,
            "clears_threshold": abs(effect) >= threshold,
        }
    )


cross = facts[(facts["fiscal_year"] == CROSS_SECTION_YEAR) & facts["err"].notna()]
groups = [g["err"].to_numpy() for _, g in cross.groupby("measure", observed=True)]
h, p = stats.kruskal(*groups)
eps2 = h / (len(cross) - 1)
record(
    "D1",
    "ERR differs across conditions",
    "Kruskal-Wallis",
    p,
    "epsilon squared",
    eps2,
    0.01,
    len(cross),
)

spread = cross.groupby("measure", observed=True)["err"].describe(percentiles=[0.1, 0.5, 0.9])
spread["p90_minus_p10"] = spread["90%"] - spread["10%"]
widest = spread["p90_minus_p10"].idxmax()
narrowest = spread["p90_minus_p10"].idxmin()

fig, ax = plt.subplots()
ax.boxplot(
    groups,
    labels=list(spread.index),
    vert=False,
    whis=(5, 95),
    showfliers=False,
    widths=0.5,
    patch_artist=True,
    boxprops={"facecolor": BLUE, "edgecolor": BLUE, "alpha": 0.85},
    medianprops={"color": "white", "linewidth": 2},
    whiskerprops={"color": BLUE},
    capprops={"color": BLUE},
)
ax.axvline(1.0, color=MUTED, linestyle="--", linewidth=1)
ax.grid(axis="x", color=LIGHT_GRAY)
ax.grid(axis="y", visible=False)
ax.set_xlabel(
    f"Excess readmission ratio, FY{CROSS_SECTION_YEAR}; dashed: 1.0 (box: quartiles, whiskers: "
    "5th to 95th percentile)"
)
ax.set_title(
    f"{widest} ratios spread {spread.loc[widest, 'p90_minus_p10'] / spread.loc[narrowest, 'p90_minus_p10']:.1f}x "
    f"as wide as {narrowest}; every condition centers near 1.0"
)
show(fig)
spread.round(3)

# COMMAND ----------

by_year = (
    facts[facts["err"].notna()]
    .groupby(["measure", "fiscal_year"], observed=True)["err"]
    .quantile([0.1, 0.5, 0.9])
    .unstack()
    .reset_index()
)

fig, axes = plt.subplots(2, 3, figsize=(11, 5.5), sharex=True, sharey=True)
for ax, measure in zip(axes.flat, MEASURES):
    d = by_year[by_year["measure"] == measure]
    ax.axvspan(2018.5, 2026.5, color=LIGHT_GRAY, alpha=0.5, linewidth=0)
    ax.fill_between(d["fiscal_year"], d[0.1], d[0.9], color=BLUE, alpha=0.2, linewidth=0)
    ax.plot(d["fiscal_year"], d[0.5], color=BLUE, marker="o", markersize=3)
    ax.axhline(1.0, color=MUTED, linestyle="--", linewidth=1)
    ax.set_title(measure, fontsize=10)
    ax.set_xticks([2013, 2017, 2021, 2025])
for ax in axes[:, 0]:
    ax.set_ylabel("ERR")
for ax in axes[1]:
    ax.set_xlabel("Fiscal year")
fig.suptitle(
    "The median ERR sits at 1.0 in every year and condition; hip/knee and CABG spread widest",
    x=0.01,
    ha="left",
    fontweight="bold",
    fontsize=12,
)
fig.text(
    0.01,
    0.005,
    "Line: median ERR. Band: 10th to 90th percentile. Gray: FY2019 to FY2026, where the real "
    "cutoff was a peer group median and 1.0 is a stand-in.",
    color=MUTED,
    fontsize=9,
)
fig.tight_layout(rect=(0, 0.03, 1, 0.95))
plt.show()
plt.close(fig)

# COMMAND ----------

# MAGIC %md
# MAGIC CMS standardizes the ERR so the national expectation is 1.0 each year, which is why the
# MAGIC medians hardly move: the ERR is a relative measure and cannot show a national decline in
# MAGIC readmissions. That limits what any trend in this notebook can mean.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Penalty rate over time
# MAGIC
# MAGIC Hospital-level: a hospital is penalized when any condition is above the cutoff. The number of
# MAGIC conditions grew from three (FY2013, FY2014) to five (FY2015) and six (FY2017), so the
# MAGIC hospital-level share rises partly by construction. D2 tests the trend on the three
# MAGIC original conditions in the exact-cutoff years only.

# COMMAND ----------

hospital_rate = (
    mart[mart["conditions_reported"] > 0]
    .groupby("fiscal_year")
    .agg(
        penalized=("penalized", "mean"),
        hospitals=("facility_id", "count"),
        approximate=("cutoff_is_approximate", "max"),
    )
    .reset_index()
)
original = facts[facts["measure"].isin(["AMI", "HF", "PN"]) & facts["err"].notna()].copy()
original_rate = (
    original.groupby(["fiscal_year", "measure"], observed=True)["above_cutoff"].mean().unstack()
)

exact = original[original["fiscal_year"].isin(EXACT_YEARS)].copy()
exact["y"] = exact["above_cutoff"].astype(int)
exact["year"] = exact["fiscal_year"] - 2013
design = pd.get_dummies(exact["measure"].astype(str), drop_first=True, dtype=float)
design.insert(0, "year", exact["year"].astype(float))
design = sm.add_constant(design)
logit = sm.Logit(exact["y"], design).fit(
    disp=False, cov_type="cluster", cov_kwds={"groups": pd.factorize(exact["facility_id"])[0]}
)
ame = logit.get_margeff(at="overall").summary_frame().loc["year", "dy/dx"]
record(
    "D2",
    "Share of AMI, HF, PN above cutoff trends over FY2013 to FY2018",
    "Logistic regression on year, facility-clustered",
    logit.pvalues["year"],
    "points per year",
    100 * ame,
    1.0,
    len(exact),
)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
exact_rate = hospital_rate[~hospital_rate["approximate"]]
approx_rate = hospital_rate[hospital_rate["approximate"]]
ax1.plot(hospital_rate["fiscal_year"], hospital_rate["penalized"], color=BLUE, linewidth=1)
ax1.plot(exact_rate["fiscal_year"], exact_rate["penalized"], "o", color=BLUE, markersize=6)
ax1.plot(
    approx_rate["fiscal_year"],
    approx_rate["penalized"],
    "o",
    color=BLUE,
    markersize=6,
    markerfacecolor="white",
    label="approximate cutoff (1.0 stand-in)",
)
ax1.set_title("Hospitals with any condition above the cutoff", fontsize=10)
ax1.set_xlabel("Fiscal year")
ax1.set_ylabel("Share")
ax1.set_ylim(0.7, 0.95)
ax1.set_yticks(np.arange(0.7, 0.951, 0.05))
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
ax1.legend(loc="lower right", fontsize=9)
for measure, color in zip(["AMI", "HF", "PN"], [BLUE, ORANGE, AQUA]):
    ax2.plot(
        original_rate.index,
        original_rate[measure],
        color=color,
        marker="o",
        markersize=3,
        label=measure,
    )
ax2.axvspan(2018.5, 2027.5, color=LIGHT_GRAY, alpha=0.5, linewidth=0)
ax2.set_title("Condition rows above the cutoff, three original conditions", fontsize=10)
ax2.set_xlabel("Fiscal year")
ax2.set_ylim(0.3, 0.7)
ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
ax2.legend(loc="lower left", fontsize=9, ncol=3)
by_year_rate = hospital_rate.set_index("fiscal_year")["penalized"]
condition_share = exact["y"].mean()
fig.suptitle(
    f"Penalized hospitals jumped from {pct(by_year_rate[2014])} to {pct(by_year_rate[2015])} when "
    f"CMS added conditions; per condition, {pct(condition_share)} sit above 1.0",
    x=0.01,
    ha="left",
    fontweight="bold",
    fontsize=12,
)
fig.tight_layout(rect=(0, 0, 1, 0.94))
plt.show()
plt.close(fig)
hospital_rate.assign(penalized=lambda d: d["penalized"].round(3))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Hospital size, ownership and county socioeconomics
# MAGIC
# MAGIC Size is the condition's eligible discharges (Hospital General Information has no bed count
# MAGIC here). CMS's hierarchical model shrinks low-volume hospitals toward 1.0, so small
# MAGIC hospitals should show a narrower ERR spread (D4) whatever their quality.

# COMMAND ----------

sized = cross[cross["discharges"].notna()].copy()
rho, p = stats.spearmanr(np.log(sized["discharges"]), sized["err"])
record("D3", "ERR associated with volume", "Spearman", p, "rho", rho, 0.10, len(sized))

# Terciles within measure, and ERR centered on its measure median, so the measures' different
# spreads do not stand in for size.
sized["tercile"] = sized.groupby("measure", observed=True)["discharges"].transform(
    lambda s: pd.qcut(s.rank(method="first"), 3, labels=["small", "medium", "large"])
)
sized["centered"] = sized["err"] - sized.groupby("measure", observed=True)["err"].transform(
    "median"
)
tercile_groups = [g["centered"].to_numpy() for _, g in sized.groupby("tercile", observed=True)]
_, p = stats.levene(*tercile_groups, center="median")
iqrs = sized.groupby("tercile", observed=True)["centered"].agg(
    lambda s: s.quantile(0.75) - s.quantile(0.25)
)
record(
    "D4",
    "ERR spread differs by volume tercile",
    "Brown-Forsythe",
    p,
    "IQR ratio, largest to smallest",
    iqrs.max() / iqrs.min(),
    1.25,
    len(sized),
)

sized["decile"] = sized.groupby("measure", observed=True)["discharges"].transform(
    lambda s: pd.qcut(s.rank(method="first"), 10, labels=False) + 1
)
by_decile = sized.groupby("decile")["centered"].quantile([0.1, 0.5, 0.9]).unstack()

fig, ax = plt.subplots()
ax.fill_between(
    by_decile.index,
    by_decile[0.1],
    by_decile[0.9],
    color=BLUE,
    alpha=0.2,
    linewidth=0,
    label="10th to 90th percentile",
)
ax.plot(by_decile.index, by_decile[0.5], color=BLUE, marker="o", markersize=4, label="median")
ax.axhline(0, color=MUTED, linestyle="--", linewidth=1)
ax.set_xticks(range(1, 11))
ax.set_xlabel(f"Discharge decile within condition, FY{CROSS_SECTION_YEAR} (1 = fewest)")
ax.set_ylabel("ERR minus condition median")
ax.legend(loc="upper left", fontsize=9)
ratio = (by_decile.loc[10, 0.9] - by_decile.loc[10, 0.1]) / (
    by_decile.loc[1, 0.9] - by_decile.loc[1, 0.1]
)
ax.set_title(
    f"ERR spread grows with volume: the largest decile spreads {ratio:.1f}x as wide as the smallest"
)
show(fig)
iqrs.round(4)

# COMMAND ----------


# Ownership: CMS's twelve categories collapse to four, so no group is a handful of hospitals.
def ownership_group(value: str | None) -> str:
    if value is None:
        return "Unknown"
    if value.startswith("Voluntary"):
        return "Non-profit"
    if value.startswith("Proprietary"):
        return "For-profit"
    if value.startswith("Government"):
        return "Government"
    return "Physician, tribal or other"


hospitals = mart[(mart["fiscal_year"] == CROSS_SECTION_YEAR) & mart["mean_err"].notna()].merge(
    facts[["facility_id", "hospital_ownership"]].drop_duplicates("facility_id"),
    on="facility_id",
    how="left",
)
hospitals["ownership"] = hospitals["hospital_ownership"].map(ownership_group)
owned = hospitals[hospitals["ownership"] != "Unknown"]
own_groups = {k: g["mean_err"].to_numpy() for k, g in owned.groupby("ownership")}
h, p = stats.kruskal(*own_groups.values())
record(
    "D5",
    "Hospital mean ERR differs by ownership",
    "Kruskal-Wallis",
    p,
    "epsilon squared",
    h / (len(owned) - 1),
    0.01,
    len(owned),
)

summary = (
    owned.groupby("ownership")["mean_err"]
    .agg(n="count", median="median", q1=lambda s: s.quantile(0.25), q3=lambda s: s.quantile(0.75))
    .sort_values("median")
)
fig, ax = plt.subplots(figsize=(9, 3.2))
y = np.arange(len(summary))
ax.hlines(y, summary["q1"], summary["q3"], color=BLUE, linewidth=6, alpha=0.35)
ax.plot(summary["median"], y, "o", color=BLUE, markersize=8)
for i, (name, row) in enumerate(summary.iterrows()):
    ax.text(row["q3"] + 0.002, i, f"n = {int(row['n']):,}", color=MUTED, fontsize=9, va="center")
ax.set_yticks(y, summary.index)
ax.axvline(1.0, color=MUTED, linestyle="--", linewidth=1)
ax.grid(axis="x", color=LIGHT_GRAY)
ax.grid(axis="y", visible=False)
ax.set_xlabel(f"Hospital mean ERR, FY{CROSS_SECTION_YEAR} (dot: median, bar: quartiles)")
gap = summary.loc["For-profit", "median"] - summary.loc["Non-profit", "median"]
ax.set_title(
    f"For-profit hospitals' median ERR is {gap:.3f} {'above' if gap > 0 else 'below'} "
    "non-profits'; the small physician and tribal group sits lowest"
)
show(fig)

# COMMAND ----------

ACS_LABELS = {
    "acs_median_household_income": ("D6", "County median household income"),
    "acs_pct_below_poverty": ("D7", "County share below poverty"),
    "acs_pct_uninsured": ("D8", "County share uninsured"),
    "acs_pct_age_65_plus": ("D9", "County share aged 65 and over"),
    "acs_pct_disability": ("D10", "County share with a disability"),
}
with_acs = hospitals.merge(acs, on="facility_id", how="inner")
rows = []
for column, (hid, label) in ACS_LABELS.items():
    d = with_acs[["mean_err", column]].dropna()
    rho, p = stats.spearmanr(d[column], d["mean_err"])
    # Fisher z interval for a Spearman rho, with the Bonett-Wright variance adjustment.
    se = math.sqrt((1 + rho**2 / 2) / (len(d) - 3))
    lo, hi = np.tanh(np.arctanh(rho) + np.array([-1.96, 1.96]) * se)
    rows.append({"label": label, "rho": rho, "lo": lo, "hi": hi, "n": len(d)})
    record(
        hid,
        f"Hospital mean ERR associated with {label.lower()}",
        "Spearman",
        p,
        "rho",
        rho,
        0.10,
        len(d),
    )
acs_rho = pd.DataFrame(rows).sort_values("rho")

fig, ax = plt.subplots(figsize=(9, 3.4))
y = np.arange(len(acs_rho))
ax.axvspan(-0.10, 0.10, color=LIGHT_GRAY, alpha=0.6, linewidth=0)
ax.hlines(y, acs_rho["lo"], acs_rho["hi"], color=BLUE, linewidth=2)
ax.plot(acs_rho["rho"], y, "o", color=BLUE, markersize=8)
ax.set_yticks(y, acs_rho["label"])
ax.axvline(0, color=MUTED, linewidth=1)
ax.grid(axis="x", color=LIGHT_GRAY)
ax.grid(axis="y", visible=False)
ax.set_xlabel(
    f"Spearman rho with hospital mean ERR, FY{CROSS_SECTION_YEAR}, 95% interval; gray: below "
    f"the 0.10 threshold; ACS vintage {int(acs['acs_vintage'].max())}"
)
strongest = acs_rho.iloc[acs_rho["rho"].abs().argmax()]
ax.set_title(
    f"No county measure clears |rho| 0.10; the closest is {strongest['label'].lower()} "
    f"({strongest['rho']:.3f})"
    if (acs_rho["rho"].abs() < 0.10).all()
    else f"{strongest['label']} tracks ERR most closely (rho {strongest['rho']:.3f})"
)
show(fig)
acs_rho.round(3)

# COMMAND ----------

# MAGIC %md
# MAGIC These are county-level (ecological) associations with a hospital average across conditions.
# MAGIC They describe where higher-ERR hospitals sit, not why, and say nothing about patients.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Anomaly flag review
# MAGIC
# MAGIC Gold flags a row when its ERR change from the prior non-overlapping window is extreme for its
# MAGIC measure and year (robust z beyond 3.5), or when its discharges per month moved more than
# MAGIC double or under half the year's median move (`docs/gold.md`). The review below pools all
# MAGIC ERR levels, so it says nothing about the cutoff.

# COMMAND ----------

windowed = facts[facts["fiscal_year"] >= 2016].copy()
windowed["covid"] = windowed["fiscal_year"].isin(COVID_YEARS)
windowed["err_rule"] = windowed["anomaly_reason"].fillna("").str.contains("err_change")
windowed["discharge_rule"] = windowed["anomaly_reason"].fillna("").str.contains("discharge_jump")
table_ = pd.crosstab(windowed["covid"], windowed["anomaly_flag"])
chi2, p, _, _ = stats.chi2_contingency(table_)
rates = windowed.groupby("covid")["anomaly_flag"].mean()
record(
    "D12",
    "Anomaly rate differs in COVID-affected years",
    "Chi-squared",
    p,
    "difference in rate, points",
    100 * (rates[True] - rates[False]),
    1.0,
    len(windowed),
)

by_fy = windowed.groupby("fiscal_year")[["err_rule", "discharge_rule"]].mean()
fig, ax = plt.subplots()
ax.axvspan(min(COVID_YEARS) - 0.5, max(COVID_YEARS) + 0.5, color=LIGHT_GRAY, alpha=0.6, linewidth=0)
ax.plot(
    by_fy.index,
    by_fy["discharge_rule"],
    color=BLUE,
    marker="o",
    markersize=4,
    label="discharge jump",
)
ax.plot(
    by_fy.index,
    by_fy["err_rule"],
    color=ORANGE,
    marker="o",
    markersize=4,
    label="ERR change, robust z beyond 3.5",
)
for col, name in [("discharge_rule", "discharge jump"), ("err_rule", "ERR change")]:
    ax.text(by_fy.index[-1] + 0.2, by_fy[col].iloc[-1], name, color=MUTED, fontsize=9, va="center")
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.1%}"))
ax.set_xlabel("Fiscal year (gray: COVID-affected windows, FY2022 to FY2025)")
ax.set_ylabel("Share of rows flagged")
ax.legend(loc="upper left", fontsize=9)
ax.set_title(
    f"Discharge jumps drive the flags; {pct(rates[True])} of COVID-era rows are flagged "
    f"against {pct(rates[False])} in other years"
)
show(fig)

flag_mix = (
    windowed[windowed["anomaly_flag"]]
    .assign(both=lambda d: d["err_rule"] & d["discharge_rule"])
    .agg({"err_rule": "sum", "discharge_rule": "sum", "both": "sum"})
)
print(f"Flagged rows FY2016 to FY2027: {int(windowed['anomaly_flag'].sum()):,}; by rule:")
print(flag_mix.to_string())

# COMMAND ----------

# The most extreme ERR moves, for a manual look: a hospital whose ERR moved by more than half
# a point between windows usually has very few discharges, or a CCN that changed hands.
extreme = (
    facts[facts["anomaly_reason"].fillna("").str.contains("err_change")]
    .assign(abs_z=lambda d: d["err_change_robust_z"].abs())
    .sort_values("abs_z", ascending=False)
    .head(15)[
        [
            "facility_id",
            "state",
            "measure",
            "fiscal_year",
            "discharges",
            "err",
            "err_change_robust_z",
            "anomaly_reason",
        ]
    ]
)
repeat = extreme["facility_id"].value_counts()
print(
    f"{repeat.iloc[0]} of these 15 rows are facility {repeat.index[0]}: one hospital whose ratios "
    "swing between windows, worth a manual check before it enters a model or the RDD sample."
)
extreme.round(3)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Contoso against its Ohio peers
# MAGIC
# MAGIC Contoso's 12 facilities are real Ohio hospitals' CCNs under invented names
# MAGIC (`docs/data-sources.md`), so this compares those hospitals' CMS results with the rest of Ohio.

# COMMAND ----------

ohio = hospitals[hospitals["state"] == "OH"]
contoso = ohio[ohio["is_contoso"]]
peers = ohio[~ohio["is_contoso"]]
_, p = stats.mannwhitneyu(contoso["mean_err"], peers["mean_err"], alternative="two-sided")
diff = contoso["mean_err"].median() - peers["mean_err"].median()
record(
    "D11",
    "Contoso mean ERR differs from other Ohio hospitals",
    "Mann-Whitney U",
    p,
    "difference in medians",
    diff,
    0.01,
    len(ohio),
)

fig, ax = plt.subplots(figsize=(9, 3))
rng = np.random.default_rng(7)
ax.plot(
    peers["mean_err"],
    rng.uniform(-0.25, 0.25, len(peers)),
    "o",
    color=GRAY,
    alpha=0.6,
    markersize=5,
    label=f"other Ohio hospitals (n = {len(peers)})",
)
ax.plot(
    contoso["mean_err"],
    rng.uniform(-0.25, 0.25, len(contoso)),
    "o",
    color=ORANGE,
    markersize=8,
    markeredgecolor="white",
    label=f"Contoso (n = {len(contoso)})",
)
ax.axvline(peers["mean_err"].median(), color=GRAY, linewidth=1.5)
ax.axvline(contoso["mean_err"].median(), color=ORANGE, linewidth=1.5)
ax.set_yticks([])
ax.grid(axis="x", color=LIGHT_GRAY)
ax.grid(axis="y", visible=False)
ax.set_xlabel(f"Hospital mean ERR, FY{CROSS_SECTION_YEAR} (vertical lines: group medians)")
ax.legend(loc="upper center", fontsize=9, ncol=2, bbox_to_anchor=(0.5, -0.28))
ax.set_title(
    f"Contoso's median hospital sits {abs(diff):.3f} {'above' if diff > 0 else 'below'} "
    f"other Ohio hospitals (p = {p:.2f}, 12 hospitals)"
)
show(fig)

contoso_conditions = (
    scorecard[scorecard["fiscal_year"] == CROSS_SECTION_YEAR]
    .pivot_table(
        index="contoso_facility_name", columns="measure", values="excess_readmission_ratio"
    )
    .reindex(columns=MEASURES)
)
contoso_conditions.round(3)

# COMMAND ----------

above = scorecard[
    (scorecard["fiscal_year"] == CROSS_SECTION_YEAR) & scorecard["above_cutoff"].fillna(False)
]
drivers = above.groupby("measure").size().reindex(MEASURES, fill_value=0)
reporting = (
    scorecard[
        (scorecard["fiscal_year"] == CROSS_SECTION_YEAR)
        & scorecard["excess_readmission_ratio"].notna()
    ]
    .groupby("measure")
    .size()
    .reindex(MEASURES, fill_value=0)
)
fig, ax = plt.subplots(figsize=(9, 3.2))
ax.set_axisbelow(True)
ax.bar(drivers.index, drivers.values, color=ORANGE, width=0.6)
for x, v in zip(drivers.index, drivers.values):
    ax.text(x, v + 0.1, f"{v} of {reporting[x]}", ha="center", color=INK, fontsize=10)
ax.set_ylabel("Contoso facilities above 1.0")
ax.set_xlabel(
    f"Condition, FY{CROSS_SECTION_YEAR} (labels: above 1.0 of those with an ERR; 1.0 stands in "
    "for the peer median)"
)
ax.set_ylim(0, max(drivers.max() + 1.5, 3))
ax.set_title(
    f"{drivers.idxmax()} puts the most Contoso facilities over the cutoff "
    f"({drivers.max()} of the {reporting[drivers.idxmax()]} that report it) in "
    f"FY{CROSS_SECTION_YEAR}"
)
show(fig)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. The hypothesis family, corrected
# MAGIC
# MAGIC Benjamini-Hochberg across all twelve tests at q = 0.05, as pre-registered. A result is
# MAGIC called meaningful only when it survives the correction and its effect size clears the
# MAGIC threshold fixed in the plan. With thousands of rows most tests reject; the effect size column
# MAGIC is the one to read.

# COMMAND ----------


def benjamini_hochberg(p: pd.Series) -> pd.Series:
    order = p.sort_values()
    m = len(p)
    adjusted = (order * m / np.arange(1, m + 1))[::-1].cummin()[::-1].clip(upper=1)
    return adjusted.reindex(p.index)


family = pd.DataFrame(results)
family["id_num"] = family["id"].str[1:].astype(int)
family = family.sort_values("id_num").drop(columns="id_num").set_index("id")
family["p_bh"] = benjamini_hochberg(family["p"])
family["rejects_at_q"] = family["p_bh"] < FDR_Q
family["meaningful"] = family["rejects_at_q"] & family["clears_threshold"]
assert len(family) == 12, f"expected the 12 pre-registered tests, got {len(family)}"
pd.set_option("display.max_colwidth", 80)
family[
    [
        "hypothesis",
        "test",
        "n",
        "effect",
        "value",
        "threshold",
        "p",
        "p_bh",
        "rejects_at_q",
        "meaningful",
    ]
].round({"value": 4, "p": 6, "p_bh": 6})

# COMMAND ----------

for hid, row in family.iterrows():
    verdict = (
        "meaningful"
        if row["meaningful"]
        else "rejects but below the practical threshold"
        if row["rejects_at_q"]
        else "not distinguishable from no difference"
    )
    print(
        f"{hid}: {row['hypothesis']}: {row['effect']} {row['value']:.4f}, "
        f"BH p {row['p_bh']:.3g}, {verdict}"
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. What the data can and cannot answer
# MAGIC
# MAGIC The same section, with this run's numbers, is `docs/eda/what-the-data-can-answer.md` for
# MAGIC the readout.
# MAGIC
# MAGIC **Can answer**
# MAGIC - Which conditions and facilities sit above the penalty cutoff, every year from FY2013 to
# MAGIC   FY2027, for every hospital CMS reports (about 3,000 to 3,500 a year).
# MAGIC - How a hospital's ERR compares with its condition's national distribution and with its Ohio
# MAGIC   peers, including Contoso's 12 facilities.
# MAGIC - Whether ERR is associated with volume, ownership and county socioeconomics, as
# MAGIC   descriptive, county-level associations.
# MAGIC - With the RDD in task 2.3, whether crossing the 1.0 cutoff in FY2013 to FY2018 changed a
# MAGIC   condition's ERR three years later, locally at the cutoff.
# MAGIC
# MAGIC **Cannot answer**
# MAGIC - Whether national readmissions fell: the ERR is standardized to 1.0 each year, so it ranks
# MAGIC   hospitals against each other and cannot show a level shift.
# MAGIC - The exact penalty status in FY2019 to FY2026: CMS used dual-eligibility peer group medians
# MAGIC   that no file here carries; 1.0 is a stand-in that disagrees with the real cutoff for about
# MAGIC   6 percent of rows (measured in FY2027, where both are known).
# MAGIC - Penalty dollars: the payment reduction also depends on each hospital's base DRG payments,
# MAGIC   which this project does not load.
# MAGIC - Why a hospital's ERR is high: there are no patient-level data, no staffing or process
# MAGIC   measures, and the ACS links are county averages.
# MAGIC - Anything about Contoso's operations from CMS data: the 12 CCNs are real hospitals relabeled,
# MAGIC   and Contoso's operational records are synthetic and cover only FY2026 and FY2027 windows.
# MAGIC - Hospital attributes as of past years: ownership and type are today's snapshot.
# MAGIC - The effect of the penalty away from the cutoff, or on hospitals that are far above it.
