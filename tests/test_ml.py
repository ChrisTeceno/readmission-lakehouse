from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from readmission.gold.features import FEATURE_COLUMNS, KEY, LABEL_COLUMNS
from readmission.ml import dataset, evaluate, score, train

MEASURES = ["AMI", "HF", "PN"]


@pytest.fixture(scope="module")
def features():
    """Gold feature rows for 40 hospitals, three conditions, target years FY2013 to FY2028. The
    label follows last year's ERR plus noise; FY2028 rows have no label, as in gold_features,
    and one condition a year has its ERR suppressed."""
    rng = np.random.default_rng(7)
    rows = []
    for h in range(40):
        ownership = ["Proprietary", "Voluntary non-profit - Private", "Government - Local"][h % 3]
        for measure in MEASURES:
            for year in range(2013, 2029):
                err_lag1 = None if year == 2013 else 1 + rng.normal(0, 0.05)
                err = (err_lag1 or 1.0) + rng.normal(0, 0.03)
                suppressed = year < 2028 and h == 0 and measure == "AMI"
                label = None if year == 2028 or suppressed else err > 1.0
                row = {c: None for c in FEATURE_COLUMNS}
                row.update(
                    facility_id=f"{h:06d}",
                    measure=measure,
                    target_fiscal_year=year,
                    err_lag1=err_lag1,
                    above_cutoff_lag1=None if err_lag1 is None else err_lag1 > 1.0,
                    discharges_lag1=None if err_lag1 is None else 100 * (h % 5 + 1),
                    years_observed=year - 2013,
                    state=["AL", "TX"][h % 2],
                    hospital_ownership=ownership,
                    emergency_services=h % 4 != 0,
                    acs_vintage=year - 3,
                    acs_pct_below_poverty=float(h % 7),
                    label_above_cutoff=label,
                    label_err=None if label is None else err,
                    label_cutoff_is_approximate=None if label is None else 2019 <= year <= 2026,
                )
                rows.append(row)
    return pd.DataFrame(rows)[[*KEY, *FEATURE_COLUMNS, *LABEL_COLUMNS]]


@pytest.fixture(scope="module")
def splits(features):
    return dataset.split(train.with_groups(features))


def test_split_is_by_target_year_and_drops_unlabelled_rows(splits):
    years = {s: set(rows["target_fiscal_year"]) for s, rows in splits.items()}
    assert years == {
        "train": set(range(2014, 2024)),
        "validation": {2024, 2025},
        "test": {2026},
        "test_exact": {2027},
    }
    # No training target is as late as a validation or test target, and FY2013 (no history)
    # and the FY2028 scoring rows are in no split.
    assert max(years["train"]) < min(years["validation"]) < min(years["test"])
    for rows in splits.values():
        assert rows[dataset.TARGET].notna().all()
        assert len(rows) == (40 * 3 - 1) * rows["target_fiscal_year"].nunique()


def test_model_features_leave_out_labels_and_the_acs_vintage():
    left_out = [*LABEL_COLUMNS, "facility_id", "target_fiscal_year", "acs_vintage"]
    assert set(dataset.MODEL_FEATURES).isdisjoint(left_out)
    assert set(dataset.MODEL_FEATURES) | {"acs_vintage"} == set(FEATURE_COLUMNS) | {"measure"}


def test_model_input_types(features):
    x = dataset.model_input(features.head(3).assign(state=[None, "AL", "TX"]))
    assert list(x.columns) == dataset.MODEL_FEATURES
    assert list(x["state"]) == ["missing", "AL", "TX"]
    assert x["emergency_services"].dtype == float
    assert x["above_cutoff_lag1"].isna().tolist() == [True, False, False]
    assert all(x[c].dtype == float for c in dataset.NUMERIC_FEATURES)


def test_ownership_groups():
    ownership = pd.Series(
        ["Voluntary non-profit - Church", "Government - State", "Proprietary", "Tribal", None]
    )
    assert list(dataset.ownership_group(ownership)) == [
        "nonprofit",
        "government",
        "proprietary",
        "other or unknown",
        "other or unknown",
    ]


def test_size_bucket_sums_a_hospitals_conditions():
    frame = pd.DataFrame(
        {
            "facility_id": ["a", "a", "b", "c", "c", "d"],
            "target_fiscal_year": [2026] * 6,
            "discharges_lag1": [200, 149, 350, 600, 400, None],
        }
    )
    assert list(dataset.size_bucket(frame)) == [
        "small (under 350)",
        "small (under 350)",
        "medium (350 to 999)",
        "large (1,000 and over)",
        "large (1,000 and over)",
        "unknown (no FY T-1 discharges)",
    ]


def test_recall_at_top_counts_ties_in_proportion():
    y = np.array([1, 0, 1, 0, 0, 0, 0, 0, 0, 1] * 2)
    # The top 10 percent is 2 rows. A perfect score puts 2 of the 6 positives there.
    perfect = y + np.linspace(0, 0.5, 20)
    assert evaluate.recall_at_top(y, perfect) == pytest.approx(2 / 6)
    # A constant score ties every row: 2 of 20 rows, 30 percent positive, so 0.6 of 6.
    assert evaluate.recall_at_top(y, np.zeros(20)) == pytest.approx(0.6 / 6)


def test_naive_probability_uses_prevalence_when_last_year_is_missing():
    frame = pd.DataFrame({"above_cutoff_lag1": [True, False, None]})
    assert list(evaluate.naive_probability(frame, 0.48)) == [1.0, 0.0, 0.48]


def test_gate():
    assert evaluate.gate_failures(0.87, 0.86) == []
    failures = evaluate.gate_failures(0.74, 0.76)
    assert len(failures) == 2
    assert "below the logistic regression's 0.7600" in failures[0]
    assert f"below the floor {evaluate.AUC_FLOOR}" in failures[1]
    with pytest.raises(evaluate.GateFailed):
        evaluate.check_gate(0.70, 0.60)


def test_calibration_table():
    table = evaluate.calibration_table(np.array([0, 1, 1, 1]), np.array([0.05, 0.15, 0.95, 1.0]))
    assert list(table["bin"]) == [0, 1, 9]
    assert list(table["rows"]) == [1, 1, 2]
    assert list(table["observed_rate"]) == [0.0, 1.0, 1.0]


def test_fit_candidates_and_champion(splits):
    candidates = train.fit_candidates(splits, names=("logistic_regression",))
    assert [c.name for c in candidates] == [train.NAIVE, "logistic_regression"]
    model = candidates[1]
    for s in train.EVAL_SPLITS:
        assert len(model.probability[s]) == len(splits[s])
        assert set(model.metrics[s]) == {
            "rows",
            "prevalence",
            "auc",
            "pr_auc",
            "brier",
            "recall_top_decile",
        }
    # The label follows err_lag1, so the model ranks well above chance.
    assert model.metrics["test"]["auc"] > 0.7
    assert train.choose_champion(candidates) is model
    table = train.metrics_table(candidates)
    assert len(table) == 2 * len(train.EVAL_SPLITS)
    fairness = train.fairness_table(model, splits)
    assert set(fairness["dimension"]) == {"ownership_group", "size_bucket"}
    assert set(fairness["split"]) == {"test", "test_exact"}
    assert fairness.groupby(["split", "dimension"])["rows"].sum().unique().tolist() == [
        len(splits["test"])
    ]


def test_predictions(features):
    rows = features[features["target_fiscal_year"] == 2028].head(20).reset_index(drop=True)
    probability = np.linspace(0.95, 0.0, 20)
    scored_at = datetime(2026, 10, 21, tzinfo=UTC)
    frame = score.predictions(rows, probability, "c.s.penalty_model", 3, "run1", scored_at)
    assert list(frame.columns) == score.GOLD_PENALTY_PREDICTIONS_SCHEMA.fieldNames()
    assert list(frame["risk_rank"]) == list(range(1, 21))
    assert list(frame["risk_decile"]) == [d for d in range(1, 11) for _ in range(2)]
    assert frame["predicted_above_cutoff"].sum() == 10
    assert set(frame["model_version"]) == {"3"}
    assert frame["above_cutoff_lag1"].tolist() == rows["above_cutoff_lag1"].tolist()


def test_predictions_fit_the_table_schema(spark, features):
    rows = features[features["target_fiscal_year"] == 2028].head(3)
    frame = score.predictions(
        rows, [0.2, 0.6, 0.9], "c.s.penalty_model", "1", "run1", datetime.now(UTC)
    )
    df = spark.createDataFrame(frame, score.GOLD_PENALTY_PREDICTIONS_SCHEMA)
    assert df.count() == 3
