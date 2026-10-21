"""Batch scoring: the champion penalty model's predictions for the next fiscal year.

    readmission-score [--catalog workspace] [--schema readmission]

The `score` task of the `readmission_pipeline` job, after `train`. It loads
`models:/<catalog>.<schema>.penalty_model@champion`, scores the gold_features rows of the
next cycle (the latest target year, whose labels are null: FY2028 from data through FY2027) and
rebuilds gold_penalty_predictions with the model version that produced each row.
"""

import argparse
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.gold.build import write_table
from readmission.gold.features import TABLE as FEATURES_TABLE
from readmission.ml import dataset
from readmission.ml.train import CHAMPION_ALIAS, MODEL
from readmission.ops import RunRecorder, add_ops_arguments, track_run
from readmission.silver.governance import TableDoc, field, gold_tags

TABLE = "gold_penalty_predictions"
# Classes are close to even and both models weight them evenly, so 0.5 is the decision point.
THRESHOLD = 0.5

GOLD_PENALTY_PREDICTIONS_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field(
            "measure",
            T.StringType(),
            "HRRP condition: AMI, HF, PN, COPD, HIP_KNEE or CABG",
            nullable=False,
        ),
        field(
            "target_fiscal_year",
            T.IntegerType(),
            "Fiscal year predicted, from data published before it",
            nullable=False,
        ),
        field(
            "penalty_probability",
            T.DoubleType(),
            "Predicted probability that the ERR is above the cutoff in the target year",
            nullable=False,
        ),
        field(
            "predicted_above_cutoff",
            T.BooleanType(),
            "penalty_probability is at least 0.5",
            nullable=False,
        ),
        field(
            "risk_rank",
            T.IntegerType(),
            "Rank by penalty_probability within the target year, 1 is the highest",
            nullable=False,
        ),
        field(
            "risk_decile",
            T.IntegerType(),
            "Decile of penalty_probability within the target year, 1 is the top 10 percent",
            nullable=False,
        ),
        field(
            "above_cutoff_lag1",
            T.BooleanType(),
            "Whether the ERR was above the cutoff in the year before, the naive prediction",
        ),
        field(
            "model_name", T.StringType(), "Unity Catalog model that scored the row", nullable=False
        ),
        field("model_version", T.StringType(), "Version of model_name used", nullable=False),
        field(
            "model_run_id", T.StringType(), "MLflow run that trained the version", nullable=False
        ),
        field("scored_at", T.TimestampType(), "When the row was scored (UTC)", nullable=False),
    ]
)

GOLD_PENALTY_PREDICTIONS_DOC = TableDoc(
    comment=(
        "Penalty model predictions for the next HRRP fiscal year, one row per facility and "
        "condition: the champion model's probability that the condition's ERR lands above the "
        "penalty cutoff, its rank and decile, and the model version. Rebuilt by the score task "
        "from gold_features with the version of penalty_model that holds the champion alias. "
        "See docs/model-card.md for the model's metrics and limits."
    ),
    tags=gold_tags("cms,census", dataset="9n3s-kdb3,ipps-supplemental,xubh-q36u,acs5"),
)


def predictions(
    features: pd.DataFrame,
    probability: np.ndarray,
    model_name: str,
    model_version: str,
    model_run_id: str,
    scored_at: datetime,
) -> pd.DataFrame:
    frame = features[["facility_id", "measure", "target_fiscal_year"]].reset_index(drop=True)
    frame["penalty_probability"] = np.asarray(probability, dtype=float)
    frame["predicted_above_cutoff"] = frame["penalty_probability"] >= THRESHOLD
    ranks = frame.groupby("target_fiscal_year")["penalty_probability"]
    frame["risk_rank"] = ranks.rank(method="first", ascending=False).astype(int)
    share = ranks.rank(method="first", ascending=False, pct=True)
    frame["risk_decile"] = np.ceil(share * 10).clip(1, 10).astype(int)
    lag1 = features["above_cutoff_lag1"].reset_index(drop=True).astype(object)
    frame["above_cutoff_lag1"] = lag1.where(lag1.notna(), None)
    frame["model_name"] = model_name
    frame["model_version"] = str(model_version)
    frame["model_run_id"] = model_run_id
    frame["scored_at"] = scored_at
    return frame[GOLD_PENALTY_PREDICTIONS_SCHEMA.fieldNames()]


def score(spark: SparkSession, prefix: str, run: RunRecorder) -> None:
    import mlflow
    import mlflow.sklearn
    from mlflow import MlflowClient

    features = spark.table(f"{prefix}.{FEATURES_TABLE}")
    next_year = features.where(F.col(dataset.TARGET).isNull()).agg(F.max("target_fiscal_year"))
    year = next_year.first()[0]
    rows = features.where(F.col("target_fiscal_year") == year).toPandas()

    mlflow.set_registry_uri("databricks-uc")
    model_name = f"{prefix}.{MODEL}"
    version = MlflowClient().get_model_version_by_alias(model_name, CHAMPION_ALIAS)
    model = mlflow.sklearn.load_model(f"models:/{model_name}@{CHAMPION_ALIAS}")
    probability = model.predict_proba(dataset.model_input(rows))[:, 1]
    frame = predictions(
        rows, probability, model_name, version.version, version.run_id, datetime.now(UTC)
    )
    print(f"FY{year}: {len(frame)} rows scored with {model_name} version {version.version}")
    write_table(
        spark,
        f"{prefix}.{TABLE}",
        spark.createDataFrame(frame, GOLD_PENALTY_PREDICTIONS_SCHEMA),
        GOLD_PENALTY_PREDICTIONS_SCHEMA,
        GOLD_PENALTY_PREDICTIONS_DOC,
        run,
    )
    flagged = float(frame["predicted_above_cutoff"].mean())
    run.metric(f"{prefix}.{TABLE}", "predicted_above_cutoff_rate", flagged)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score the next fiscal year with the champion.")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--schema", default="readmission")
    add_ops_arguments(parser)
    args = parser.parse_args(argv)
    with track_run("score", args) as run:
        spark = SparkSession.builder.getOrCreate()
        score(spark, f"{args.catalog}.{args.schema}", run)


if __name__ == "__main__":
    main()
