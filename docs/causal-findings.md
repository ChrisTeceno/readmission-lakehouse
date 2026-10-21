# Causal findings: does the HRRP penalty change hospital behavior?

For the readout. Source: `analysis/02_causal` run on serverless on 2026-10-21 over that day's Gold
tables (export with outputs: `docs/causal/02_causal.html`). The design, every check and the
wording of a null result were fixed beforehand in `docs/analysis-plan.md`; everything the plan
asked for is reported below, including the checks that came out against the design.

## Pre-registration

- The analysis plan was committed on its own as `3aaf635` ("Analysis plan: pre-registration of
  the RDD, DiD check and EDA hypotheses, written before outcomes are examined") on 2026-10-21 at
  10:42:39 -0600, on branch `pf/eda-notebook-and-analysis-plan-written-before-ou`.
- The factory squash-merged that branch (PR #9), so on `main` the plan and the EDA arrive
  together in `07d6c4f` (2026-10-21 11:01:24 -0600). The standalone commit `3aaf635` is the
  evidence that the plan came first.
- The causal notebook was first committed as `bf64ec8` on 2026-10-21 at 11:12:07 -0600, after the
  plan, and before it was run on the real outcomes. Its later commit adds only the supplementary
  attrition check (section 8.2 of the notebook) and fixes to labels, comments and printed
  versions; the primary specification and
  every pre-registered check are unchanged, and both runs give identical numbers.

## The answer

**No.** Crossing the penalty cutoff did not change a hospital-condition's readmission ratio by
the next cycle by any amount that matters.

| | |
|---|---|
| Estimate (jump in ERR change at ERR = 1.0) | +0.0004 |
| Robust bias-corrected 95% interval | -0.0027 to +0.0047 |
| p (H0: no jump, two-sided) | 0.60 |
| Minimum detectable effect, 80% power (2.80 x robust SE) | 0.0053 |
| Equivalence test against +/-0.02 (two one-sided tests) | p < 0.001 |
| Bandwidth (MSE-optimal, triangular kernel) | 0.061 either side of 1.0 |
| Effective rows | 20,819 below, 18,423 above |
| Sample | 69,163 hospital-condition-years, 3,268 hospitals, FY2013 to FY2018 |

In the plan's terms (section 6) this is a **null**: effects larger than 0.02 in either direction
are ruled out at the cutoff. The data had the power to see much less: an effect of 0.0053, about a quarter of
that threshold, would have been detected with 80 percent probability.

What the numbers mean: an ERR of 1.02 is 2 percent more readmissions than CMS expects for that
hospital's patients. Hospitals whose ratio landed just over 1.0, and so had that condition
counted toward their penalty and were publicly listed as penalized, ended the next
non-overlapping three-year window with the same ratio change, to within a few tenths of a
percent, as hospitals that landed just under it.

## What it means for Contoso

- **The label alone does not move the number.** Being pushed just over the line, with the
  public penalty and the small payment cut that come with it there, does not by itself make a
  hospital's readmission ratio improve. If Contoso wants a facility near 1.0 to improve, it will
  have to come from something the facility does, not from the penalty status.
- **A penalized facility's ratio falling next cycle is not evidence that anything worked.**
  Ratios near and above 1.0 drift back toward 1.0 on their own (next section). Contoso should
  judge its own readmission programs against comparable facilities that did not run them, which
  is what the pilot design in this project sets out to do.
- **This says nothing about hospitals far above the line.** The design compares hospitals near
  1.0, where the dollars at stake are small. Hospitals far above it face much larger cuts and may
  respond to those; this analysis cannot tell.

## Why this is not regression to the mean

Every hospital's ratio is partly luck: a handful more or fewer readmissions in a three-year
window moves it. So a hospital with a bad score this cycle tends to score better next cycle with
no change in care, because some of the bad score was bad luck that does not repeat. Compared
naively, penalized hospitals would look as if the penalty helped. The notebook shows it: rows
within 0.10 of the cutoff that were above 1.0 at t averaged 1.018 three years earlier, 1.042 at
t and 1.018 three years later; the rows below averaged 0.984, 0.958 and 0.987.

The regression discontinuity compares hospitals that are nearly identical, one at 1.004 and
another at 0.996. Both had about the same bad luck, so both should fall back by about the same
amount; the only difference between them is the penalty. The method fits a smooth line through
the fall-back on each side of 1.0 and measures only the step between the lines at exactly 1.0.
Luck has no reason to change at 1.0; the penalty does. The placebo cutoffs (below) confirm that
no step appears at values where nothing changes.

## Validity checks (all reported, plan section 4.3)

| Check | Result | Reading |
|---|---|---|
| Density, analysis sample (Cattaneo, Jansson and Ma) | t = 0.11, p = 0.91 | no jump in the number of rows at 1.0 |
| Density, every FY2013 to FY2018 row with an ERR (context run) | t = -2.62, p = 0.009 | rejects: fewer rows just above 1.0 than just below |
| Balance: discharges in year t | +4.8 (-15.1 to +20.6), BH p 0.97 | balanced |
| Balance: ERR change t-3 to t | +0.002 (-0.003 to +0.008), BH p 0.89 | balanced |
| Balance: county share below poverty | -0.14 points (-0.56 to +0.24), BH p 0.89 | balanced |
| Balance: county share aged 65+ | +0.02 points (-0.28 to +0.27), BH p 0.97 | balanced |
| Placebo at ERR 0.90 | -0.0002 (-0.010 to +0.007), BH p 0.75 | no jump |
| Placebo at ERR 0.95 | +0.0037 (-0.004 to +0.012), BH p 0.61 | no jump |
| Placebo at ERR 1.05 | -0.0056 (-0.018 to +0.004), BH p 0.61 | no jump |
| Placebo at ERR 1.10 | +0.0032 (-0.006 to +0.013), BH p 0.63 | no jump |

BH: Benjamini-Hochberg adjusted across the four checks of each family, q = 0.05.

The density test on all rows rejects, and the plan says to report the estimate with that caveat,
which this does. It led to one check that was not pre-registered (notebook section 8.2): whether
a row has an outcome at all, that is whether CMS published an ERR for it in the later window.
That share jumps by 3.5 points at the cutoff (+0.035, 95% interval +0.019 to +0.058; 91.6 percent
of rows have an outcome overall): conditions just above 1.0 are more likely to be reported again
three years later. In the analysis sample the density test does not reject, consistent with the two
offsetting, and the balance tests show no
difference between the sides in volume, prior trend or county. It still means the rows compared
just above and just below 1.0 were retained at slightly different rates, which is the main
caveat on this result (see Limitations).

## Sensitivity (all reported, plan section 4.4)

| Specification | Estimate | Robust 95% interval |
|---|---|---|
| Bandwidth 0.5x (0.031) | +0.0016 | -0.0031 to +0.0069 |
| Bandwidth 0.75x | +0.0008 | -0.0031 to +0.0053 |
| Bandwidth 1x (primary) | +0.0004 | -0.0027 to +0.0047 |
| Bandwidth 1.25x | +0.0002 | -0.0023 to +0.0046 |
| Bandwidth 1.5x | -0.0000 | -0.0023 to +0.0043 |
| Bandwidth 2x (0.122) | -0.0008 | -0.0029 to +0.0033 |
| Local quadratic | +0.0008 | -0.0036 to +0.0049 |
| Uniform kernel | +0.0012 | -0.0025 to +0.0056 |
| Donut, drops within 0.005 of 1.0 | +0.0002 | -0.0040 to +0.0056 |
| Excluding anomaly-flagged rows | +0.0007 | -0.0024 to +0.0052 |
| Outcome at t+4 (secondary) | +0.0009 | -0.0023 to +0.0054 |
| Hospital level (worst ERR) | +0.0018 | -0.0037 to +0.0078 |
| FY2019 to FY2023 and FY2025 at the 1.0 stand-in | -0.0020 | -0.0054 to +0.0012 |
| Those years pooled with the primary years | -0.0008 | -0.0033 to +0.0020 |
| FY2019 to FY2023 and FY2025, donut 0.02 | -0.0041 | -0.0127 to +0.0063 |

Per condition (secondary, BH across six): AMI +0.003, HF -0.004, pneumonia -0.001, COPD
+0.006, hip/knee +0.006, CABG -0.005; none is distinguishable from zero (smallest adjusted p
0.42). Hip/knee and CABG have the fewest rows and intervals reaching past 0.02.

Every interval includes zero and every one except the per-condition hip/knee and CABG runs lies
inside +/-0.02. None of these is promoted to the headline.

## Difference-in-differences check (plan section 4.5)

ERR in window t+3 against window t-3 (both share no discharges with window t, on which treatment
is assigned), for t = 2016 to 2018, facility-measure and measure-by-year-by-post fixed effects,
facility-clustered errors.

| Sample | Estimate | 95% interval | Rows above / below |
|---|---|---|---|
| ERR(t) within 0.10 of 1.0 | -0.0030 | -0.0058 to -0.0002 | 10,180 / 11,393 |
| Within the RDD bandwidth (0.061) | -0.0025 | -0.0056 to +0.0005 | 7,441 / 8,296 |

The plan counts the DiD as supporting the RDD when the signs agree and the intervals overlap.
The intervals overlap but the signs differ, so by that rule it does not support it, and the
disagreement is reported rather than reconciled. Both estimates are small: the DiD's largest
reading is a 0.006 improvement, under a third of the smallest effect of interest. The DiD rests
on a different assumption (that hospitals just above and just below 1.0 would have moved alike
from t-3 to t+3), which cannot be checked here because t-6 predates FY2013.

## Supplementary, not pre-registered

**First-time penalized against never-penalized hospitals (event study).** The task card asked
for it; it was designed in the notebook before the first run and does not change the headline.
743 hospitals were first penalized in FY2014 to FY2018 (252, 319, 82, 50 and 40 by year) against
64 never penalized in FY2013 to FY2018. Relative to the year before the first penalty, their mean
AMI, HF and pneumonia ERR was 0.009 to 0.014 lower beforehand, jumped 0.023 in the first
penalized year and stayed 0.020 to 0.028 higher for the next three years. That jump is the
selection that defines the group (a hospital becomes first-time penalized because its ratio rose),
and there is no sign of a fall-back relative to never-penalized hospitals within three years. The
comparison cannot separate a penalty response from selection, which is why it is not the design,
and with 64 control hospitals it is imprecise.

**Outcome availability at the cutoff.** Covered under Validity checks above.

## Limitations

- **Differential retention at the cutoff.** Rows just above 1.0 are 3.5 points more likely to
  have a later ERR. If the extra retained rows differ in their ERR change, the estimate is
  biased by an unknown amount. Bounding it (for example with Lee bounds, trimming the better
  retained side) was not pre-registered and has not been run.
- **Local to the cutoff, and to small hospitals.** The estimate applies to hospitals near 1.0.
  CMS shrinks low-volume hospitals toward 1.0, so those inside the bandwidth are smaller (median
  201 discharges against 237 in the full sample).
- **Status, not dollars.** The payment cut grows with the distance above the cutoff, so near 1.0
  it is small. The RDD measures the effect of the status (counted, publicly penalized). A
  response to dollars, which would show up far above the line, is not ruled out.
- **Dilution in the outcome window.** About 13 of the 36 months in the t+3 window are discharges
  from before the hospital learned its FY t status, which dilutes a real effect by roughly a
  third. The t+4 outcome, which starts one month before the announcement, gives the same null.
- **Exact years only, early conditions weighted.** FY2013 to FY2018 had a 1.0 cutoff; later years
  used peer group medians not in this data. AMI, heart failure and pneumonia are present in every
  primary year and carry more weight than COPD, hip/knee and CABG.
- **Persistence.** A row just above 1.0 at t is more likely to be above it again at t+1 and t+2,
  so this is the effect of being penalized at t including the later penalties it makes more
  likely.
- **Relative measure.** ERR is standardized each year, so the analysis compares hospitals with
  each other and cannot show whether readmissions fell nationally.

## Deviations from plan

Listed in full in section 9 of the notebook:

1. The RDD is evaluated at half the smallest positive running value (2.1e-7) rather than at
   exactly 0, so the 6 rows with an ERR of exactly 1.0 stay untreated as Gold's `above_cutoff`
   has them. The treated set is the plan's.
2. The DiD's two-window fixed effects model is computed as the equivalent first-difference
   regression.
3. The bandwidth sensitivity runs scale the bias bandwidth with the main one, so 1x reproduces
   the primary run.
4. The event study is supplementary and was not pre-registered (the task card asked for it).
5. The outcome-availability check was added after the first run, prompted by the density
   result on all rows. It does not enter the headline.
6. The install cell also requests numpy 1.26 and restarts Python, because the packages broke
   the default matplotlib import on serverless. `rdrobust` 2.1.1 and `rddensity` 3.0 ran; the
   hand-rolled fallback the plan allows was not needed.
