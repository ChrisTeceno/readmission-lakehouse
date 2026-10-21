"""gold_facility_condition_year: one row per facility, measure and fiscal year, FY2013 to FY2027.

The HRRP results from `silver_hrrp_history`, the penalty cutoff that applied that year, the
comparison with the prior non-overlapping window, anomaly flags and the hospital's attributes
from Hospital General Information.
"""

from itertools import chain

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.gold.anomalies import MIN_DISCHARGES, with_anomaly_flags
from readmission.gold.windows import PRIOR_WINDOW_YEARS, WINDOW_MONTHS
from readmission.silver.governance import TableDoc, field, gold_tags

TABLE = "gold_facility_condition_year"
KEY = ["facility_id", "measure", "fiscal_year"]

# FY2013 to FY2018 penalized any ERR above 1.0. From FY2019 CMS compares each hospital with the
# median ERR of its dual-eligibility peer group for the measure, and only the FY2027 IPPS file
# carries that median here: FY2019 to FY2026 use 1.0 as a stand-in, marked approximate.
FIXED_CUTOFF = 1.0
FIRST_PEER_GROUP_YEAR = 2019

GOLD_FACILITY_CONDITION_YEAR_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field(
            "measure",
            T.StringType(),
            "HRRP condition: AMI, HF, PN, COPD, HIP_KNEE or CABG",
            nullable=False,
        ),
        field("fiscal_year", T.IntegerType(), "HRRP payment fiscal year", nullable=False),
        field("facility_name", T.StringType(), "Hospital name; null if no longer in CMS's list"),
        field("state", T.StringType(), "Two-letter state code, from Hospital General Information"),
        field("county", T.StringType(), "County or parish, from Hospital General Information"),
        field("hospital_type", T.StringType(), "CMS hospital type"),
        field("hospital_ownership", T.StringType(), "CMS ownership category"),
        field("period_start", T.DateType(), "First discharge date of the performance period"),
        field("period_end", T.DateType(), "Last discharge date of the performance period"),
        field(
            "window_months",
            T.IntegerType(),
            "Months of discharges in the window: 36, or 30 (FY2022, FY2024), or 24 (FY2027)",
        ),
        field(
            "discharges", T.IntegerType(), "Eligible discharges in the period; null if suppressed"
        ),
        field(
            "readmissions",
            T.IntegerType(),
            "Unplanned readmissions within 30 days; null if suppressed and for IPPS years",
        ),
        field(
            "excess_readmission_ratio",
            T.DoubleType(),
            "Predicted over expected readmission rate (ratio); null when CMS computed none",
        ),
        field(
            "predicted_rate",
            T.DoubleType(),
            "Risk-adjusted predicted readmission rate, percent; null for IPPS years",
        ),
        field(
            "expected_rate",
            T.DoubleType(),
            "Risk-adjusted expected readmission rate, percent; null for IPPS years",
        ),
        field(
            "peer_group",
            T.IntegerType(),
            "Dual-eligibility peer group quintile, 1 to 5; FY2027 only",
        ),
        field(
            "penalty_cutoff",
            T.DoubleType(),
            "ERR above which the condition counted toward the penalty that year (ratio)",
            nullable=False,
        ),
        field(
            "cutoff_is_approximate",
            T.BooleanType(),
            "True for FY2019 to FY2026: the real cutoff was the peer group median, not in this "
            "data, and 1.0 stands in",
            nullable=False,
        ),
        field(
            "above_cutoff",
            T.BooleanType(),
            "ERR above penalty_cutoff; null when there is no ERR",
        ),
        field(
            "prior_window_fiscal_year",
            T.IntegerType(),
            "Latest fiscal year whose performance period ends before this one starts",
        ),
        field("prior_window_err", T.DoubleType(), "ERR in the prior non-overlapping window"),
        field(
            "err_change",
            T.DoubleType(),
            "ERR minus prior_window_err; null unless both windows have a ratio",
        ),
        field(
            "err_change_robust_z",
            T.DoubleType(),
            "Modified z-score of err_change among hospitals with the same measure and year",
        ),
        field(
            "discharge_ratio",
            T.DoubleType(),
            "Discharges per month over the prior window's; null under 25 discharges in either",
        ),
        field(
            "anomaly_flag",
            T.BooleanType(),
            "True when err_change or discharge_ratio is anomalous (see anomaly_reason)",
            nullable=False,
        ),
        field(
            "anomaly_reason",
            T.StringType(),
            "Rules that fired: err_change beyond robust z 3.5, discharge_jump beyond x2 or x0.5",
        ),
        field(
            "source",
            T.StringType(),
            "pdc (Provider Data Catalog archive) or ipps (CMS.gov IPPS supplemental file)",
            nullable=False,
        ),
    ]
)

GOLD_FACILITY_CONDITION_YEAR_DOC = TableDoc(
    comment=(
        "HRRP results per facility, condition and fiscal year, FY2013 to FY2027, with the "
        "penalty cutoff, the change from the prior non-overlapping window and anomaly flags. "
        "Cutoff: 1.0 for FY2013 to FY2018 (exact), the peer group median ERR for FY2027 "
        "(exact), and 1.0 as a stand-in for FY2019 to FY2026, where CMS used the peer group "
        "median but no file here carries it (cutoff_is_approximate = true). Hospital "
        "attributes are the current Hospital General Information snapshot. Built from "
        "silver_hrrp_history and silver_hospital_info."
    ),
    tags=gold_tags("cms", dataset="9n3s-kdb3,ipps-supplemental,xubh-q36u"),
)

HOSPITAL_ATTRIBUTES = ["facility_name", "state", "county", "hospital_type", "hospital_ownership"]


def year_map(values: dict[int, int]) -> Column:
    return F.create_map(*chain.from_iterable((F.lit(k), F.lit(v)) for k, v in values.items()))


def with_penalty_cutoff(df: DataFrame) -> DataFrame:
    """A FY2027 row without a peer median (no ERR, so none was published) falls back to 1.0 and
    is marked approximate too."""
    peer_era = F.col("fiscal_year") >= FIRST_PEER_GROUP_YEAR
    peer_median = F.when(peer_era, F.col("peer_median_err"))
    return (
        df.withColumn("penalty_cutoff", F.coalesce(peer_median, F.lit(FIXED_CUTOFF)))
        .withColumn("cutoff_is_approximate", peer_era & peer_median.isNull())
        .withColumn("above_cutoff", F.col("excess_readmission_ratio") > F.col("penalty_cutoff"))
    )


def monthly(discharges: str, months: str) -> Column:
    return F.when(F.col(discharges) >= MIN_DISCHARGES, F.col(discharges) / F.col(months))


def gold_facility_condition_year(history: DataFrame, hospital_info: DataFrame) -> DataFrame:
    """`history` is silver_hrrp_history, `hospital_info` silver_hospital_info. One output row per
    history row: both joins are on unique keys."""
    facts = with_penalty_cutoff(
        history.withColumn(
            "prior_window_fiscal_year", year_map(PRIOR_WINDOW_YEARS)[F.col("fiscal_year")]
        ).withColumn("window_months", year_map(WINDOW_MONTHS)[F.col("fiscal_year")])
    )
    prior = facts.select(
        "facility_id",
        "measure",
        F.col("fiscal_year").alias("prior_window_fiscal_year"),
        F.col("excess_readmission_ratio").alias("prior_window_err"),
        monthly("discharges", "window_months").alias("_prior_monthly"),
    )
    df = (
        facts.join(prior, ["facility_id", "measure", "prior_window_fiscal_year"], "left")
        .withColumn("err_change", F.col("excess_readmission_ratio") - F.col("prior_window_err"))
        .withColumn(
            "discharge_ratio", monthly("discharges", "window_months") / F.col("_prior_monthly")
        )
    )
    df = with_anomaly_flags(df)
    attributes = hospital_info.select("facility_id", *HOSPITAL_ATTRIBUTES)
    df = df.join(attributes, "facility_id", "left")
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in GOLD_FACILITY_CONDITION_YEAR_SCHEMA]
    )
