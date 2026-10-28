# Databricks notebook source
# MAGIC %md
# MAGIC # 02 Causal: does the HRRP penalty change hospital behavior?
# MAGIC
# MAGIC The pre-registered causal analysis of `docs/analysis-plan.md` (section 4), run on the Gold
# MAGIC tables in `workspace.readmission`. The plan was committed before any outcome was examined;
# MAGIC its numbers (cutoff years, bandwidth rule and multiples, placebo cutoffs, donut widths, the
# MAGIC smallest effect of interest) are the constants in the next cells, and anything this notebook
# MAGIC does differently is listed under Deviations from plan at the end.
# MAGIC
# MAGIC Design: a sharp regression discontinuity at the penalty cutoff. For a hospital-condition
# MAGIC whose excess readmission ratio (ERR) in fiscal year t is just above the cutoff (1.0 to
# MAGIC FY2018, the peer group median from FY2019), how much does its ERR change by the next
# MAGIC non-overlapping window, compared with one just below?
# MAGIC
# MAGIC The primary sample is the one the plan pre-registered: FY2013 to FY2018, when those were the
# MAGIC only years with an exact cutoff. The FY2019 to FY2026 peer medians were loaded after the
# MAGIC first run, so a secondary sample of every year t with an exact cutoff and an outcome (FY2013
# MAGIC to FY2023 and FY2025) is run beside it in section 4 (Deviations, item 7).
# MAGIC
# MAGIC Sections:
# MAGIC 1. Sample
# MAGIC 2. Validity checks: density, covariate balance, placebo cutoffs
# MAGIC 3. Primary estimate
# MAGIC 4. Sensitivity
# MAGIC 5. Difference-in-differences check
# MAGIC 6. Why this is not regression to the mean
# MAGIC 7. Result, in the plan's terms
# MAGIC 8. Supplementary, not pre-registered: first-time penalized hospitals (event study) and
# MAGIC    outcome availability at the cutoff
# MAGIC 9. Deviations from plan
# MAGIC
# MAGIC Estimator: `rdrobust` and `rddensity` (Calonico, Cattaneo, Farrell and Titiunik), installed
# MAGIC in the notebook environment below. They installed on serverless, so the hand-rolled fallback
# MAGIC the plan allows was not needed. Runs top to bottom on serverless; the HTML export is in
# MAGIC `docs/causal/`.

# COMMAND ----------

# MAGIC %pip install rdrobust==2.1.1 rddensity==3.0 lpdensity==3.0.0 "numpy>=1.26,<2"

# COMMAND ----------

dbutils.library.restartPython()  # noqa: F821 (Databricks global)

# COMMAND ----------

import contextlib
import importlib.metadata
import io
import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from rddensity import rddensity
from rdrobust import rdrobust
from scipy import stats

CATALOG = "workspace"
SCHEMA = "readmission"
MEASURES = ["AMI", "HF", "PN", "COPD", "HIP_KNEE", "CABG"]

# docs/analysis-plan.md, sections 3 to 6. SECONDARY_YEARS is not in the plan (Deviations, item
# 7): every year from FY2013 has had an exact cutoff since the FY2019 to FY2026 peer medians were
# loaded, and FY2024 and FY2026 have no outcome window yet, so they drop out on their own.
PRIMARY_YEARS = range(2013, 2019)
SECONDARY_YEARS = range(2013, 2027)
DID_YEARS = range(2016, 2019)
CUTOFF = 1.0
BANDWIDTH_MULTIPLES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
PLACEBO_BELOW = [0.90, 0.95]
PLACEBO_ABOVE = [1.05, 1.10]
DONUT = 0.005
DID_WINDOW = 0.10
SMALLEST_EFFECT = 0.02
MDE_FACTOR = 2.80
ALPHA = 0.05
FDR_Q = 0.05

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

print(
    {
        p: importlib.metadata.version(p)
        for p in ["rdrobust", "rddensity", "statsmodels", "numpy", "pandas"]
    }
)


def table(name: str):
    return spark.table(f"{CATALOG}.{SCHEMA}.{name}")  # noqa: F821 (Databricks global)


def show(fig) -> None:
    fig.tight_layout()
    plt.show()
    plt.close(fig)


def benjamini_hochberg(p: pd.Series) -> pd.Series:
    order = p.sort_values()
    m = len(p)
    adjusted = (order * m / np.arange(1, m + 1))[::-1].cummin()[::-1].clip(upper=1)
    return adjusted.reindex(p.index)


# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Sample
# MAGIC
# MAGIC One row per facility, measure and fiscal year t. The running variable is ERR(t) minus the
# MAGIC cutoff (1.0 to FY2018, the row's peer group median from FY2019). The outcome is the ERR
# MAGIC change from window t to the next
# MAGIC window that shares no discharges with it (year s, usually t+3): Gold's `err_change` on the
# MAGIC row whose `prior_window_fiscal_year` is t (plan, section 3.2). Rows without an ERR at t or s
# MAGIC drop out. Secondary outcome: ERR(t+4) minus ERR(t).

# COMMAND ----------

facts = (
    table("gold_facility_condition_year")
    .select(
        "facility_id",
        "measure",
        "fiscal_year",
        "discharges",
        "excess_readmission_ratio",
        "penalty_cutoff",
        "cutoff_is_approximate",
        "above_cutoff",
        "prior_window_fiscal_year",
        "prior_window_err",
        "err_change",
        "anomaly_flag",
    )
    .toPandas()
)
mart = (
    table("gold_penalty_mart")
    .select(
        "facility_id",
        "fiscal_year",
        "conditions_reported",
        "penalized",
        "worst_err",
        "prior_window_fiscal_year",
        "mean_err_change",
        "cutoff_is_approximate",
    )
    .toPandas()
)
acs = (
    table("gold_features")
    .select(
        "facility_id",
        "measure",
        "target_fiscal_year",
        "acs_pct_below_poverty",
        "acs_pct_age_65_plus",
    )
    .toPandas()
    .rename(columns={"target_fiscal_year": "fiscal_year"})
)
facts["err"] = facts["excess_readmission_ratio"]
facts["x"] = facts["err"] - facts["penalty_cutoff"]
print(f"gold_facility_condition_year: {len(facts):,} rows; gold_penalty_mart: {len(mart):,} rows")

# COMMAND ----------

KEY = ["facility_id", "measure"]
outcome = facts[facts["prior_window_fiscal_year"].notna() & facts["err_change"].notna()][
    [*KEY, "prior_window_fiscal_year", "fiscal_year", "err_change", "anomaly_flag"]
].rename(
    columns={
        "prior_window_fiscal_year": "t",
        "fiscal_year": "s",
        "err_change": "y",
        "anomaly_flag": "anomaly_s",
    }
)
outcome["t"] = outcome["t"].astype(int)
plus4 = facts[facts["err"].notna()][[*KEY, "fiscal_year", "err"]].rename(columns={"err": "err_t4"})
plus4["t"] = plus4["fiscal_year"] - 4

rows = (
    facts[facts["err"].notna()]
    .rename(columns={"fiscal_year": "t", "anomaly_flag": "anomaly_t", "err_change": "pre_change"})
    .merge(outcome, on=[*KEY, "t"], how="inner")
    .merge(plus4.drop(columns="fiscal_year"), on=[*KEY, "t"], how="left")
    .merge(acs.rename(columns={"fiscal_year": "t"}), on=[*KEY, "t"], how="left")
)
rows["y4"] = rows["err_t4"] - rows["err"]
rows["treated"] = rows["x"] > 0
rows["measure_year"] = rows["measure"] + "_" + rows["t"].astype(str)
assert (rows["treated"] == rows["above_cutoff"]).all(), "treatment must be Gold's above_cutoff"

primary = rows[rows["t"].isin(PRIMARY_YEARS)].copy()
assert not primary["cutoff_is_approximate"].any()
assert (primary["penalty_cutoff"] == CUTOFF).all()

# A FY2019 or later row whose hospital is missing from that year's supplemental file, or an
# FY2023 pneumonia row (CMS left pneumonia out of FY2023), has no published median and a 1.0
# stand-in; it is not exact, so the secondary sample leaves it out.
stand_in = rows["cutoff_is_approximate"]
secondary = rows[rows["t"].isin(SECONDARY_YEARS) & ~stand_in].copy()
print(f"Rows left out of the secondary sample for a 1.0 stand-in cutoff: {int(stand_in.sum()):,}")

# Gold counts a condition only when its ERR is strictly above the cutoff, so an ERR exactly at
# the cutoff is untreated. rdrobust treats x >= c as treated; evaluating at half the smallest
# positive running value puts the rows at exactly 0 on the untreated side and moves nothing else.
C0 = float(primary.loc[primary["x"] > 0, "x"].min()) / 2
print(
    f"Rows with ERR exactly at the cutoff (untreated): {(primary['x'] == 0).sum():,}; c = {C0:.2e}"
)

sample_counts = (
    rows.groupby("t")
    .agg(rows=("y", "size"), treated=("treated", "mean"), outcome_year=("s", "first"))
    .assign(
        role=lambda d: np.where(
            d.index.isin(PRIMARY_YEARS), "primary and secondary", "secondary (peer group median)"
        )
    )
)
print(
    f"Primary sample (t = 2013 to 2018): {len(primary):,} rows, "
    f"{primary['facility_id'].nunique():,} facilities"
)
print(
    f"Secondary sample (t = 2013 to 2023 and 2025): {len(secondary):,} rows, "
    f"{secondary['facility_id'].nunique():,} facilities"
)
sample_counts.round({"treated": 3})

# COMMAND ----------


def rd(
    d: pd.DataFrame,
    y: str = "y",
    c: float | None = None,
    covs: str | None = "measure_year",
    label: str = "",
    **kwargs,
) -> dict:
    """Local linear RDD on d[y] against d["x"] at c (C0 unless given): triangular kernel,
    common MSE-optimal bandwidth, facility-clustered errors, the indicators in `covs` as
    covariates. Returns the conventional estimate, the robust bias-corrected interval and p
    value, the bandwidths, effective observations each side and the MDE from the robust SE."""
    d = d[d[y].notna() & d["x"].notna()]
    c = C0 if c is None else c
    z = (
        pd.get_dummies(d[covs], drop_first=True, dtype=float).to_numpy()
        if covs and d[covs].nunique() > 1
        else None
    )
    # rdrobust prints a notice on every call with repeated running values (many hospitals share
    # an ERR); it is expected here, and the mass-point adjustment it announces stays on.
    with contextlib.redirect_stdout(io.StringIO()):
        fit = rdrobust(
            d[y].to_numpy(),
            d["x"].to_numpy(),
            c=c,
            covs=z,
            cluster=pd.factorize(d["facility_id"])[0],
            **{"kernel": "triangular", "bwselect": "mserd", **kwargs},
        )
    se = float(fit.se.loc["Robust"].iloc[0])
    lo, hi = (float(v) for v in fit.ci.loc["Robust"])
    return {
        "label": label,
        "estimate": float(fit.coef.loc["Conventional"].iloc[0]),
        "estimate_bc": float(fit.coef.loc["Robust"].iloc[0]),
        "robust_se": se,
        "ci_low": lo,
        "ci_high": hi,
        "p": float(fit.pv.loc["Robust"].iloc[0]),
        "h": float(fit.bws.loc["h", "left"]),
        "b": float(fit.bws.loc["b", "left"]),
        "n_left": int(fit.N_h[0]),
        "n_right": int(fit.N_h[1]),
        "n": len(d),
        "mde": MDE_FACTOR * se,
    }


def results_frame(results: list[dict]) -> pd.DataFrame:
    cols = ["estimate", "ci_low", "ci_high", "p", "h", "n_left", "n_right", "mde"]
    return (
        pd.DataFrame(results)
        .set_index("label")[cols]
        .round({"estimate": 4, "ci_low": 4, "ci_high": 4, "p": 4, "h": 4, "mde": 4})
    )


# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Validity checks
# MAGIC
# MAGIC Run before the estimate is reported (plan, section 4.3), and reported whatever they show.

# COMMAND ----------

# MAGIC %md
# MAGIC ### 2.1 Density at the cutoff
# MAGIC
# MAGIC Cattaneo, Jansson and Ma (2020). CMS computes the ERR from claims, so a hospital cannot place
# MAGIC itself just below 1.0 and no bunching is expected. Run on the analysis sample and, for
# MAGIC context, on every primary-year row with an ERR (with or without an outcome).

# COMMAND ----------


def density_test(x: np.ndarray, label: str) -> dict:
    fit = rddensity(x, c=C0)
    return {
        "label": label,
        "n_left": int(fit.n["eff_left"]),
        "n_right": int(fit.n["eff_right"]),
        "h_left": float(fit.h["left"]),
        "h_right": float(fit.h["right"]),
        "t": float(fit.test["t_jk"]),
        "p": float(fit.test["p_jk"]),
    }


all_primary_x = facts.loc[facts["fiscal_year"].isin(PRIMARY_YEARS) & facts["err"].notna(), "x"]
density = pd.DataFrame(
    [
        density_test(primary["x"].to_numpy(), "analysis sample"),
        density_test(all_primary_x.to_numpy(), "all primary-year rows with an ERR"),
    ]
).set_index("label")

bins = np.arange(-0.15, 0.15 + 1e-9, 0.005)
fig, ax = plt.subplots()
counts, edges = np.histogram(primary["x"], bins=bins + C0)
centers = (edges[:-1] + edges[1:]) / 2
ax.bar(
    centers,
    counts,
    width=0.0045,
    color=np.where(centers > C0, ORANGE, BLUE),
    alpha=0.85,
)
ax.axvline(C0, color=MUTED, linestyle="--", linewidth=1)
ax.set_xlabel("ERR(t) minus 1.0, FY2013 to FY2018 (orange: counts toward the penalty)")
ax.set_ylabel("Rows per 0.005 bin")
p_density = density.loc["analysis sample", "p"]
ax.set_title(
    f"No sign of sorting around the cutoff (density test p = {p_density:.2f})"
    if p_density >= ALPHA
    else f"The density test rejects at the cutoff (p = {p_density:.3f}); read the estimate with care"
)
show(fig)
density.round(4)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 2.2 Covariate balance
# MAGIC
# MAGIC The same RDD with things fixed before treatment as outcomes: discharges in year t, the ERR
# MAGIC change into window t (t-3 to t, available for t = 2016 to 2018), and the county poverty and
# MAGIC age 65+ shares (ACS vintage t-3, as in `gold_features`; FY2013 has none). A jump in any of
# MAGIC them would mean hospitals just above and just below the cutoff differ in something other
# MAGIC than penalty status. Benjamini-Hochberg across the four, q = 0.05.

# COMMAND ----------

balance = pd.DataFrame(
    [
        rd(primary, "discharges", label="discharges in year t"),
        rd(primary, "pre_change", label="ERR change t-3 to t"),
        rd(primary, "acs_pct_below_poverty", label="county share below poverty"),
        rd(primary, "acs_pct_age_65_plus", label="county share aged 65+"),
    ]
).set_index("label")
balance["p_bh"] = benjamini_hochberg(balance["p"])
balance["imbalanced"] = balance["p_bh"] < FDR_Q
balance[
    ["estimate", "ci_low", "ci_high", "p", "p_bh", "imbalanced", "h", "n_left", "n_right"]
].round(4)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 2.3 Placebo cutoffs
# MAGIC
# MAGIC The same specification at 0.10 and 0.05 below the cutoff using only rows not above it, and
# MAGIC at 0.05 and 0.10 above using only rows above it, so no placebo window straddles the real
# MAGIC cutoff (to FY2018, ERR = 0.90, 0.95, 1.05 and 1.10). Nothing
# MAGIC happens at those values, so a jump there would point at something in the data other than the
# MAGIC penalty. Benjamini-Hochberg across the four, q = 0.05.

# COMMAND ----------


def placebo_runs(d: pd.DataFrame) -> pd.DataFrame:
    runs = pd.DataFrame(
        [
            rd(d[~d["treated"]], c=v - CUTOFF, label=f"cutoff {v - CUTOFF:+.2f}")
            for v in PLACEBO_BELOW
        ]
        + [
            rd(d[d["treated"]], c=v - CUTOFF, label=f"cutoff {v - CUTOFF:+.2f}")
            for v in PLACEBO_ABOVE
        ]
    ).set_index("label")
    runs["p_bh"] = benjamini_hochberg(runs["p"])
    return runs


placebo = placebo_runs(primary)
placebo["survives_bh"] = placebo["p_bh"] < FDR_Q
placebo[
    ["estimate", "ci_low", "ci_high", "p", "p_bh", "survives_bh", "h", "n_left", "n_right"]
].round(4)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Primary estimate
# MAGIC
# MAGIC Sharp RDD, FY2013 to FY2018, pooled across measures and years with measure-by-year
# MAGIC indicators as covariates, local linear, triangular kernel, common MSE-optimal bandwidth
# MAGIC (`bwselect = "mserd"`), errors clustered by facility. The reported interval is the robust
# MAGIC bias-corrected 95 percent interval; the point estimate beside it is the conventional one.
# MAGIC One confirmatory test: H0, the jump is zero, two-sided at 0.05.

# COMMAND ----------


def tost_p(run: dict) -> float:
    # Equivalence against the smallest effect of interest: two one-sided tests on the
    # bias-corrected estimate and the robust standard error.
    z_low = (run["estimate_bc"] + SMALLEST_EFFECT) / run["robust_se"]
    z_high = (run["estimate_bc"] - SMALLEST_EFFECT) / run["robust_se"]
    return float(max(1 - stats.norm.cdf(z_low), stats.norm.cdf(z_high)))


main = rd(primary, label="primary")
H, B = main["h"], main["b"]
main["tost_p"] = tost_p(main)

print(
    f"Jump at the cutoff: {main['estimate']:+.4f} (robust 95% CI {main['ci_low']:+.4f} to "
    f"{main['ci_high']:+.4f}, robust p = {main['p']:.3f})"
)
print(
    f"Bandwidth h = {H:.4f} (bias b = {B:.4f}); effective rows {main['n_left']:,} below and "
    f"{main['n_right']:,} above; MDE at 80% power = {main['mde']:.4f}; equivalence (TOST, "
    f"+/-{SMALLEST_EFFECT}) p = {main['tost_p']:.3f}"
)

# COMMAND ----------

window = primary[primary["x"].abs() <= 0.15]
binned = (
    window.assign(bin=np.floor((window["x"] - C0) / 0.005) * 0.005 + 0.0025 + C0)
    .groupby("bin")
    .agg(y=("y", "mean"), n=("y", "size"))
    .reset_index()
)


def local_fit(side: pd.DataFrame, grid: np.ndarray) -> np.ndarray:
    w = np.clip(1 - (side["x"] - C0).abs() / H, 0, None)
    design = sm.add_constant(side["x"] - C0)
    fit = sm.WLS(side["y"], design, weights=w).fit()
    return fit.params["const"] + fit.params["x"] * (grid - C0)


fig, ax = plt.subplots()
below = binned["bin"] < C0
ax.scatter(
    binned.loc[below, "bin"], binned.loc[below, "y"], color=BLUE, s=18, label="not penalized"
)
ax.scatter(
    binned.loc[~below, "bin"], binned.loc[~below, "y"], color=ORANGE, s=18, label="penalized"
)
for side, color, grid in [
    (primary[(primary["x"] < C0) & (primary["x"] >= C0 - H)], BLUE, np.linspace(C0 - H, C0, 50)),
    (primary[(primary["x"] >= C0) & (primary["x"] <= C0 + H)], ORANGE, np.linspace(C0, C0 + H, 50)),
]:
    ax.plot(grid, local_fit(side, grid), color=color)
ax.axvline(C0, color=MUTED, linestyle="--", linewidth=1)
ax.axvspan(C0 - H, C0 + H, color=LIGHT_GRAY, alpha=0.4, linewidth=0)
ax.set_xlabel(
    "ERR(t) minus 1.0, FY2013 to FY2018 (dots: mean per 0.005 bin; lines: local linear fit "
    "inside the bandwidth, shaded)"
)
ax.set_ylabel("ERR change, window t to t+3")
ax.legend(loc="upper right", fontsize=9)
ax.set_title(
    f"High ratios fall back either way; the jump at the cutoff is {main['estimate']:+.4f} "
    f"(95% CI {main['ci_low']:+.3f} to {main['ci_high']:+.3f})"
)
show(fig)

# COMMAND ----------

# MAGIC %md
# MAGIC CMS shrinks low-volume hospitals toward an ERR of 1.0, so the rows near the cutoff
# MAGIC over-represent small hospitals and the estimate is local to them (plan, section 3.4).

# COMMAND ----------

inside = primary[(primary["x"] - C0).abs() <= H]
discharge_mix = pd.DataFrame(
    {
        "inside bandwidth": inside["discharges"].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]),
        "full primary sample": primary["discharges"].describe(
            percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]
        ),
    }
).T
discharge_mix.round(0)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Sensitivity
# MAGIC
# MAGIC Reported in full and never chosen among (plan, section 4.4). The bandwidth runs scale both
# MAGIC the estimation and the bias bandwidth of the primary run, so 1x reproduces it; the other
# MAGIC specifications choose their own MSE-optimal bandwidth.

# COMMAND ----------

by_bandwidth = pd.DataFrame(
    [rd(primary, h=k * H, b=k * B, label=f"{k:g}x") for k in BANDWIDTH_MULTIPLES]
).set_index("label")

fig, ax = plt.subplots()
k = np.array(BANDWIDTH_MULTIPLES)
ax.axhspan(-SMALLEST_EFFECT, SMALLEST_EFFECT, color=LIGHT_GRAY, alpha=0.6, linewidth=0)
ax.vlines(k, by_bandwidth["ci_low"], by_bandwidth["ci_high"], color=BLUE, linewidth=2)
ax.plot(k, by_bandwidth["estimate"], "o", color=BLUE, markersize=7)
ax.axhline(0, color=MUTED, linewidth=1)
ax.set_xticks(k, [f"{v:g}x\n{v * H:.3f}" for v in k])
ax.set_xlabel("Bandwidth, multiple of the MSE-optimal one and in ERR units (gray: within +/-0.02)")
ax.set_ylabel("Jump in ERR change")
ax.set_title("Estimate and robust 95% interval against bandwidth")
show(fig)
results_frame(by_bandwidth.reset_index().to_dict("records"))

# COMMAND ----------

no_anomaly = primary[~primary["anomaly_t"] & ~primary["anomaly_s"].astype(bool)]
specifications = [
    main,
    rd(primary, p=2, label="local quadratic"),
    rd(primary, kernel="uniform", label="uniform kernel"),
    rd(primary[(primary["x"] - C0).abs() >= DONUT], label=f"donut, |x| < {DONUT} dropped"),
    rd(no_anomaly, label="anomaly flag in t or s excluded"),
    rd(primary, "y4", label="outcome t+4 (secondary)"),
]
results_frame(specifications)

# COMMAND ----------

per_measure = pd.DataFrame(
    [
        rd(primary[primary["measure"] == m], covs="t", label=m)
        for m in MEASURES
        if (primary["measure"] == m).any()
    ]
).set_index("label")
per_measure["p_bh"] = benjamini_hochberg(per_measure["p"])

fig, ax = plt.subplots(figsize=(9, 3.6))
y = np.arange(len(per_measure))
ax.axvspan(-SMALLEST_EFFECT, SMALLEST_EFFECT, color=LIGHT_GRAY, alpha=0.6, linewidth=0)
ax.hlines(y, per_measure["ci_low"], per_measure["ci_high"], color=BLUE, linewidth=2)
ax.plot(per_measure["estimate"], y, "o", color=BLUE, markersize=7)
ax.plot([main["estimate"]], [len(y)], "D", color=ORANGE, markersize=7)
ax.hlines(len(y), main["ci_low"], main["ci_high"], color=ORANGE, linewidth=2)
ax.set_yticks([*y, len(y)], [*per_measure.index, "pooled (primary)"])
ax.axvline(0, color=MUTED, linewidth=1)
ax.grid(axis="x", color=LIGHT_GRAY)
ax.grid(axis="y", visible=False)
ax.set_xlabel("Jump in ERR change, robust 95% interval (gray: within +/-0.02)")
ax.set_title("Per condition (secondary, Benjamini-Hochberg across six)")
show(fig)
per_measure[["estimate", "ci_low", "ci_high", "p", "p_bh", "h", "n_left", "n_right"]].round(4)

# COMMAND ----------

# MAGIC %md
# MAGIC Secondary sample, not pre-registered (Deviations, item 7): every year t with an exact cutoff
# MAGIC and an outcome, FY2013 to FY2023 and FY2025, the running variable ERR(t) minus the row's own
# MAGIC cutoff (1.0 to FY2018, the peer group median from FY2019). Same specification and placebo
# MAGIC offsets as the primary run. Beside it, FY2019 onward alone, whose outcome windows run into
# MAGIC COVID.

# COMMAND ----------

secondary_main = rd(secondary, label="secondary, FY2013 to FY2023 and FY2025")
secondary_main["tost_p"] = tost_p(secondary_main)
secondary_placebo = placebo_runs(secondary)
secondary_runs = [
    secondary_main,
    rd(secondary[~secondary["t"].isin(PRIMARY_YEARS)], label="FY2019 onward alone"),
]
print(secondary_placebo[["estimate", "ci_low", "ci_high", "p", "p_bh"]].round(4))
results_frame(secondary_runs)

# COMMAND ----------

# MAGIC %md
# MAGIC Hospital level: a hospital is penalized at all when its worst ERR is above the cutoff, so
# MAGIC the running variable is the worst ERR minus 1.0 in year t and the outcome is the mean ERR
# MAGIC change across its conditions (`gold_penalty_mart.mean_err_change` of the year s row).

# COMMAND ----------

hospital = (
    mart[mart["fiscal_year"].isin(PRIMARY_YEARS) & mart["worst_err"].notna()][
        ["facility_id", "fiscal_year", "worst_err", "penalized"]
    ]
    .rename(columns={"fiscal_year": "t"})
    .merge(
        mart[mart["prior_window_fiscal_year"].notna()][
            ["facility_id", "prior_window_fiscal_year", "mean_err_change"]
        ].rename(columns={"prior_window_fiscal_year": "t", "mean_err_change": "y"}),
        on=["facility_id", "t"],
        how="inner",
    )
)
hospital["x"] = hospital["worst_err"] - CUTOFF
assert ((hospital["x"] > 0) == hospital["penalized"]).all()
hospital_run = rd(hospital, covs="t", label="hospital level (worst ERR)")
results_frame([hospital_run])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Difference-in-differences check
# MAGIC
# MAGIC A check from a different identifying assumption, not a second confirmatory test (plan,
# MAGIC section 4.5). For t = 2016 to 2018, each facility-measure has two windows that share no
# MAGIC discharges with window t: pre = t-3 and post = t+3. Window t itself is left out because
# MAGIC treatment is assigned on ERR(t), and comparing against it would build regression to the mean
# MAGIC into the difference. Treated: above 1.0 at t; control: not above. Samples: |ERR(t) - 1.0| at
# MAGIC most 0.10, and separately within the RDD bandwidth.
# MAGIC
# MAGIC Model: ERR on treated x post with facility-measure fixed effects and measure-by-year-by-post
# MAGIC fixed effects, errors clustered by facility. Each year t is its own two-window panel, so the
# MAGIC facility-measure effect is per t, and with two windows the model is exactly a regression of
# MAGIC ERR(t+3) - ERR(t-3) on treated and measure-by-year indicators; that is how it is computed.
# MAGIC Assumption: absent the penalty, hospitals just above and just below 1.0 would have moved by the
# MAGIC same amount from t-3 to t+3. No pre-period placebo is possible inside the exact years (t-6
# MAGIC predates FY2013).

# COMMAND ----------


def did(d: pd.DataFrame, label: str) -> dict:
    design = pd.get_dummies(d["measure_year"], drop_first=True, dtype=float)
    design.insert(0, "treated", d["treated"].astype(float))
    design = sm.add_constant(design)
    fit = sm.OLS(d["did_change"], design).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(d["facility_id"])[0]}
    )
    lo, hi = fit.conf_int().loc["treated"]
    return {
        "label": label,
        "estimate": float(fit.params["treated"]),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "p": float(fit.pvalues["treated"]),
        "n_treated": int(d["treated"].sum()),
        "n_control": int((~d["treated"]).sum()),
    }


panel = primary[primary["t"].isin(DID_YEARS)].copy()
# For t = 2016 to 2018 the prior non-overlapping window is t-3, so prior_window_err is ERR(t-3);
# y is ERR(t+3) - ERR(t), so ERR(t+3) - ERR(t-3) is y + pre_change.
assert (panel["prior_window_fiscal_year"] == panel["t"] - 3).all()
panel["did_change"] = panel["y"] + panel["pre_change"]
panel = panel[panel["did_change"].notna()]
did_runs = pd.DataFrame(
    [
        did(panel[panel["x"].abs() <= DID_WINDOW], f"|ERR(t) - 1.0| <= {DID_WINDOW}"),
        did(panel[(panel["x"] - C0).abs() <= H], f"within the RDD bandwidth ({H:.3f})"),
    ]
).set_index("label")
did_runs["overlaps_rdd"] = (did_runs["ci_low"] <= main["ci_high"]) & (
    did_runs["ci_high"] >= main["ci_low"]
)
did_runs["same_sign"] = np.sign(did_runs["estimate"]) == np.sign(main["estimate"])
did_runs["supports_rdd"] = did_runs["overlaps_rdd"] & did_runs["same_sign"]
did_runs.round(4)

# COMMAND ----------

means = (
    panel[panel["x"].abs() <= DID_WINDOW]
    .assign(pre=lambda d: d["prior_window_err"], post=lambda d: d["err"] + d["y"])
    .groupby("treated")[["pre", "err", "post"]]
    .mean()
)
fig, ax = plt.subplots()
for treated, color, name in [(False, BLUE, "not above 1.0 at t"), (True, ORANGE, "above 1.0 at t")]:
    ax.plot([-3, 0, 3], means.loc[treated], color=color, marker="o", label=name)
    ax.text(3.15, means.loc[treated, "post"], name, color=MUTED, fontsize=9, va="center")
ax.set_xticks([-3, 0, 3], ["window t-3 (pre)", "window t (selection)", "window t+3 (post)"])
ax.set_ylabel("Mean ERR")
ax.set_xlim(-3.4, 4.8)
ax.set_title(
    f"Rows selected on ERR(t) rise into t and fall back after; DiD uses t-3 and t+3 only "
    f"(|ERR(t) - 1.0| <= {DID_WINDOW})"
)
show(fig)
means.round(4)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Why this is not regression to the mean
# MAGIC
# MAGIC Every hospital's ratio is partly luck: a few more or fewer readmissions in a three-year window
# MAGIC move it. So a hospital that scores badly this cycle tends to score better next cycle with no
# MAGIC change in how it treats patients, simply because some of the bad score was bad luck that does
# MAGIC not repeat. The chart in section 5 shows it: rows picked for being above 1.0 at t had risen
# MAGIC into t and fall back afterwards. Comparing penalized with unpenalized hospitals directly would
# MAGIC credit the penalty with that fall.
# MAGIC
# MAGIC The regression discontinuity compares hospitals that are almost identical: one at 1.004,
# MAGIC another at 0.996. Both had about the same amount of bad luck, so both should fall back by
# MAGIC about the same amount. The only thing that differs between them is that one was penalized.
# MAGIC The design draws a smooth line through the fall-back on each side of 1.0 (the two fitted lines
# MAGIC in section 3) and measures only the step between them at 1.0. Regression to the mean bends
# MAGIC those lines but cannot make them step at exactly 1.0, because luck has no reason to change at
# MAGIC that value; the penalty does. The placebo cutoffs in section 2.3 check exactly that: at
# MAGIC 0.90, 0.95, 1.05 and 1.10, where nothing changes, there should be no step.
# MAGIC
# MAGIC The DiD check handles it a different way: it leaves window t, the one hospitals were selected
# MAGIC on, out of the comparison and compares the windows before and after it.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Result, in the plan's terms
# MAGIC
# MAGIC Section 6 of the plan fixed how the primary interval is read before it was computed.

# COMMAND ----------

if main["ci_low"] > 0 or main["ci_high"] < 0:
    verdict = "effect"
    reading = (
        f"An effect: crossing 1.0 changes the next window's ERR by {main['estimate']:+.4f} "
        f"(robust 95% CI {main['ci_low']:+.4f} to {main['ci_high']:+.4f})."
    )
elif -SMALLEST_EFFECT <= main["ci_low"] and main["ci_high"] <= SMALLEST_EFFECT:
    verdict = "null"
    reading = (
        f"A null: effects larger than {SMALLEST_EFFECT} in either direction are ruled out at the "
        f"cutoff (robust 95% CI {main['ci_low']:+.4f} to {main['ci_high']:+.4f}; two one-sided "
        f"tests p = {main['tost_p']:.3f})."
    )
else:
    verdict = "inconclusive"
    reach = max(abs(main["ci_low"]), abs(main["ci_high"]))
    reading = (
        f"Inconclusive: the data cannot distinguish no effect from an effect of up to {reach:.3f} "
        f"(robust 95% CI {main['ci_low']:+.4f} to {main['ci_high']:+.4f}; MDE {main['mde']:.4f})."
    )
print(reading)
checks = {
    "density_p": float(density.loc["analysis sample", "p"]),
    "balance_failures": int(balance["imbalanced"].sum()),
    "placebo_failures": int(placebo["survives_bh"].sum()),
    "did_supports_rdd": bool(did_runs["supports_rdd"].all()),
}
print(checks)

# COMMAND ----------

summary = {
    "verdict": verdict,
    "reading": reading,
    "primary": main,
    "checks": checks,
    "density": density.reset_index().to_dict("records"),
    "balance": balance.reset_index().to_dict("records"),
    "placebo": placebo.reset_index().to_dict("records"),
    "bandwidth": by_bandwidth.reset_index().to_dict("records"),
    "specifications": specifications,
    "per_measure": per_measure.reset_index().to_dict("records"),
    "secondary": secondary_runs,
    "secondary_placebo": secondary_placebo.reset_index().to_dict("records"),
    "hospital": hospital_run,
    "did": did_runs.reset_index().to_dict("records"),
    "sample": {
        "primary_rows": len(primary),
        "primary_facilities": int(primary["facility_id"].nunique()),
        "secondary_rows": len(secondary),
        "secondary_facilities": int(secondary["facility_id"].nunique()),
        "stand_in_rows_left_out": int(stand_in.sum()),
        "rows_by_year": sample_counts["rows"].to_dict(),
        "rows_at_cutoff": int((primary["x"] == 0).sum()),
        "c": C0,
        "discharges_median_inside": float(inside["discharges"].median()),
        "discharges_median_all": float(primary["discharges"].median()),
    },
}
# Machine-readable copy of every number above, for docs/causal-findings.md.
print(json.dumps(summary, default=lambda v: v.item() if hasattr(v, "item") else str(v)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Supplementary, not pre-registered
# MAGIC
# MAGIC ### 8.1 First-time penalized hospitals, event study
# MAGIC
# MAGIC The task card also asked for hospitals penalized for the first time against never-penalized
# MAGIC ones, with an event-study plot of pre-trends. The plan did not pre-register it, so it sits
# MAGIC here and never changes the headline.
# MAGIC
# MAGIC Hospital-level, FY2013 to FY2021. Outcome: the hospital's mean ERR over AMI, HF and
# MAGIC pneumonia, the three conditions present in every year, so measure entry does not move it.
# MAGIC Cohort g: first penalized in FY g (2014 to 2018), having reported a condition and not been
# MAGIC penalized in every earlier year from FY2013. Control: hospitals reporting in every year
# MAGIC FY2013 to FY2018 and never penalized in them. For each event time e, every cohort's change
# MAGIC from FY g-1 to FY g+e is compared with the controls' change over the same years (stacked by
# MAGIC cohort, errors clustered by facility), which avoids the bias of a single two-way fixed
# MAGIC effects regression with staggered timing.
# MAGIC
# MAGIC Two reasons to read it with care. Consecutive fiscal years share two of three years of
# MAGIC discharges, so adjacent points are not independent. And a hospital becomes "first-time
# MAGIC penalized" because its ERR rose above 1.0, so the rise into g and some fall after it are
# MAGIC selection and regression to the mean, not a penalty response: this is the comparison the
# MAGIC RDD exists to avoid.

# COMMAND ----------

original = facts[facts["measure"].isin(["AMI", "HF", "PN"]) & facts["err"].notna()]
hospital_err = (
    original[original["fiscal_year"].between(2013, 2021)]
    .groupby(["facility_id", "fiscal_year"])["err"]
    .mean()
    .unstack()
)
status = (
    mart[mart["fiscal_year"].isin(PRIMARY_YEARS) & (mart["conditions_reported"] > 0)]
    .pivot(index="facility_id", columns="fiscal_year", values="penalized")
    .reindex(columns=list(PRIMARY_YEARS))
)
complete = status.notna().all(axis=1)
first = (
    status[complete].astype(bool).idxmax(axis=1).where(status[complete].astype(bool).any(axis=1))
)
cohorts = first[first > min(PRIMARY_YEARS)].astype(int)
never = first[first.isna()].index

event_rows = []
for e in range(-4, 4):
    stacks = []
    for g in sorted(cohorts.unique()):
        base, year = g - 1, g + e
        if year == base or year not in hospital_err.columns or year < min(PRIMARY_YEARS):
            continue
        cohort = cohorts.index[cohorts == g]
        ids = hospital_err.index.intersection(cohort.append(never))
        change = (hospital_err.loc[ids, year] - hospital_err.loc[ids, base]).dropna()
        stacks.append(
            pd.DataFrame(
                {
                    "facility_id": change.index,
                    "change": change.to_numpy(),
                    "cohort": g,
                    "treated": change.index.isin(cohort),
                }
            )
        )
    if not stacks:
        continue
    d = pd.concat(stacks)
    design = pd.get_dummies(d["cohort"].astype(str), drop_first=True, dtype=float)
    design.insert(0, "treated", d["treated"].astype(float).to_numpy())
    design = sm.add_constant(design)
    fit = sm.OLS(d["change"].to_numpy(), design.to_numpy()).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(d["facility_id"])[0]}
    )
    lo, hi = fit.conf_int()[1]
    event_rows.append(
        {
            "e": e,
            "estimate": fit.params[1],
            "ci_low": lo,
            "ci_high": hi,
            "cohorts": d["cohort"].nunique(),
        }
    )
event = pd.DataFrame(event_rows)

fig, ax = plt.subplots()
ax.axvspan(-4.5, -0.5, color=LIGHT_GRAY, alpha=0.4, linewidth=0)
ax.vlines(event["e"], event["ci_low"], event["ci_high"], color=BLUE, linewidth=2)
ax.plot(event["e"], event["estimate"], "o", color=BLUE, markersize=7)
ax.plot([-1], [0], "o", color=MUTED, markersize=7)
ax.axhline(0, color=MUTED, linewidth=1)
ax.set_xlabel("Fiscal years since first penalty (gray: before; -1 is the base year); 95% intervals")
ax.set_ylabel("Mean ERR change vs never penalized")
ax.set_title(
    f"First-time penalized ({len(cohorts):,} hospitals) vs never penalized ({len(never):,}): "
    "the jump at 0 is selection"
)
show(fig)
print(f"Cohorts: {cohorts.value_counts().sort_index().to_dict()}; never penalized: {len(never):,}")
event.round(4)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 8.2 Outcome availability at the cutoff (added after the first run)
# MAGIC
# MAGIC Not pre-registered. The first run of this notebook found no density jump in the analysis
# MAGIC sample (section 2.1) but a rejection on all primary-year rows with an ERR, with or without an
# MAGIC outcome. A row leaves the analysis sample when CMS publishes no ERR for it in year s. If that
# MAGIC attrition jumped at the cutoff, the rows left on each side could differ. This runs the primary
# MAGIC specification with "has an outcome" (0 or 1) as the outcome. It describes the sample and
# MAGIC does not change the headline.

# COMMAND ----------

with_err = (
    facts[facts["fiscal_year"].isin(PRIMARY_YEARS) & facts["err"].notna()]
    .rename(columns={"fiscal_year": "t"})
    .merge(outcome[[*KEY, "t"]].assign(has_outcome=1.0), on=[*KEY, "t"], how="left")
    .fillna({"has_outcome": 0.0})
)
with_err["measure_year"] = with_err["measure"] + "_" + with_err["t"].astype(str)
attrition = rd(with_err, "has_outcome", label="has an outcome in year s")
print(
    f"Rows with an outcome: {with_err['has_outcome'].mean():.1%} of {len(with_err):,}; jump at "
    f"the cutoff {attrition['estimate']:+.4f} (robust 95% CI {attrition['ci_low']:+.4f} to "
    f"{attrition['ci_high']:+.4f}, p = {attrition['p']:.3f})"
)
print(json.dumps({"attrition": attrition}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Deviations from plan
# MAGIC
# MAGIC 1. **Evaluation point of the cutoff.** Gold counts a condition only when its ERR is strictly
# MAGIC    above 1.0, while `rdrobust` puts rows at exactly c on the treated side. The RDD is
# MAGIC    evaluated at half the smallest positive running value in the data (printed in section 1),
# MAGIC    so rows at exactly 1.0 stay untreated as Gold has them. The treated set is unchanged, and
# MAGIC    the density test uses the same point. Reason: to match the plan's treatment definition
# MAGIC    (`above_cutoff`); not a design change.
# MAGIC 2. **DiD computed in first differences.** The plan's model (facility-measure and
# MAGIC    measure-by-year-by-post fixed effects, two windows) is fitted as the change from t-3 to t+3
# MAGIC    on treated plus measure-by-year indicators, with the facility-measure effect taken per year
# MAGIC    t. With two windows per unit the two are the same estimator. Reason: simpler and exact.
# MAGIC 3. **Bandwidth sensitivity scales both bandwidths.** The 0.5x to 2x runs scale the bias
# MAGIC    bandwidth b with h, so 1x reproduces the primary run; the plan only names h. Reason: keeps
# MAGIC    the bias correction comparable across runs.
# MAGIC 4. **Section 8.1 is supplementary and was not pre-registered.** The task card asked for it;
# MAGIC    it was designed in this notebook, before the first run, and does not enter the headline.
# MAGIC 5. **Section 8.2 was added after the first run.** It checks outcome attrition at the cutoff,
# MAGIC    prompted by the density result on all rows. Nothing else in the notebook changed between
# MAGIC    the two runs except labels and this section. It does not enter the headline.
# MAGIC 6. **Package environment.** Installing `rdrobust` and `rddensity` alone broke the matplotlib
# MAGIC    import on serverless (a newer matplotlib against the default numpy), so the install cell
# MAGIC    also requests numpy 1.26 and restarts Python. The versions that ran are printed above. No
# MAGIC    effect on the analysis.
# MAGIC 7. **Secondary sample with the FY2019 to FY2026 peer medians (2026-10-28).** The plan
# MAGIC    restricted the primary sample to FY2013 to FY2018 because those were the only years with
# MAGIC    an exact cutoff (plan, section 3.1), and ran FY2019 onward at a 1.0 stand-in as a
# MAGIC    sensitivity check. The FY2019 to FY2026 peer group medians were loaded from the IPPS
# MAGIC    supplemental files after the first run, so those years now have their exact cutoff. The
# MAGIC    primary sample and its result are unchanged. A secondary sample of every year t with an
# MAGIC    exact cutoff and an outcome (FY2013 to FY2023 and FY2025) replaces the stand-in runs in
# MAGIC    section 4, with the same specification and placebo offsets; rows still on a 1.0 stand-in
# MAGIC    (a hospital missing from a year's supplemental file, and FY2023 pneumonia) are left out.
# MAGIC    It was added after the primary results had been seen, so it is reported beside the
# MAGIC    primary result and does not replace it; the project owner asked for it.
