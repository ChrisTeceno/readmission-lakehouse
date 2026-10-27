"""Penalty model training: logistic regression against LightGBM, tracked in MLflow.

    readmission-train [--catalog workspace] [--schema readmission]
        [--experiment /Shared/readmission-penalty] [--set-champion true|false]

The `train` task of the `readmission_pipeline` job, after `gold`. It reads gold_features, splits
it by target fiscal year (readmission.ml.dataset), and logs one MLflow run per candidate: the
naive rule (last year's flag carried forward), logistic regression and LightGBM. Each run holds
its parameters, AUC, PR-AUC, Brier score and recall in the top decile for validation, test and
test_exact, a calibration plot, and, for the two models, the feature importance, a fairness
table by ownership and size, and the model with its signature and an input example.

The champion is the model with the higher validation PR-AUC. It is registered in Unity Catalog
as `<catalog>.<schema>.penalty_model` with the alias `champion` only if it passes the metric
gate (readmission.ml.evaluate.check_gate); otherwise the task fails and the alias stays on the
previous version, so the `score` task never runs on a model that failed. `--set-champion false`
registers the version without moving the alias, for a retrain to review before it scores.
"""

import argparse
import os
import tempfile
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from sklearn.pipeline import Pipeline

from readmission.gold.features import TABLE as FEATURES_TABLE
from readmission.ml import dataset, evaluate, models
from readmission.ops import RunRecorder, add_ops_arguments, track_run

EXPERIMENT = "/Shared/readmission-penalty"
MODEL = "penalty_model"
CHAMPION_ALIAS = "champion"
NAIVE = "naive_lag1"
EVAL_SPLITS = ["validation", "test", "test_exact"]
OWNERSHIP_GROUP = "_ownership_group"
SIZE_BUCKET = "_size_bucket"
# The versions serverless environment 2 ships, and LightGBM as the job installs it; the same
# pins as the `ml` dependency group in pyproject.toml.
PIP_REQUIREMENTS = [
    "scikit-learn==1.3.0",
    "lightgbm==4.7.0",
    "pandas==1.5.3",
    "numpy==1.23.5",
    "cloudpickle==3.0.0",
]


@dataclass
class Candidate:
    name: str
    pipeline: Pipeline | None
    probability: dict[str, np.ndarray]
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)


def with_groups(features: pd.DataFrame) -> pd.DataFrame:
    """Adds the fairness groups before the split, since a hospital's size sums the FY T-1
    discharges of all its conditions, including those with no label in FY T."""
    return features.assign(
        **{
            OWNERSHIP_GROUP: dataset.ownership_group(features["hospital_ownership"]),
            SIZE_BUCKET: dataset.size_bucket(features),
        }
    )


def fit_candidates(
    splits: dict[str, pd.DataFrame], names: tuple[str, ...] = tuple(models.CANDIDATES)
) -> list[Candidate]:
    train = splits["train"]
    x_train, y_train = dataset.model_input(train), dataset.target(train)
    prevalence = float(y_train.mean())
    candidates = [
        Candidate(
            NAIVE,
            None,
            {s: evaluate.naive_probability(splits[s], prevalence) for s in EVAL_SPLITS},
        )
    ]
    for name in names:
        pipeline = models.fit(name, x_train, y_train)
        probability = {
            s: pipeline.predict_proba(dataset.model_input(splits[s]))[:, 1] for s in EVAL_SPLITS
        }
        candidates.append(Candidate(name, pipeline, probability))
    for candidate in candidates:
        candidate.metrics = {
            s: evaluate.classification_metrics(dataset.target(splits[s]), candidate.probability[s])
            for s in EVAL_SPLITS
        }
    return candidates


def choose_champion(candidates: list[Candidate]) -> Candidate:
    fitted = [c for c in candidates if c.pipeline is not None]
    return max(fitted, key=lambda c: c.metrics["validation"]["pr_auc"])


def metrics_table(candidates: list[Candidate]) -> pd.DataFrame:
    rows = [{"model": c.name, "split": s, **c.metrics[s]} for c in candidates for s in EVAL_SPLITS]
    return pd.DataFrame(rows)


def fairness_table(candidate: Candidate, splits: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    for s in ["test", "test_exact"]:
        for dimension in [OWNERSHIP_GROUP, SIZE_BUCKET]:
            table = evaluate.group_metrics(
                dataset.target(splits[s]), candidate.probability[s], splits[s][dimension]
            )
            parts.append(table.assign(split=s, dimension=dimension.strip("_")))
    return pd.concat(parts, ignore_index=True)


def calibration_figure(candidate: Candidate, splits: dict[str, pd.DataFrame]):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    figure, axis = plt.subplots(figsize=(6, 6))
    axis.plot([0, 1], [0, 1], linestyle="--", color="grey", label="perfectly calibrated")
    for s in EVAL_SPLITS:
        table = evaluate.calibration_table(dataset.target(splits[s]), candidate.probability[s])
        axis.plot(table["mean_predicted"], table["observed_rate"], marker="o", label=s)
    axis.set_xlabel("Mean predicted probability")
    axis.set_ylabel("Observed share above the cutoff")
    axis.set_title(f"Calibration: {candidate.name}")
    axis.legend()
    return figure


def log_csv(frame: pd.DataFrame, name: str) -> None:
    import mlflow

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, name)
        frame.to_csv(path, index=False)
        mlflow.log_artifact(path)


def log_candidate(
    candidate: Candidate,
    splits: dict[str, pd.DataFrame],
    job_run_id: str,
    comparison: pd.DataFrame,
) -> str:
    import mlflow
    import mlflow.sklearn
    from mlflow.models import infer_signature

    with mlflow.start_run(run_name=candidate.name) as mlflow_run:
        mlflow.set_tags({"candidate": candidate.name, "job_run_id": job_run_id})
        mlflow.log_params(
            {
                "features_table": FEATURES_TABLE,
                "label": dataset.TARGET,
                **{
                    f"{s}_target_years": f"{min(years)}-{max(years)}"
                    for s, years in dataset.SPLITS.items()
                },
                **{f"{s}_rows": len(splits[s]) for s in dataset.SPLITS},
            }
        )
        for s in EVAL_SPLITS:
            mlflow.log_metrics({f"{s}_{k}": v for k, v in candidate.metrics[s].items()})
        mlflow.log_figure(calibration_figure(candidate, splits), "calibration.png")
        log_csv(comparison, "model_comparison.csv")
        if candidate.pipeline is None:
            mlflow.log_params({"rule": "above_cutoff_lag1, training prevalence when null"})
            return mlflow_run.info.run_id
        mlflow.log_params(
            {
                "class_weight": "balanced",
                "categorical_features": ",".join(dataset.CATEGORICAL_FEATURES),
                "numeric_features": len(dataset.NUMERIC_FEATURES),
                **models.PARAMS[candidate.name],
            }
        )
        log_csv(models.feature_importance(candidate.pipeline), "feature_importance.csv")
        log_csv(fairness_table(candidate, splits), "fairness.csv")
        example = dataset.model_input(splits["train"].head(5))
        mlflow.sklearn.log_model(
            candidate.pipeline,
            "model",
            signature=infer_signature(example, candidate.pipeline.predict_proba(example)),
            input_example=example,
            pyfunc_predict_fn="predict_proba",
            pip_requirements=PIP_REQUIREMENTS,
        )
        return mlflow_run.info.run_id


def register(model_name: str, run_id: str, champion: Candidate, set_champion: bool = True) -> str:
    import mlflow
    from mlflow import MlflowClient

    version = mlflow.register_model(f"runs:/{run_id}/model", model_name).version
    client = MlflowClient()
    for s in EVAL_SPLITS:
        for metric in ["auc", "pr_auc"]:
            client.set_model_version_tag(
                model_name, version, f"{s}_{metric}", f"{champion.metrics[s][metric]:.4f}"
            )
    client.set_model_version_tag(model_name, version, "candidate", champion.name)
    if set_champion:
        client.set_registered_model_alias(model_name, CHAMPION_ALIAS, version)
    return version


def train(
    spark: SparkSession,
    prefix: str,
    experiment: str,
    job_run_id: str,
    run: RunRecorder,
    set_champion: bool = True,
) -> None:
    import mlflow

    first, last = min(dataset.SPLITS["train"]), max(dataset.SPLITS["test_exact"])
    features = (
        spark.table(f"{prefix}.{FEATURES_TABLE}")
        .where(F.col("target_fiscal_year").between(first, last))
        .toPandas()
    )
    splits = dataset.split(with_groups(features))
    for s, rows in splits.items():
        print(f"{s}: {len(rows)} labelled rows")
    candidates = fit_candidates(splits)
    comparison = metrics_table(candidates)
    print(comparison.to_string(index=False))

    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(experiment)
    run_ids = {c.name: log_candidate(c, splits, job_run_id, comparison) for c in candidates}

    model_name = f"{prefix}.{MODEL}"
    champion = choose_champion(candidates)
    baseline = next(c for c in candidates if c.name == "logistic_regression")
    for c in candidates:
        for s in EVAL_SPLITS:
            run.metric(model_name, f"{c.name}_{s}_auc", c.metrics[s]["auc"])
    champion_auc = champion.metrics["test"]["auc"]
    run.check(
        model_name,
        "champion_test_auc",
        champion_auc,
        max(evaluate.AUC_FLOOR, baseline.metrics["test"]["auc"]),
        lambda: evaluate.check_gate(champion_auc, baseline.metrics["test"]["auc"]),
    )
    version = register(model_name, run_ids[champion.name], champion, set_champion)
    if set_champion:
        print(f"{model_name}: version {version} ({champion.name}) is @{CHAMPION_ALIAS}")
    else:
        print(f"{model_name}: version {version} ({champion.name}) registered, alias not moved")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train and register the penalty model.")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--schema", default="readmission")
    parser.add_argument("--experiment", default=EXPERIMENT)
    parser.add_argument(
        "--set-champion",
        choices=["true", "false"],
        default="true",
        help=f"false: register the version without moving @{CHAMPION_ALIAS}",
    )
    add_ops_arguments(parser)
    args = parser.parse_args(argv)
    with track_run("train", args) as run:
        spark = SparkSession.builder.getOrCreate()
        train(
            spark,
            f"{args.catalog}.{args.schema}",
            args.experiment,
            args.job_run_id,
            run,
            args.set_champion == "true",
        )


if __name__ == "__main__":
    main()
