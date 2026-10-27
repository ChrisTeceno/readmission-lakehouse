"""Penalty model data: the time-based split of gold_features and the model's input matrix.

The label is `label_above_cutoff`: the hospital's ERR for the condition is above the penalty
cutoff in target fiscal year T. Every feature already follows the Gold leakage rule (fiscal
years before T only, see readmission.gold.features), so the split only has to keep later target
years out of training:

- train: targets FY2014 to FY2023. FY2013 has no earlier HRRP year, so none of its history
  features exist.
- validation: FY2024 and FY2025, which pick the champion.
- test: FY2026, the held-out year the metric gate reads. Its cutoff, like every target's from
  FY2019, is the published peer group median.
- test_exact: FY2027, a second check. The name dates from when it was the only year with a
  published median; FY2027's window is two years and shares only one with FY2026's.
  Its features come from FY2026 and earlier, so it shares no label with training.

Rows with a null label (an ERR CMS suppressed for under 25 discharges) are left out.
"""

import pandas as pd

from readmission.gold.features import FEATURE_COLUMNS

TARGET = "label_above_cutoff"
SPLITS = {
    "train": range(2014, 2024),
    "validation": range(2024, 2026),
    "test": range(2026, 2027),
    "test_exact": range(2027, 2028),
}

CATEGORICAL_FEATURES = ["measure", "state", "hospital_type", "hospital_ownership"]
# `acs_vintage` says which ACS release the acs_* columns come from. It is a calendar year, so
# as a feature it would only stand in for the target year.
NUMERIC_FEATURES = [c for c in FEATURE_COLUMNS if c not in CATEGORICAL_FEATURES + ["acs_vintage"]]
MODEL_FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES
MISSING_CATEGORY = "missing"

# Hospital size for the fairness check: the hospital's eligible discharges in FY T-1 summed
# over its conditions. The edges sit near the FY2026 tertiles (367 and 1,014).
SIZE_EDGES = [350, 1000]
SIZE_BUCKETS = ["small (under 350)", "medium (350 to 999)", "large (1,000 and over)"]
SIZE_UNKNOWN = "unknown (no FY T-1 discharges)"


def labelled(features: pd.DataFrame) -> pd.DataFrame:
    return features[features[TARGET].notna()]


def split(features: pd.DataFrame) -> dict[str, pd.DataFrame]:
    rows = labelled(features)
    return {
        name: rows[rows["target_fiscal_year"].isin(years)].reset_index(drop=True)
        for name, years in SPLITS.items()
    }


def model_input(features: pd.DataFrame) -> pd.DataFrame:
    """The columns the model reads, with stable types: categories as strings with nulls as
    "missing", everything else as float with NaN, so a batch with no nulls in an integer column
    has the same signature as one with nulls."""
    x = pd.DataFrame(index=features.index)
    for name in CATEGORICAL_FEATURES:
        x[name] = features[name].astype(object).where(features[name].notna(), MISSING_CATEGORY)
        x[name] = x[name].astype(str)
    for name in NUMERIC_FEATURES:
        x[name] = pd.to_numeric(features[name].astype(object), errors="coerce").astype(float)
    return x


def target(features: pd.DataFrame) -> pd.Series:
    return features[TARGET].astype(bool).astype(int)


def ownership_group(ownership: pd.Series) -> pd.Series:
    """CMS's eleven ownership categories, collapsed to four groups large enough to compare."""

    def group(value) -> str:
        if not isinstance(value, str):
            return "other or unknown"
        if value.startswith("Voluntary non-profit"):
            return "nonprofit"
        if value.startswith("Government"):
            return "government"
        if value == "Proprietary":
            return "proprietary"
        return "other or unknown"

    return ownership.map(group)


def size_bucket(features: pd.DataFrame) -> pd.Series:
    """Rows of `features` keep their index; the total is over the hospital's rows for the same
    target year, so it needs every condition of the hospital in `features`."""
    total = features.groupby(["facility_id", "target_fiscal_year"])["discharges_lag1"].transform(
        lambda d: d.sum(min_count=1)
    )
    buckets = pd.cut(total, [-float("inf"), *SIZE_EDGES, float("inf")], right=False)
    labels = dict(zip(buckets.cat.categories, SIZE_BUCKETS, strict=True))
    return buckets.map(labels).astype(object).where(total.notna(), SIZE_UNKNOWN)
