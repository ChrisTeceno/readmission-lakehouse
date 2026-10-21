"""The two penalty model candidates, as scikit-learn pipelines over `dataset.model_input`.

Positives are 47 to 50 percent of the labelled rows in every target year (FY2014 to FY2027),
so there is no resampling. Both models still weight classes inversely to their frequency
(`class_weight="balanced"`), which moves each weight by under 5 percent; it keeps the choice
explicit if a later year is less even.

The pipelines hold only scikit-learn and LightGBM objects, so the registered model loads
without this package installed.
"""

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from readmission.ml.dataset import CATEGORICAL_FEATURES, NUMERIC_FEATURES

RANDOM_STATE = 2028

LOGISTIC_REGRESSION_PARAMS = {"C": 1.0, "max_iter": 2000, "class_weight": "balanced"}
LIGHTGBM_PARAMS = {
    "n_estimators": 400,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 100,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "class_weight": "balanced",
}


def logistic_regression() -> Pipeline:
    """Missing numbers take the training median plus an indicator column, so "no ratio last
    year" stays visible to a linear model; rare categories (under 50 rows) share one column."""
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]
    )
    categorical = OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=50)
    prepare = ColumnTransformer(
        [("num", numeric, NUMERIC_FEATURES), ("cat", categorical, CATEGORICAL_FEATURES)]
    )
    model = LogisticRegression(random_state=RANDOM_STATE, **LOGISTIC_REGRESSION_PARAMS)
    return Pipeline([("prepare", prepare), ("model", model)])


def lightgbm() -> Pipeline:
    """LightGBM splits on missing values itself, so numbers pass through unimputed. Categories
    become integer codes it treats as categorical; a category unseen in training is -1."""
    from lightgbm import LGBMClassifier

    categorical = OrdinalEncoder(
        handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1
    )
    prepare = ColumnTransformer(
        [("cat", categorical, CATEGORICAL_FEATURES), ("num", "passthrough", NUMERIC_FEATURES)],
        verbose_feature_names_out=False,
    )
    model = LGBMClassifier(random_state=RANDOM_STATE, verbose=-1, **LIGHTGBM_PARAMS)
    return Pipeline([("prepare", prepare), ("model", model)])


CANDIDATES = {"logistic_regression": logistic_regression, "lightgbm": lightgbm}
PARAMS = {"logistic_regression": LOGISTIC_REGRESSION_PARAMS, "lightgbm": LIGHTGBM_PARAMS}


def fit(candidate: str, x: pd.DataFrame, y: pd.Series) -> Pipeline:
    pipeline = CANDIDATES[candidate]()
    fit_params = {}
    if candidate == "lightgbm":
        # The ordinal-encoded categories are the first columns `prepare` emits.
        fit_params["model__categorical_feature"] = list(range(len(CATEGORICAL_FEATURES)))
    return pipeline.fit(x, y, **fit_params)


def feature_importance(pipeline: Pipeline) -> pd.DataFrame:
    """Logistic regression: the absolute coefficient on the standardized column. LightGBM: the
    total gain of the splits on the feature. Sorted, largest first."""
    names = pipeline.named_steps["prepare"].get_feature_names_out()
    model = pipeline.named_steps["model"]
    if isinstance(model, LogisticRegression):
        importance = np.abs(model.coef_[0])
        kind = "abs_coefficient"
    else:
        importance = model.booster_.feature_importance(importance_type="gain")
        kind = "gain"
    frame = pd.DataFrame({"feature": names, "importance": importance, "kind": kind})
    return frame.sort_values("importance", ascending=False).reset_index(drop=True)
