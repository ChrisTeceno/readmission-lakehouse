"""Anomaly flags on the facility-condition facts.

Both rules compare a fiscal year with its prior non-overlapping window (`gold.windows`), so a
flag means two independent sets of discharges disagree, not one set seen twice:

- `err_change`: the ERR change is an outlier among hospitals with the same measure and fiscal
  year, by the modified z-score of Iglewicz and Hoaglin (median and MAD, so the outliers being
  looked for do not widen the spread that judges them).
- `discharge_jump`: annualized discharges more than doubled or fell by more than half, which
  is more often a merger, a CCN change or a coding change than a change in patients.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

# The modified z-score is 0.6745 * (x - median) / MAD; Iglewicz and Hoaglin flag beyond 3.5.
MAD_SCALE = 0.6745
ROBUST_Z_THRESHOLD = 3.5

DISCHARGE_JUMP_FACTOR = 2.0
# CMS computes no ratio under 25 discharges; below that a jump of a few patients is noise.
MIN_DISCHARGES = 25


def with_robust_z(df: DataFrame, value: str, by: list[str], name: str) -> DataFrame:
    """Modified z-score of `value` within each `by` group; null where the MAD is 0."""
    median = df.groupBy(*by).agg(F.median(value).alias("_median"))
    deviations = df.join(median, by, "left").withColumn(
        "_deviation", F.abs(F.col(value) - F.col("_median"))
    )
    mad = deviations.groupBy(*by).agg(F.median("_deviation").alias("_mad"))
    z = MAD_SCALE * (F.col(value) - F.col("_median")) / F.col("_mad")
    return (
        deviations.join(mad, by, "left")
        .withColumn(name, F.when(F.col("_mad") > 0, z))
        .drop("_median", "_deviation", "_mad")
    )


def with_anomaly_flags(df: DataFrame) -> DataFrame:
    """Needs `err_change` and `discharge_ratio`; adds `err_change_robust_z`, `anomaly_flag`
    (never null) and `anomaly_reason` (the rules that fired, joined with '; ', or null)."""
    df = with_robust_z(df, "err_change", ["measure", "fiscal_year"], "err_change_robust_z")
    reasons = [
        F.when(
            F.abs("err_change_robust_z") > ROBUST_Z_THRESHOLD,
            F.format_string(
                "err_change %+.3f (robust z %.1f)",
                F.col("err_change"),
                F.col("err_change_robust_z"),
            ),
        ),
        F.when(
            (F.col("discharge_ratio") > DISCHARGE_JUMP_FACTOR)
            | (F.col("discharge_ratio") < 1 / DISCHARGE_JUMP_FACTOR),
            F.format_string("discharge_jump x%.2f", F.col("discharge_ratio")),
        ),
    ]
    reason = F.concat_ws("; ", *reasons)
    return df.withColumn("anomaly_reason", F.nullif(reason, F.lit(""))).withColumn(
        "anomaly_flag", F.col("anomaly_reason").isNotNull()
    )
