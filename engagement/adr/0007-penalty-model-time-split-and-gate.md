# ADR 0007: Train the penalty model on past years only, pick the champion on validation and gate on test

Status: accepted (2026-10-21)

## Context

Contoso wants to know which facility-condition pairs will be above the cutoff next cycle.
Consecutive fiscal years share two of three performance years, so last year's flag already
predicts a lot: any model has to beat that rule, and a random split would leak neighbouring
years into the test set (`docs/model-card.md`).

Options considered:

1. **Random split** of facility-condition-years.
2. **Split by target year**, train on the oldest years, validate and test on later ones, with
   features built only from earlier years.
3. **Refit through the latest year** before scoring.

## Decision

Option 2. Train FY2014 to FY2023, validate FY2024 and FY2025, test FY2026, and report FY2027
(the exact-cutoff year) as a second test. Features for target year T use only what CMS had
published before T's results: HRRP history strictly before T and ACS county measures from
vintage T-3. `tests/test_gold.py::test_features_never_use_the_target_year_or_later` fails if any
feature column moves when year T or later is changed.

Candidates: the carry-forward rule, logistic regression and LightGBM. The champion is the fitted
model with the higher validation PR-AUC (LightGBM, 0.858 against 0.852), registered in Unity
Catalog as `penalty_model` with the `champion` alias. The `train` task fails, and leaves the
alias where it was, if the champion's test AUC is below the logistic regression's or below 0.80.
The registered model is not refit before scoring FY2028.

## Why

- **The split matches use.** The model is used on a year it has not seen, so it is measured that
  way.
- **The gate sits above the naive rule.** The carry-forward rule scores 0.772 test AUC, so a
  model that restates last year's flag cannot pass the 0.80 floor.
- **Not refitting keeps the metrics honest.** Refitting through FY2027 would use more recent
  data and leave no held-out year to describe the model that scores.

## Consequences

- **The lead over logistic regression is small** on every split; the large step is from the
  naive rule to either model (test AUC 0.772 to 0.874). LightGBM is kept because it won the
  stated rule, and either would do.
- **FY2027 is weak.** Test AUC 0.678, probabilities too confident at both ends: rows scored
  under 0.1 are above the cutoff 22 percent of the time, and rows over 0.9, 76 percent. The
  dashboard shows probabilities with that caption, never a yes or no.
- **Small hospitals rank worst** on FY2027 (AUC 0.634 against 0.715 for large ones), and
  hospitals with no prior ratio are flagged least (`docs/model-card.md`, Fairness).
- **No dollar estimate and no patient-level use.** The model predicts above or below the cutoff
  for a hospital and condition.
- **The job retrains on every run**, registering a new version each time; the alias moves only
  when the gate passes.
