# Model card: HRRP penalty model

`workspace.readmission.penalty_model`, alias `champion`. Code: `src/readmission/ml/`. MLflow
experiment: `/Shared/readmission-penalty`. Numbers below are from the 2026-10-27 job run
1074163289288573 (model version 7, LightGBM, MLflow run `f724b6647aa94a2c865810c96846ec4b`),
the first trained on CMS's published peer group median cutoffs for FY2019 to FY2026 (ADR 0008).
Version 7 became the `champion` on 2026-10-28, replacing version 6, which was trained on the
1.0 stand-in of ADR 0006 (test AUC 0.874 on FY2026 and 0.678 on FY2027, the FY2026 figure
measured against the stand-in label).

## Intended use

Contoso asks which of its facilities and conditions will drive next year's HRRP penalty. The
model gives, for every hospital and HRRP condition (AMI, HF, PN, COPD, hip/knee, CABG), the
probability that the condition's excess readmission ratio (ERR) lands above the penalty cutoff
in the next fiscal year. The `score` task writes those probabilities for FY2028 to
`gold_penalty_predictions`, with a rank and decile per row and the model version.

Use it to rank where to look first: which conditions at which hospitals are most likely to
count toward next year's payment reduction. Do not use it to estimate the size of a penalty
(it predicts above or below the cutoff, not the ERR or the dollar amount), to judge a
hospital's quality of care, or for any decision about individual patients. It sees no patient
data.

## Data

One row per facility, condition and target fiscal year T from `gold_features`
(`docs/gold.md`). Features use only what CMS had published before FY T's results: lags 1 to 3
of the ERR, the above-cutoff flag and discharges; lag 1 of readmissions, predicted and expected
rates, the ERR change and the anomaly flag; counts and the mean and maximum ERR over all earlier
years; state, hospital type, ownership and emergency services; and county ACS measures from
vintage T-3. `acs_vintage` itself is left out, since as a calendar year it would stand in for
T. `tests/test_gold.py::test_features_never_use_the_target_year_or_later` guards the leakage
rule.

The label is `label_above_cutoff`. Rows with no ERR (CMS suppresses it under 25 discharges)
have no label and are left out.

| Split | Target years | Labelled rows | Share above cutoff | Cutoff |
|---|---|---|---|---|
| train | FY2014 to FY2023 | 135,581 | 47 to 50 percent | 1.0 to FY2018, published peer group median from FY2019 |
| validation | FY2024, FY2025 | 24,004 | 50.0 percent | published peer group median |
| test | FY2026 | 11,720 | 49.9 percent | published peer group median |
| test_exact | FY2027 | 14,575 | 49.9 percent | published peer group median |

The split is by target year, never random. FY2013 has no earlier year, so it has no history
features and is not used. From FY2019 CMS compares each hospital with the median ERR of its
dual-eligibility peer group, and the label uses the median CMS published for that year. Where
CMS published no median the label uses 1.0 (`label_cutoff_is_approximate = true`): every FY2023
pneumonia row (CMS left pneumonia out of that year's program; 2,951 labelled rows) and about 200
labelled rows a year for hospitals missing from a year's supplemental file (197 in FY2026, none in
FY2027). FY2027 is reported as a second test, the latest year and the one the FY2028 scores
follow: its features come from FY2026 and earlier, so no FY2027 label reaches training. The
names `test` and `test_exact` date from when FY2027 was the only year with an exact cutoff.

Class balance: 47 to 50 percent of the labelled rows are above the cutoff in every year, so
there is no resampling. Both models use `class_weight="balanced"`, which changes each class's
weight by under 5 percent, and 0.5 is the decision threshold.

## Models

| Candidate | What it is |
|---|---|
| `naive_lag1` | Last year's above-cutoff flag carried forward; the training prevalence when there is no FY T-1 ratio. No fitting. |
| `logistic_regression` | Median imputation with missing indicators, standardized numbers, one-hot categories (under 50 rows grouped), C = 1.0. |
| `lightgbm` | LightGBM 4.7.0: 400 trees, learning rate 0.05, 31 leaves, at least 100 rows per leaf, row and column subsampling 0.8, categories as LightGBM categoricals. |

The naive rule is strong because consecutive fiscal years share two of their three performance
years: FY T-1's ERR is already two thirds of FY T's claims. The models have to beat that, not
just chance.

The champion is the fitted model with the higher validation PR-AUC.

## Metrics

All three candidates, every split. Recall at top decile is the share of all above-cutoff rows
found in the 10 percent of rows with the highest scores; with about half the rows positive, the
most it can be is about 0.20.

| Model | Split | AUC | PR-AUC | Brier | Recall at top decile |
|---|---|---|---|---|---|
| naive_lag1 | validation | 0.760 | 0.699 | 0.240 | 0.152 |
| logistic_regression | validation | 0.855 | 0.855 | 0.156 | 0.192 |
| **lightgbm** | validation | **0.859** | **0.860** | **0.154** | **0.193** |
| naive_lag1 | test (FY2026) | 0.767 | 0.705 | 0.233 | 0.153 |
| logistic_regression | test (FY2026) | 0.863 | 0.864 | 0.151 | 0.194 |
| **lightgbm** | test (FY2026) | **0.868** | **0.872** | **0.148** | **0.196** |
| naive_lag1 | test_exact (FY2027) | 0.635 | 0.590 | 0.335 | 0.128 |
| logistic_regression | test_exact (FY2027) | 0.676 | 0.667 | 0.245 | 0.157 |
| **lightgbm** | test_exact (FY2027) | **0.679** | **0.667** | **0.246** | **0.155** |

LightGBM is the champion (validation PR-AUC 0.860 against 0.855). Its lead over logistic
regression is small on every split; the larger step is from the naive rule to either model
(test AUC 0.767 to 0.868). Version 6, trained and tested on the stand-in labels, scored 0.874 on
FY2026 and 0.678 on FY2027; version 7's FY2026 label follows CMS's rule, so the two FY2026
figures measure different targets.

**FY2028 scores.** Version 7 puts 8,641 of the 17,472 FY2028 pairs at 0.5 or more (version 6:
8,377), and 29 of Contoso's 72 (version 6: 28; the new one is CH05 Elm hip/knee, 0.489 to
0.537). Contoso's highest: CH11 Oak hip/knee 0.995, CH11 Oak heart failure 0.994, CH07
Hawthorn CABG 0.987, CH01 Alder CABG 0.985.

**Metric gate.** The `train` task fails, and leaves the `champion` alias where it was, if the
champion's test AUC is below the logistic regression's or below 0.80
(`readmission.ml.evaluate.AUC_FLOOR`). The floor sits above the naive rule's 0.77, so a model
that only restates last year's flag cannot pass. Version 7 passed at 0.868; the
gate's value and threshold go to `ops_dq_metrics` as `champion_test_auc`.

Each MLflow run also holds `calibration.png` (observed share against mean predicted probability
per tenth, for validation, test and test_exact), `feature_importance.csv`, `fairness.csv`,
`model_comparison.csv`, and for the two models the logged model with its signature and an
input example.

**Calibration.** On validation and test, LightGBM's probabilities track the observed rate
within a few points in every tenth. On FY2027 they are too confident at both ends: rows given
under 0.1 are above the cutoff 21 percent of the time (1,680 rows), and rows given over 0.9, 75
percent (2,033 rows).

**What the model uses.** By LightGBM gain, the FY T-1 ERR dominates, followed by the FY T-1
above-cutoff flag, the mean prior ERR, the last ERR change, state and the FY T-3 ERR. ACS
county measures contribute little (county population is the largest, 11th).

## Why FY2027 is harder

Every candidate, the naive rule included, loses about 0.13 to 0.19 AUC from FY2026 to FY2027.
FY2027's performance window is two years (07/2023 to 06/2025) and shares only one year with
FY2026's, against two for a normal pair of years, so the lag-1 ratio carries less of the target.
The cutoff no longer explains the gap: with each year's published median in the labels, FY2026
and FY2027 use the same rule, and the gap is as wide as it was with the stand-in. The model was
trained on years where the windows overlapped by two years. FY2028, which the `score` task predicts, follows FY2027, so its accuracy
is more likely to look like the FY2027 column than the FY2026 one.

## Fairness

Champion metrics by group, at the 0.5 threshold. Ownership groups collapse CMS's categories:
nonprofit is every "Voluntary non-profit" type, government every "Government" type, other is
physician-owned, tribal and unknown. Size is the hospital's eligible discharges in FY T-1 summed
over its conditions, with edges at 350 and 1,000 (near the FY2026 tertiles of 367 and 1,014);
unknown means no FY T-1 ratio for any condition.

FY2026 (test):

| Group | Rows | Observed rate | Mean predicted | AUC | True positive rate | False positive rate |
|---|---|---|---|---|---|---|
| nonprofit | 8,042 | 0.481 | 0.486 | 0.868 | 0.764 | 0.205 |
| proprietary | 2,093 | 0.565 | 0.586 | 0.865 | 0.844 | 0.299 |
| government | 1,441 | 0.514 | 0.489 | 0.869 | 0.757 | 0.185 |
| other or unknown | 144 | 0.451 | 0.437 | 0.869 | 0.723 | 0.203 |
| small (under 350) | 2,423 | 0.488 | 0.476 | 0.855 | 0.732 | 0.194 |
| medium (350 to 999) | 4,040 | 0.514 | 0.518 | 0.867 | 0.790 | 0.231 |
| large (1,000 and over) | 4,713 | 0.510 | 0.523 | 0.878 | 0.806 | 0.231 |
| unknown | 544 | 0.353 | 0.359 | 0.851 | 0.604 | 0.131 |

FY2027 (test_exact):

| Group | Rows | Observed rate | Mean predicted | AUC | True positive rate | False positive rate |
|---|---|---|---|---|---|---|
| nonprofit | 9,602 | 0.482 | 0.486 | 0.677 | 0.586 | 0.349 |
| proprietary | 2,786 | 0.575 | 0.565 | 0.683 | 0.707 | 0.453 |
| government | 2,015 | 0.476 | 0.491 | 0.666 | 0.582 | 0.351 |
| other or unknown | 172 | 0.471 | 0.372 | 0.653 | 0.272 | 0.176 |
| small (under 350) | 3,607 | 0.485 | 0.488 | 0.635 | 0.557 | 0.376 |
| medium (350 to 999) | 4,677 | 0.512 | 0.517 | 0.678 | 0.639 | 0.394 |
| large (1,000 and over) | 4,844 | 0.521 | 0.518 | 0.716 | 0.663 | 0.361 |
| unknown | 1,447 | 0.422 | 0.417 | 0.629 | 0.410 | 0.264 |

Ranking quality (AUC) is close across ownership groups in both years. The rates at 0.5 differ
mostly because the groups' base rates differ: proprietary hospitals are above the cutoff more
often, are flagged more often, and so have both the highest true and false positive rates.
Size matters more in FY2027, where small hospitals rank worst (AUC 0.635 against 0.716 for
large): fewer discharges make a noisier ERR. Hospitals with no prior ratio are flagged least
and their positives are missed most; the model has only their attributes and county to go on.
The "other or unknown" ownership group is small (144 and 172 rows), so its numbers are noisy.

## Limitations

- About 200 labels a year from FY2019, and every FY2023 pneumonia label, use 1.0 because CMS
  published no peer median for them (ADR 0008); every other label follows CMS's rule.
- FY2027 is weaker than FY2026 for every candidate, and FY2028 is likelier to look like FY2027
  (see Why FY2027 is harder).
- The registered model is the one trained on FY2014 to FY2023 and evaluated above; it is not
  refit on later years before scoring FY2028, so the metrics describe the model that scores.
  Refitting through FY2027 would use more recent data but leave it without a held-out year.
- Hospital attributes come from the current Hospital General Information snapshot, so a
  hospital that changed ownership is described by its current owner in every year.
- The job retrains on every run, which registers a new version each time; the alias moves only
  when the gate passes.
- Free Edition: model versions 1 and 2 are stuck in `PENDING_REGISTRATION` from a first run on
  MLflow 2.11.4, whose direct S3 upload the workspace denies. The job now installs
  mlflow-skinny 2.22.0 (see `resources/readmission_pipeline.yml`).
