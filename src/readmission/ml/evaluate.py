"""Penalty model metrics, the naive baseline, the fairness table and the metric gate."""

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

# The champion's test AUC must reach this floor as well as the logistic regression's. Carrying
# last year's above-cutoff flag forward already scores 0.77 on FY2026 (lag-1 shares two of the
# target's three performance years), so a model under 0.80 adds little to that rule. The
# 2026-10-21 champion scored 0.874.
AUC_FLOOR = 0.80
TOP_FRACTION = 0.1


class GateFailed(Exception):
    pass


def recall_at_top(y: np.ndarray, score: np.ndarray, fraction: float = TOP_FRACTION) -> float:
    """Share of all positives among the `fraction` of rows with the highest scores: how much of
    next year's penalty a review of the top decile would catch. Rows tied at the decile
    boundary count in proportion, the expected value of breaking the tie at random, so a score
    with few distinct values (the naive 0/1 flag) is not flattered or punished by row order."""
    y = np.asarray(y)
    score = np.asarray(score)
    positives = y.sum()
    if positives == 0:
        return float("nan")
    k = int(np.ceil(fraction * len(y)))
    threshold = np.sort(score)[::-1][k - 1]
    above = score > threshold
    tied = score == threshold
    share_of_tied = (k - above.sum()) / tied.sum()
    return float((y[above].sum() + share_of_tied * y[tied].sum()) / positives)


def classification_metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    y = np.asarray(y)
    return {
        "rows": float(len(y)),
        "prevalence": float(y.mean()),
        "auc": float(roc_auc_score(y, probability)),
        "pr_auc": float(average_precision_score(y, probability)),
        "brier": float(brier_score_loss(y, probability)),
        "recall_top_decile": recall_at_top(y, probability),
    }


def naive_probability(features: pd.DataFrame, prevalence: float) -> np.ndarray:
    """The rule a model has to beat: last year's above-cutoff flag, carried forward. A hospital
    with no FY T-1 ratio gets the training prevalence."""
    flag = features["above_cutoff_lag1"].astype(object)
    return flag.where(flag.notna(), prevalence).astype(float).to_numpy()


def calibration_table(y: np.ndarray, probability: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Observed rate against mean predicted probability, per equal-width probability bin."""
    frame = pd.DataFrame({"y": np.asarray(y), "p": np.asarray(probability)})
    frame["bin"] = np.minimum((frame["p"] * bins).astype(int), bins - 1)
    table = frame.groupby("bin").agg(
        rows=("y", "size"), mean_predicted=("p", "mean"), observed_rate=("y", "mean")
    )
    return table.reset_index()


def group_metrics(
    y: np.ndarray, probability: np.ndarray, groups: pd.Series, threshold: float = 0.5
) -> pd.DataFrame:
    """Per group: size, observed and mean predicted rate, AUC, and the true and false positive
    rates at `threshold`. AUC is null for a group with only one class."""
    frame = pd.DataFrame(
        {"y": np.asarray(y), "p": np.asarray(probability), "group": np.asarray(groups)}
    )
    rows = []
    for group, part in frame.groupby("group"):
        flagged = part["p"] >= threshold
        positives = part["y"] == 1
        rows.append(
            {
                "group": group,
                "rows": len(part),
                "observed_rate": part["y"].mean(),
                "mean_predicted": part["p"].mean(),
                "auc": roc_auc_score(part["y"], part["p"]) if part["y"].nunique() == 2 else None,
                "true_positive_rate": flagged[positives].mean() if positives.any() else None,
                "false_positive_rate": flagged[~positives].mean() if (~positives).any() else None,
            }
        )
    return pd.DataFrame(rows)


def gate_failures(champion_test_auc: float, baseline_test_auc: float) -> list[str]:
    failures = []
    if champion_test_auc < baseline_test_auc:
        failures.append(
            f"test AUC {champion_test_auc:.4f} is below the logistic regression's "
            f"{baseline_test_auc:.4f}"
        )
    if champion_test_auc < AUC_FLOOR:
        failures.append(f"test AUC {champion_test_auc:.4f} is below the floor {AUC_FLOOR}")
    return failures


def check_gate(champion_test_auc: float, baseline_test_auc: float) -> None:
    failures = gate_failures(champion_test_auc, baseline_test_auc)
    if failures:
        raise GateFailed("penalty model gate: " + "; ".join(failures))
