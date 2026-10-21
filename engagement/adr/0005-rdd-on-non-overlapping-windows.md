# ADR 0005: Estimate the penalty's effect by regression discontinuity on non-overlapping windows

Status: accepted (2026-10-21). Recorded in `docs/analysis-plan.md` before any outcome was
examined.

## Context

Contoso asked whether the penalty itself changes hospital behavior. A hospital-condition whose
ERR lands just above the cutoff is counted toward the penalty; one just below is not. Comparing
penalized with unpenalized hospitals naively is misleading, because ERR is measured with noise
and a high ratio tends to fall back on its own (`docs/causal-findings.md`).

Two facts of the data shape the design (`docs/gold.md`):

- Each fiscal year's ERR covers a three-year window of discharges, and consecutive years share
  two of the three. Fiscal year t against t+1 mostly compares the same patients.
- The cutoff is 1.0 for FY2013 to FY2018. From FY2019 it is a peer group median that the data
  here does not carry (ADR 0006).

Options considered:

1. **Regression discontinuity at the cutoff**, outcome measured on a window that shares no
   discharges with the year that assigned treatment.
2. **Difference-in-differences** of penalized against unpenalized hospitals.
3. **Compare t with t+1** and pool all years.

## Decision

Option 1 as the confirmatory test, with option 2 as a check from a different assumption.

- Running variable: ERR minus cutoff in year t. Treatment: above the cutoff.
- Outcome: the change in ERR from window t to the next non-overlapping window, normally t+3
  (`readmission.gold.windows.prior_window_year`). Gold stores it as `err_change`.
- Primary years: FY2013 to FY2018, where the cutoff is exact. The approximate-cutoff years are
  a sensitivity run only.
- Local linear regression, triangular kernel, MSE-optimal bandwidth, robust bias-corrected 95
  percent interval, errors clustered by facility.
- The analysis plan was committed first, with the wording of a null result fixed in advance:
  the smallest effect of interest is an ERR change of 0.02.
- The difference-in-differences compares t+3 with t-3, leaving out window t, so regression to
  the mean is not built into the difference.

## Why

- **Overlap would manufacture a result.** An outcome that shares two thirds of its claims with
  the assigning year carries the assigning year's noise.
- **The jump at the cutoff isolates the status.** Luck has no reason to change at exactly 1.0;
  the penalty does.
- **Exact years only.** An approximate cutoff misclassifies treatment near 1.0 and attenuates
  the estimate; pooling would be more precise and biased toward zero by an amount that cannot be
  sized.

## Consequences

- **The result is a null with power behind it.** Estimate +0.0004, robust 95 percent interval
  -0.0027 to +0.0047, minimum detectable effect 0.0053 at 80 percent power. Effects larger than
  0.02 either way are ruled out at the cutoff (`docs/causal-findings.md`).
- **It answers a narrow question.** It speaks to hospitals near 1.0 and to the status, not the
  dollars; hospitals far above the line are not covered.
- **The two designs disagree in sign.** The DiD estimate is -0.0030 (interval -0.0058 to
  -0.0002), so by the plan's own rule it does not support the RDD. Both are small, and the
  disagreement is reported, not reconciled.
- **Differential retention is the main caveat.** Rows just above 1.0 are 3.5 points more likely
  to have a later ERR. Lee bounds were not pre-registered and have not been run.
- **No outcome for FY2024, FY2026 and FY2027** yet; they have no later non-overlapping window.
- **Dilution.** About 13 of the 36 months in the t+3 window precede the hospital learning its
  status, which dilutes a real effect by roughly a third; the t+4 outcome gives the same null.
