# Analysis plan (pre-registration)

Written 2026-10-21, before any outcome analysis: no ERR change, penalty rate or ERR
distribution was queried to write it. It rests on SPEC.md, `docs/gold.md`,
`docs/data-sources.md`, the Gold code in `src/readmission/gold/` and the table structure (schemas,
fiscal years, row counts, which years have an exact cutoff). It is committed on its own so the
history shows it came first. The EDA notebook (`analysis/01_eda`) and the causal notebook (task
2.3) follow it. A change to the causal design after outcome data has been seen is ask-first
(SPEC.md, Boundaries) and is recorded under Deviations at the end of this file, with the date
and the reason.

## 1. Question

Contoso's question, in part: does the HRRP penalty itself change hospital behavior? Stated so
the data can answer it:

> For a hospital-condition whose excess readmission ratio (ERR) in fiscal year t sits just
> above the penalty cutoff, how much does its ERR change by the next non-overlapping window,
> compared with one that sits just below?

## 2. Data

All from `workspace.readmission`, built by the `readmission_pipeline` job.

- `gold_facility_condition_year`: one row per facility (CCN), measure and fiscal year, FY2013
  to FY2027. Supplies `excess_readmission_ratio`, `penalty_cutoff`, `cutoff_is_approximate`,
  `above_cutoff`, `prior_window_fiscal_year`, `prior_window_err`, `err_change`, `discharges`,
  `anomaly_flag` and the hospital attributes.
- `gold_penalty_mart`: hospital and fiscal year level (penalized, conditions above cutoff, mean
  ERR, Contoso flag).
- `gold_features`: the ACS county measures by fiscal year, lagged as described in
  `docs/gold.md`.
- `gold_contoso_scorecard`: Contoso's 12 facilities.

Unit of observation for the causal analysis: facility, measure and fiscal year t. Standard
errors are clustered by facility throughout, because one hospital contributes up to six
measures in several years.

## 3. Design facts the analysis must handle

### 3.1 The cutoff by year

| Fiscal year t | Cutoff | Exact? | Role in the causal analysis |
|---|---|---|---|
| FY2013 to FY2018 | ERR = 1.0, the statutory rule | yes | primary |
| FY2019 to FY2026 | CMS used the median ERR of the hospital's dual-eligibility peer group; no file here carries it, Gold uses 1.0 as a stand-in | no (`cutoff_is_approximate = true`) | sensitivity only |
| FY2027 | `peer_median_err` from the IPPS supplemental file | yes | no outcome window yet (see 3.2) |

With an approximate cutoff the treatment is misclassified near 1.0: in FY2027, where both are
known, 6.2 percent of rows with an ERR fall on different sides of 1.0 and of their peer median,
and the FY2027 peer medians run from 0.9837 to 1.0143 (`docs/gold.md`). A sharp RDD at 1.0 in
those years estimates a diluted, attenuated jump. The primary analysis therefore uses only
FY2013 to FY2018 as year t.

### 3.2 The outcome window

Each ERR covers a three-year window that overlaps the next fiscal year's by two years, so the
outcome compares non-overlapping windows: the ERR of the fiscal year s whose prior
non-overlapping window is t (`readmission.gold.windows.prior_window_year`). Gold already
stores this as `err_change` on the row for year s, so the outcome for year t is `err_change`
of the row with `prior_window_fiscal_year = t`, same facility and measure.

| Year t (running variable) | Outcome year s | Notes |
|---|---|---|
| 2013 to 2018 | t + 3 (2016 to 2021) | primary; every outcome window ends by 06/2019, before COVID |
| 2019 to 2021 | t + 3 (2022 to 2024) | approximate cutoff; outcome windows shortened or interrupted by COVID |
| 2022 | 2025 | approximate cutoff |
| 2023 | 2026 | approximate cutoff |
| 2025 | 2027 | approximate cutoff at t; two-year outcome window |
| 2024, 2026, 2027 | none | no later non-overlapping window yet |

Timing: FY t's results are published with the IPPS final rule in August of t-1, and FY t+3's
window runs 07/(t-2) to 06/(t+1). About 13 of its 36 months are discharges from before the
hospital learned its FY t status, so a real effect is diluted by roughly a third in the t+3
outcome. FY t+4's window starts 07/(t-1), one month before the announcement. The t+4 change
(ERR of FY t+4 minus ERR of FY t, computed from the facts table, which also does not overlap
window t) is a pre-registered secondary outcome for that reason.

### 3.3 Power, and what restricting to exact years costs

Rows with an ERR in a year t that has an outcome year (from the Silver row counts in
`docs/data-sources.md`, not from outcomes): 75,542 in FY2013 to FY2018 and 80,461 in FY2019 to
FY2023 plus FY2025. Restricting the primary analysis to exact-cutoff years keeps 48 percent of
the candidate rows, so for the same bandwidth the standard error is about 1.44 times what
pooling all years would give, and the minimum detectable effect rises by the same factor. The
trade is deliberate: a pooled estimate would be more precise and biased toward zero by the
misclassified rows, with no way to size that bias. FY2013 and FY2014 carry three measures and
FY2015 and FY2016 five, so the exact years also lean on AMI, HF and pneumonia.

The causal notebook reports, next to the estimate, the minimum detectable effect at 80 percent
power and two-sided alpha 0.05 implied by the estimate's own standard error
(MDE = 2.80 x SE), and the number of effective observations on each side of the cutoff.

### 3.4 Other features of the ERR to keep in mind

- **Regression to the mean.** ERR is measured with noise, so rows with a high ERR at t tend to
  fall by t+3 whether or not they were penalized. This is smooth in the running variable and
  is absorbed by the local linear slopes; it does not create a jump at the cutoff. It is the
  reason a naive above-versus-below comparison is not used.
- **Shrinkage.** CMS's hierarchical model shrinks low-volume hospitals toward an ERR of 1.0, so
  hospitals near the cutoff over-represent small hospitals. The estimate is local to them; the
  notebook reports the discharge distribution inside the bandwidth against the full sample.
- **Penalty dose is a kink, not a jump.** Under HRRP the payment reduction grows with
  (ERR - 1) times the condition's base payments, so dollars at stake are continuous at the
  cutoff. What jumps is the status: the condition counts toward the penalty and the hospital
  is publicly penalized. The RDD estimates the effect of that status. A null RDD result is
  consistent with hospitals responding to dollars (small near the cutoff) rather than to the
  label, and the readout says so.
- **Persistence.** A row just above the cutoff at t is more likely to be above at t+1 and t+2.
  The estimand is the intent-to-treat effect of status at t, including any later statuses it
  makes more likely.
- **Hospital attributes** (ownership, type) are the current Hospital General Information
  snapshot, not as of year t.

## 4. Causal analysis (task 2.3)

### 4.1 Estimand

The average jump, at ERR(t) = cutoff(t), in the expected change in ERR from window t to window
s (section 3.2), for hospital-conditions in FY2013 to FY2018: a local intent-to-treat effect
of the condition counting toward the HRRP penalty in year t.

### 4.2 Primary specification

- Sharp RDD. Running variable: `excess_readmission_ratio - penalty_cutoff` in year t (cutoff
  1.0 in every primary year). Treatment: running variable above 0, which is Gold's
  `above_cutoff`.
- Outcome: `err_change` of the matching year s row. Rows with a null ERR at t or s drop out.
  Rows with `anomaly_flag = true` in year s stay in the primary analysis (dropping them would
  condition on the outcome) and are excluded in a sensitivity run.
- Pooled across measures and fiscal years, with measure-by-year indicators as covariates.
- Local linear regression, triangular kernel, a common MSE-optimal bandwidth on both sides
  (Calonico, Cattaneo and Titiunik 2014; `rdrobust`, `bwselect = "mserd"`), and the
  bias-corrected robust 95 percent confidence interval as the reported interval. Standard
  errors clustered by facility.
- If `rdrobust` will not install on serverless, a hand-rolled local linear estimator with the
  same kernel, the same bandwidth rule and facility-clustered errors is used instead, and the
  notebook says which ran.
- One confirmatory test: H0, the jump is zero, two-sided, alpha 0.05. No correction applies to
  this single primary test.

### 4.3 Validity checks (run before the estimate is reported)

- **Density at the cutoff**: the Cattaneo, Jansson and Ma (2020) density test (`rddensity`, or
  a McCrary-style test if that package is unavailable). Hospitals cannot set their own ERR,
  which CMS computes from claims, so no bunching is expected. A rejection is reported, and the
  estimate is still reported with that caveat.
- **Covariate balance**: the same RDD with discharges in year t, the pre-period ERR change (t-3
  to t, available for t = 2016 to 2018), and the ACS county poverty and age 65+ shares as
  outcomes. Benjamini-Hochberg across these four, q = 0.05.
- **Placebo cutoffs**: the same specification at ERR = 0.90 and 0.95 using only rows below 1.0,
  and at 1.05 and 1.10 using only rows above 1.0, so no placebo window straddles the real
  cutoff. Benjamini-Hochberg across the four, q = 0.05. Placebo jumps that survive correction
  are reported as evidence against the design.

### 4.4 Sensitivity (reported in full, never chosen among)

- Bandwidth 0.5, 0.75, 1.25, 1.5 and 2 times the MSE-optimal one, shown as a plot of estimate
  and interval against bandwidth.
- Local quadratic; uniform kernel.
- Donut: drop rows with |running variable| below 0.005.
- Excluding rows with `anomaly_flag = true` in year t or s.
- The t+4 outcome (section 3.2).
- Per measure (six estimates, Benjamini-Hochberg across them, q = 0.05). Secondary: these
  describe heterogeneity and do not replace the pooled test.
- Approximate-cutoff years (t = 2019 to 2023 and 2025) at the 1.0 stand-in, alone and pooled
  with the primary years, plus a donut run on those years that drops |ERR - 1.0| below 0.02,
  the band in which FY2027 shows the 1.0 stand-in and the peer median can disagree. These
  years also carry COVID-affected outcome windows, so they are read as a robustness check on
  the sign, not as a second estimate of the effect.
- Hospital level: running variable is the worst ERR minus the cutoff in year t (the hospital is
  penalized at all above it), outcome is the mean ERR change across the hospital's conditions
  with a ratio in both windows (`gold_penalty_mart.mean_err_change` of the year s row).

### 4.5 Difference-in-differences check

The DiD is a check on the RDD from a different identifying assumption, not a second
confirmatory test.

- Panel: facility-measure, two windows, pre = t-3 and post = t+3, for t = 2016 to 2018 (the
  primary years with a window three years earlier). The selection window t is left out on
  purpose: treatment is assigned on ERR(t), so comparing against window t would build regression
  to the mean into the difference. Windows t-3 and t+3 share no discharges with t.
- Treated: above the cutoff at t. Control: below it. Sample: |ERR(t) - 1.0| at most 0.10, and
  separately within the RDD bandwidth.
- Model: ERR on treated x post, with facility-measure fixed effects and measure-by-year-by-post
  fixed effects, standard errors clustered by facility.
- Assumption: absent the penalty, ERR would have moved from t-3 to t+3 by the same amount for
  hospitals just above and just below the cutoff. No pre-period placebo is possible within the
  exact years (t-6 predates FY2013); this is stated as a limitation.
- Reading it: the DiD supports the RDD when the two estimates have the same sign and their 95
  percent intervals overlap. Disagreement is reported, not reconciled.

## 5. Descriptive hypotheses tested in the EDA

The EDA (`analysis/01_eda`) tests these twelve as one family, each two-sided at alpha 0.05,
with Benjamini-Hochberg false discovery rate control at q = 0.05 across all twelve. With tens
of thousands of rows most tests will reject, so each is reported with an effect size and a
practical threshold fixed here; a result is called meaningful only when it survives the
correction and clears the threshold.

| ID | Hypothesis (H1) | Data | Test | Effect size, practical threshold |
|---|---|---|---|---|
| D1 | ERR distributions differ across the six conditions | condition-level ERR, FY2026 | Kruskal-Wallis | epsilon squared, 0.01 |
| D2 | The share of AMI, HF and pneumonia rows above the cutoff trends over FY2013 to FY2018 (the exact-cutoff years, and the three conditions present in all of them, so measure entry does not drive the trend) | condition-level `above_cutoff` | logistic regression on fiscal year, facility-clustered | change in share per year, 1 point |
| D3 | ERR is associated with hospital volume | condition-level ERR and log discharges, FY2026 | Spearman | rho, 0.10 in absolute value |
| D4 | ERR spread differs by volume tercile (shrinkage toward 1.0 for small hospitals) | condition-level ERR, FY2026, terciles of discharges within measure | Brown-Forsythe | ratio of largest to smallest interquartile range, 1.25 |
| D5 | Hospital mean ERR differs by ownership category | hospital-level `mean_err`, FY2026 | Kruskal-Wallis | epsilon squared, 0.01 |
| D6 | Hospital mean ERR is associated with county median household income | `mean_err`, FY2026, with that row's ACS vintage from `gold_features` | Spearman | rho, 0.10 |
| D7 | ... with county poverty share | as D6 | Spearman | rho, 0.10 |
| D8 | ... with county uninsured share | as D6 | Spearman | rho, 0.10 |
| D9 | ... with county share aged 65 and over | as D6 | Spearman | rho, 0.10 |
| D10 | ... with county disability share | as D6 | Spearman | rho, 0.10 |
| D11 | Contoso's 12 hospitals differ in mean ERR from the other Ohio hospitals | hospital-level `mean_err`, FY2026 | Mann-Whitney U | difference in medians, 0.01 |
| D12 | The anomaly flag rate differs between COVID-affected years (FY2022 to FY2025) and the rest | condition-level `anomaly_flag`, FY2016 to FY2027 (years with a prior window) | chi-squared | difference in rate, 1 point |

Notes on the family:

- FY2026 is the cross-section year: the latest full three-year window on PDC with predicted and
  expected rates, and the current Hospital General Information snapshot matches it best.
- Hospital-level `mean_err` averages the conditions a hospital reports, unweighted, so a
  hospital with fewer conditions is not comparable term by term. D5 to D11 are descriptive for
  that reason too.
- D11 has 12 hospitals in one group and little power; a non-rejection there is reported as
  "not distinguishable at this sample size", never as "Contoso is like its peers".
- Contoso's facilities are real Ohio hospitals relabeled (`docs/data-sources.md`), so D11
  describes those hospitals' CMS data, not anything about the fictional client's operations.
- The ACS associations are ecological (county, not patient) and say nothing about why.

What the EDA does not do, so the causal analysis stays blind: it does not plot or tabulate ERR
change against ERR in the same row's prior window near the cutoff, and does not split ERR
change by `above_cutoff`. Anomaly review uses `err_change` only through the robust z-scores
already in Gold, pooled over all ERR levels.

## 6. What counts as a null result, and how it is reported

The smallest effect of interest is an ERR change of 0.02, the threshold Gold already uses for a
hospital's trend (`gold_penalty_mart.TREND_THRESHOLD`): an ERR of 1.02 means 2 percent more
readmissions than expected, and smaller moves are within a cycle's normal noise.

For the primary RDD estimate and its robust 95 percent interval:

| Interval | Reported as |
|---|---|
| excludes 0 | an effect: sign, size and interval, with the validity checks beside it |
| includes 0 and lies within -0.02 to 0.02 | a null: "effects larger than 0.02 in either direction are ruled out at the cutoff" (an equivalence statement, two one-sided tests at 0.05) |
| includes 0 and extends past -0.02 or 0.02 | inconclusive: "the data cannot distinguish no effect from an effect of up to X", with the MDE from section 3.3 |

A null or inconclusive result is reported in the readout with the same prominence a positive
one would get, together with the reasons it may understate an effect: the t+3 window's
pre-announcement months, the kink in penalty dollars, and the local nature of the estimate.
Results from section 4.4 are never promoted to the headline if the primary is null.

For the descriptive family: each test is reported with its raw p, its BH-adjusted p, the effect
size and whether it clears the practical threshold, including the ones that do not.

## 7. Outputs

- `analysis/01_eda` and its HTML export under `docs/eda/`.
- The causal notebook (task 2.3), which reads this plan's numbers (cutoff years, bandwidth rule,
  placebo cutoffs, donut widths, smallest effect of interest) as constants and cites this file.
- The readout's "what the data can and cannot answer" section.

## Deviations

None yet.
