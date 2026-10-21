# Model card: HRRP penalty model

`workspace.readmission.penalty_model`, alias `champion`. Code: `src/readmission/ml/`. MLflow
experiment: `/Shared/readmission-penalty`. Numbers below are from the 2026-10-21 job run
303222482315557 (model version 4, LightGBM, MLflow run `17d90d5cf89543f7b0e286cfb930ff4d`);
the earlier runs that day (versions 3 and back) trained on the same data with the same seeds
and logged the same metrics.

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
| train | FY2014 to FY2023 | 135,581 | 48 to 49 percent | 1.0; exact to FY2018, stand-in from FY2019 |
| validation | FY2024, FY2025 | 24,004 | 48.6 percent | 1.0 stand-in |
| test | FY2026 | 11,720 | 48.1 percent | 1.0 stand-in |
| test_exact | FY2027 | 14,575 | 49.9 percent | published peer group median |

The split is by target year, never random. FY2013 has no earlier year, so it has no history
features and is not used. From FY2019 CMS compares each hospital with the median ERR of its
dual-eligibility peer group, which no file before FY2027 carries, so FY2019 to FY2026 labels
use 1.0 as a stand-in (`label_cutoff_is_approximate = true`). The test year FY2026 is one of
those years, so its metrics measure the model against an approximate label. FY2027 is the only
recent year with the real cutoff, and it is reported as a second test: its features come from
FY2026 and earlier, so no FY2027 label reaches training.

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
most it can be is about 0.21 (0.208 on test, 0.200 on test_exact).

| Model | Split | AUC | PR-AUC | Brier | Recall at top decile |
|---|---|---|---|---|---|
| naive_lag1 | validation | 0.762 | 0.691 | 0.238 | 0.156 |
| logistic_regression | validation | 0.859 | 0.852 | 0.154 | 0.197 |
| **lightgbm** | validation | **0.863** | **0.858** | **0.151** | **0.199** |
| naive_lag1 | test (FY2026, approximate cutoff) | 0.772 | 0.697 | 0.228 | 0.158 |
| logistic_regression | test (FY2026, approximate cutoff) | 0.869 | 0.862 | 0.147 | 0.200 |
| **lightgbm** | test (FY2026, approximate cutoff) | **0.874** | **0.871** | **0.145** | **0.203** |
| naive_lag1 | test_exact (FY2027, exact cutoff) | 0.633 | 0.589 | 0.337 | 0.129 |
| logistic_regression | test_exact (FY2027, exact cutoff) | 0.675 | 0.666 | 0.247 | 0.156 |
| **lightgbm** | test_exact (FY2027, exact cutoff) | **0.678** | **0.668** | **0.247** | **0.156** |

LightGBM is the champion (validation PR-AUC 0.858 against 0.852). Its lead over logistic
regression is small on every split; the larger step is from the naive rule to either model
(test AUC 0.772 to 0.874).

**Metric gate.** The `train` task fails, and leaves the `champion` alias where it was, if the
champion's test AUC is below the logistic regression's or below 0.80
(`readmission.ml.evaluate.AUC_FLOOR`). The floor sits above the naive rule's 0.77, so a model
that only restates last year's flag cannot pass. The 2026-10-21 champion passed at 0.874; the
gate's value and threshold go to `ops_dq_metrics` as `champion_test_auc`.

Each MLflow run also holds `calibration.png` (observed share against mean predicted probability
per tenth, for validation, test and test_exact), `feature_importance.csv`, `fairness.csv`,
`model_comparison.csv`, and for the two models the logged model with its signature and an
input example.

**Calibration.** On validation and test, LightGBM's probabilities track the observed rate
within a few points in every tenth. On FY2027 they are too confident at both ends: rows given
under 0.1 are above the cutoff 22 percent of the time, and rows given over 0.9, 76 percent.

**What the model uses.** By LightGBM gain, the FY T-1 ERR dominates, followed by the FY T-1
above-cutoff flag, the mean prior ERR, the last ERR change, state and the FY T-3 ERR. ACS
county measures contribute little (county population is the largest, 11th).

## Why FY2027 is harder

Every candidate, the naive rule included, loses about 0.1 to 0.2 AUC from FY2026 to FY2027. Two
things change at once. FY2027's performance window is two years (07/2023 to 06/2025) and shares
only one year with FY2026's, against two for a normal pair of years, so the lag-1 ratio carries
less of the target. And FY2027's cutoff is the peer group median, not 1.0: 6.2 percent of
FY2027 rows land on different sides of the two (`docs/gold.md`). The model was trained on years
where neither held. FY2028, which the `score` task predicts, follows FY2027, so its accuracy
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
| nonprofit | 8,042 | 0.461 | 0.470 | 0.873 | 0.760 | 0.196 |
| proprietary | 2,093 | 0.549 | 0.575 | 0.876 | 0.848 | 0.285 |
| government | 1,441 | 0.500 | 0.478 | 0.873 | 0.752 | 0.188 |
| other or unknown | 144 | 0.438 | 0.424 | 0.867 | 0.667 | 0.160 |
| small (under 350) | 2,423 | 0.480 | 0.470 | 0.866 | 0.745 | 0.190 |
| medium (350 to 999) | 4,040 | 0.497 | 0.502 | 0.874 | 0.786 | 0.218 |
| large (1,000 and over) | 4,713 | 0.485 | 0.505 | 0.880 | 0.798 | 0.223 |
| unknown | 544 | 0.340 | 0.346 | 0.860 | 0.595 | 0.117 |

FY2027 (test_exact):

| Group | Rows | Observed rate | Mean predicted | AUC | True positive rate | False positive rate |
|---|---|---|---|---|---|---|
| nonprofit | 9,602 | 0.482 | 0.469 | 0.677 | 0.556 | 0.326 |
| proprietary | 2,786 | 0.575 | 0.551 | 0.678 | 0.677 | 0.439 |
| government | 2,015 | 0.476 | 0.484 | 0.665 | 0.569 | 0.343 |
| other or unknown | 172 | 0.471 | 0.364 | 0.651 | 0.222 | 0.187 |
| small (under 350) | 3,607 | 0.485 | 0.482 | 0.634 | 0.537 | 0.365 |
| medium (350 to 999) | 4,677 | 0.512 | 0.500 | 0.678 | 0.616 | 0.370 |
| large (1,000 and over) | 4,844 | 0.521 | 0.498 | 0.715 | 0.626 | 0.336 |
| unknown | 1,447 | 0.422 | 0.409 | 0.625 | 0.379 | 0.256 |

Ranking quality (AUC) is close across ownership groups in both years. The rates at 0.5 differ
mostly because the groups' base rates differ: proprietary hospitals are above the cutoff more
often, are flagged more often, and so have both the highest true and false positive rates.
Size matters more in FY2027, where small hospitals rank worst (AUC 0.634 against 0.715 for
large): fewer discharges make a noisier ERR. Hospitals with no prior ratio are flagged least
and their positives are missed most; the model has only their attributes and county to go on.
The "other or unknown" ownership group is small (144 and 172 rows), so its numbers are noisy.

## Limitations

- Labels for FY2019 to FY2026, including training, validation and test, use the 1.0 stand-in
  cutoff. The model learns "ERR above 1.0", which is close to but not the rule CMS applied, and
  FY2027 shows the cost.
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
