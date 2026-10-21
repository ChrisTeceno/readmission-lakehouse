"""gold_features: penalty model features per facility, measure and target fiscal year.

Leakage rule. The features for target fiscal year T use only information CMS had published
before it released FY T's results, which in this data means fiscal years strictly before T:

- HRRP history is joined on `fiscal_year < target_fiscal_year`, so no row of year T or later can
  reach a feature column. Only the `label_*` columns come from year T.
- Hospital General Information exists here only as the current snapshot, so the features take
  the attributes that rarely change (state, hospital type, ownership, emergency services) and
  leave out the star rating and the readmission measure counts, which are built from recent
  claims and would leak later years into early targets.
- ACS county socioeconomics (run 41919ab5) join here, as of the latest ACS vintage released
  before T, once `silver_acs_county` is merged; there are no ACS columns yet.

tests/test_gold.py fails if changing any year T or later row changes a feature for target T.

Targets are every (facility, measure, fiscal year) in gold_facility_condition_year, plus one
scoring row per pair in the latest fiscal year for the next cycle (FY2028), whose labels are
null: that is the row the model predicts.
"""

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.silver.governance import TableDoc, field, gold_tags

TABLE = "gold_features"
KEY = ["facility_id", "measure", "target_fiscal_year"]
LABEL_COLUMNS = ["label_above_cutoff", "label_err", "label_cutoff_is_approximate"]
LAGS = [1, 2, 3]

LAGGED = {
    "excess_readmission_ratio": ("err", T.DoubleType(), "ERR"),
    "above_cutoff": ("above_cutoff", T.BooleanType(), "ERR above that year's cutoff"),
    "discharges": ("discharges", T.IntegerType(), "Eligible discharges"),
}
LAG1_ONLY = {
    "readmissions": ("readmissions", T.IntegerType(), "Readmissions; null for IPPS years"),
    "predicted_rate": ("predicted_rate", T.DoubleType(), "Predicted rate, percent"),
    "expected_rate": ("expected_rate", T.DoubleType(), "Expected rate, percent"),
    "err_change": ("err_change", T.DoubleType(), "ERR change from its prior window"),
    "anomaly_flag": ("anomaly_flag", T.BooleanType(), "Anomaly flag"),
}
HOSPITAL_ATTRIBUTES = [
    ("state", T.StringType(), "Two-letter state code"),
    ("hospital_type", T.StringType(), "CMS hospital type"),
    ("hospital_ownership", T.StringType(), "CMS ownership category"),
    ("emergency_services", T.BooleanType(), "Whether the hospital provides emergency care"),
]


def _lag_fields() -> list[T.StructField]:
    fields = []
    for name, data_type, comment in LAGGED.values():
        for lag in LAGS:
            fields.append(field(f"{name}_lag{lag}", data_type, f"{comment} in FY T-{lag}"))
    for name, data_type, comment in LAG1_ONLY.values():
        fields.append(field(f"{name}_lag1", data_type, f"{comment} in FY T-1"))
    return fields


GOLD_FEATURES_SCHEMA = T.StructType(
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
            "Fiscal year T the row predicts; features use fiscal years before T only",
            nullable=False,
        ),
        *_lag_fields(),
        field(
            "years_observed",
            T.IntegerType(),
            "Fiscal years before T with an ERR for this condition",
            nullable=False,
        ),
        field(
            "years_above_cutoff",
            T.IntegerType(),
            "Fiscal years before T with the ERR above the cutoff",
            nullable=False,
        ),
        field("err_mean_prior", T.DoubleType(), "Mean ERR over the fiscal years before T"),
        field("err_max_prior", T.DoubleType(), "Highest ERR over the fiscal years before T"),
        *[field(name, data_type, comment) for name, data_type, comment in HOSPITAL_ATTRIBUTES],
        field(
            "label_above_cutoff",
            T.BooleanType(),
            "Label: ERR above the cutoff in FY T; null for the next-cycle scoring rows",
        ),
        field("label_err", T.DoubleType(), "Label: the ERR in FY T"),
        field(
            "label_cutoff_is_approximate",
            T.BooleanType(),
            "The FY T cutoff is the 1.0 stand-in for a peer group median",
        ),
    ]
)

FEATURE_COLUMNS = [
    f.name for f in GOLD_FEATURES_SCHEMA if f.name not in KEY and f.name not in LABEL_COLUMNS
]

GOLD_FEATURES_DOC = TableDoc(
    comment=(
        "Penalty model features per facility, condition and target fiscal year T. Leakage "
        "rule: features use only fiscal years before T and hospital attributes that rarely "
        "change; only the label_* columns come from FY T. The latest fiscal year plus one "
        "holds scoring rows with null labels. Built from gold_facility_condition_year and "
        "silver_hospital_info."
    ),
    tags=gold_tags("cms", dataset="9n3s-kdb3,ipps-supplemental,xubh-q36u"),
)


def targets(facts: DataFrame) -> DataFrame:
    labelled = facts.select(
        "facility_id",
        "measure",
        F.col("fiscal_year").alias("target_fiscal_year"),
        F.col("above_cutoff").alias("label_above_cutoff"),
        F.col("excess_readmission_ratio").alias("label_err"),
        F.col("cutoff_is_approximate").alias("label_cutoff_is_approximate"),
    )
    latest = facts.agg(F.max("fiscal_year")).first()[0]
    scoring = facts.where(F.col("fiscal_year") == latest).select(
        "facility_id",
        "measure",
        F.lit(latest + 1).alias("target_fiscal_year"),
        F.lit(None).cast("boolean").alias("label_above_cutoff"),
        F.lit(None).cast("double").alias("label_err"),
        F.lit(None).cast("boolean").alias("label_cutoff_is_approximate"),
    )
    return labelled.unionByName(scoring)


def lagged(column: str, name: str, lag: int) -> Column:
    years_back = F.col("target_fiscal_year") - F.col("_history_year")
    return F.max(F.when(years_back == lag, F.col(column))).alias(f"{name}_lag{lag}")


def gold_features(facts: DataFrame, hospital_info: DataFrame) -> DataFrame:
    """`facts` is gold_facility_condition_year, `hospital_info` silver_hospital_info."""
    history = facts.select(
        F.col("facility_id").alias("_history_facility_id"),
        F.col("measure").alias("_history_measure"),
        F.col("fiscal_year").alias("_history_year"),
        *LAGGED,
        *LAG1_ONLY,
    )
    # The leakage rule: only fiscal years strictly before the target reach the aggregates.
    before_target = (
        (F.col("facility_id") == F.col("_history_facility_id"))
        & (F.col("measure") == F.col("_history_measure"))
        & (F.col("_history_year") < F.col("target_fiscal_year"))
    )
    joined = targets(facts).join(history, before_target, "left")
    err = F.col("excess_readmission_ratio")
    df = joined.groupBy(*KEY, *LABEL_COLUMNS).agg(
        *[lagged(c, name, lag) for c, (name, _, _) in LAGGED.items() for lag in LAGS],
        *[lagged(c, name, 1) for c, (name, _, _) in LAG1_ONLY.items()],
        F.count(err).alias("years_observed"),
        F.count_if(F.col("above_cutoff")).alias("years_above_cutoff"),
        F.avg(err).alias("err_mean_prior"),
        F.max(err).alias("err_max_prior"),
    )
    attributes = hospital_info.select("facility_id", *[name for name, _, _ in HOSPITAL_ATTRIBUTES])
    df = df.join(attributes, "facility_id", "left")
    return df.select(*[F.col(f.name).cast(f.dataType).alias(f.name) for f in GOLD_FEATURES_SCHEMA])
