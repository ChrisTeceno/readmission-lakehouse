"""HRRP payment inputs: the payment adjustment factor and what CMS computes it from.

- silver_hrrp_payment: one row per facility and fiscal year, every IPPS supplemental year
  (FY2013 to FY2027): the payment adjustment factor (PAF) and, from FY2019 on, the dual
  proportion, peer group and neutrality modifier.
- silver_hrrp_payment_measure: one row per facility, condition and fiscal year for the years
  with peer grouping (FY2019 to FY2027): eligible discharges, ERR, peer group median ERR,
  penalty indicator and DRG payment ratio, the per-condition terms of the PAF. It is the one
  place the supplemental files' per-condition values are parsed: silver_hrrp_history takes its
  peer medians from here.
- silver_ipps_impact: one row per facility and fiscal year from the IPPS impact file: the
  inputs to Gold's estimate of base operating DRG payments.

All three read Bronze `record` maps (bronze_hrrp_history_ipps, bronze_ipps_impact), each fiscal
year from its latest batch. See docs/penalty-dollars.md.
"""

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.quality.constraints import constraint_rules
from readmission.silver.governance import TableDoc, field, silver_tags
from readmission.silver.hrrp_history import (
    IPPS_CCN_KEYS,
    IPPS_MEASURE_PATTERNS,
    _first_match,
    latest_batch_per_year,
)
from readmission.silver.normalize import pad_ccn
from readmission.silver.quarantine import (
    first_violation,
    missing_rules,
    split_rows,
    with_parsed,
)

PAYMENT_TABLE = "silver_hrrp_payment"
PAYMENT_KEY = ["facility_id", "fiscal_year"]
PAYMENT_CONSTRAINTS = ["ccn_format", "paf_range"]

MEASURE_TABLE = "silver_hrrp_payment_measure"
MEASURE_KEY = ["facility_id", "measure", "fiscal_year"]
MEASURE_CONSTRAINTS = ["ccn_format", "err_positive"]

IMPACT_TABLE = "silver_ipps_impact"
IMPACT_KEY = ["facility_id", "fiscal_year"]
IMPACT_CONSTRAINTS = ["ccn_format"]

SILVER_HRRP_PAYMENT_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field("fiscal_year", T.IntegerType(), "HRRP payment fiscal year", nullable=False),
        field(
            "payment_adjustment_factor",
            T.DoubleType(),
            "Factor applied to base operating DRG payments, 0.97 to 1.0 (1.0 is no reduction)",
        ),
        field(
            "payment_reduction_pct",
            T.DoubleType(),
            "Payment reduction, percent, as CMS reports it; FY2022 on, null before",
        ),
        field(
            "dual_proportion",
            T.DoubleType(),
            "Share of Medicare stays by full-benefit dual eligibles; FY2019 on, null before",
        ),
        field(
            "peer_group",
            T.IntegerType(),
            "Dual-eligibility peer group quintile, 1 to 5; FY2019 on, null before",
        ),
        field(
            "neutrality_modifier",
            T.DoubleType(),
            "Budget neutrality modifier in the PAF formula; FY2019 on, null before",
        ),
    ]
)

SILVER_HRRP_PAYMENT_DOC = TableDoc(
    comment=(
        "HRRP payment adjustment factor per facility and fiscal year, from the CMS.gov IPPS "
        "final rule supplemental files (FY2013 to FY2027) via "
        "bronze_hrrp_history_ipps. From FY2019 the files add the dual proportion, peer group "
        "and neutrality modifier."
    ),
    tags=silver_tags("cms", dataset="ipps-supplemental"),
)

SILVER_HRRP_PAYMENT_MEASURE_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field(
            "measure",
            T.StringType(),
            "HRRP condition: AMI, HF, PN, COPD, HIP_KNEE or CABG",
            nullable=False,
        ),
        field("fiscal_year", T.IntegerType(), "HRRP payment fiscal year", nullable=False),
        field("discharges", T.IntegerType(), "Eligible discharges in the period; null if none"),
        field(
            "excess_readmission_ratio",
            T.DoubleType(),
            "Predicted over expected readmission rate (ratio); null when CMS computed none",
        ),
        field(
            "peer_median_err",
            T.DoubleType(),
            "Median ERR of the hospital's peer group for the condition (ratio)",
        ),
        field(
            "penalty_indicator",
            T.BooleanType(),
            "CMS's flag: ERR above the peer median with 25 or more eligible discharges",
        ),
        field(
            "drg_payment_ratio",
            T.DoubleType(),
            "Base operating DRG payments for the condition's stays over all base operating DRG "
            "payments (ratio)",
        ),
    ]
)

SILVER_HRRP_PAYMENT_MEASURE_DOC = TableDoc(
    comment=(
        "Per-condition terms of the HRRP payment adjustment factor, FY2019 to FY2027, one row "
        "per facility, condition and fiscal year: ERR, peer group median, penalty indicator and "
        "DRG payment ratio. From the CMS.gov IPPS supplemental files via "
        "bronze_hrrp_history_ipps. silver_hrrp_history takes its peer group medians from here."
    ),
    tags=silver_tags("cms", dataset="ipps-supplemental"),
)

SILVER_IPPS_IMPACT_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field("fiscal_year", T.IntegerType(), "IPPS payment fiscal year", nullable=False),
        field(
            "provider_type",
            T.StringType(),
            "CMS provider type: 0 IPPS, 7 rural referral center, 16 and 17 sole community "
            "hospital, others in the impact file's variable descriptions",
        ),
        field("wage_index", T.DoubleType(), "The fiscal year's wage index (ratio)"),
        field("bills", T.IntegerType(), "Medicare cases in the MedPAR year CMS modeled from"),
        field(
            "transfer_adjusted_cases",
            T.DoubleType(),
            "Transfer-adjusted cases under the fiscal year's DRG grouper",
        ),
        field(
            "transfer_adjusted_cmi",
            T.DoubleType(),
            "Transfer-adjusted case mix index under the fiscal year's DRG grouper",
        ),
    ]
)

SILVER_IPPS_IMPACT_DOC = TableDoc(
    comment=(
        "IPPS final rule impact file per facility and fiscal year, FY2026 and FY2027 (FY2027 "
        "from the correction notice): wage index, provider type and transfer-adjusted cases and "
        "case mix index, the inputs CMS models the year's payments from. Via bronze_ipps_impact. "
        "Hospital names are not carried."
    ),
    tags=silver_tags("cms", dataset="ipps-impact"),
)

# Supplemental file headers: `FY 2013  Readmission Payment Adjustment Factor`, `Corrected FY
# 2015  Readmissions Adjustment Factor`, `Payment Adjustment Factor` (FY2019, FY2020), `Payment
# adjustment factor` (FY2021 on). Bronze keys are already trimmed
# (readmission.ingest.hrrp_history.parse_ipps_rows); every lookup ignores case.
PAF_PATTERN = r"adjustment factor"
PAYMENT_FIELDS = [
    ("payment_reduction_pct", r"^payment reduction percentage$", "double"),
    ("dual_proportion", r"^dual proportion$", "double"),
    ("peer_group", r"^peer group assignment$", "int"),
    ("neutrality_modifier", r"^neutrality modifier$", "double"),
]
MEASURE_FIELD_PATTERNS = [
    ("peer_median_err", r"^peer group median err"),
    ("discharges", r"^number of"),
    ("excess_readmission_ratio", r"^err for"),
    ("penalty_indicator", r"^penalty indicator"),
    ("drg_payment_ratio", r"^drg payment ratio"),
]
# The impact file names its case columns after the DRG grouper version, which is the fiscal year
# minus 1983 (V43 for FY2026, V44 for FY2027, per each file's variable descriptions); each file
# also carries the prior grouper's columns, which model the prior year.
GROUPER_OFFSET = 1983


def _ccn() -> Column:
    return pad_ccn(F.coalesce(*[F.col("record")[k] for k in IPPS_CCN_KEYS])).alias("facility_id")


def _first_key_value(pattern: str) -> Column:
    """The record value under the first key matching `pattern`, ignoring case."""
    keys = F.filter(F.map_keys(F.col("record")), lambda k: F.lower(k).rlike(pattern))
    return F.col("record")[F.get(keys, 0)]


def _split(df: DataFrame, rules: list, *, table: str, schema: T.StructType, key: list[str]):
    df = df.withColumn("_raw", F.to_json(F.struct(*[f.name for f in schema])))
    checked = df.withColumn("_violation", first_violation(rules))
    return split_rows(checked, table=table, schema=schema, key=key)


def silver_hrrp_payment(bronze_ipps: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_hrrp_payment rows, quarantine rows). FY2022 on write the reduction as a
    percentage with a % sign (`0.04%`)."""
    df = latest_batch_per_year(bronze_ipps).select(
        _ccn(),
        "fiscal_year",
        "_batch_id",
        _first_key_value(PAF_PATTERN).alias("_paf"),
        *[
            F.regexp_replace(_first_key_value(pattern), "%", "").alias(f"_{name}")
            for name, pattern, _ in PAYMENT_FIELDS
        ],
    )
    df, parse_rules = with_parsed(
        df,
        [("payment_adjustment_factor", "_paf", "double")]
        + [(name, f"_{name}", data_type) for name, _, data_type in PAYMENT_FIELDS],
    )
    rules = [
        *missing_rules(["facility_id"]),
        *parse_rules,
        *constraint_rules(PAYMENT_CONSTRAINTS),
    ]
    return _split(
        df, rules, table=PAYMENT_TABLE, schema=SILVER_HRRP_PAYMENT_SCHEMA, key=PAYMENT_KEY
    )


def silver_hrrp_payment_measure(bronze_ipps: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Unpivot the supplemental files that carry DRG payment ratios (peer grouping, FY2019 on).

    Unlike silver_hrrp_history, an ERR is kept wherever CMS reports one, under 25 eligible
    discharges included: the PAF formula skips those conditions, so Gold applies the 25-discharge
    rule itself. FY2021 writes discharge counts with thousands separators (`1,107`), which
    suppressed_to_null strips.
    """
    bronze = latest_batch_per_year(bronze_ipps).where(F.col("fiscal_year") >= 2019)
    long = bronze.select(
        _ccn(), "fiscal_year", "_batch_id", F.explode("record").alias("key", "value")
    )
    key = F.lower(F.col("key"))
    classified = (
        long.withColumn("measure", _first_match(key, IPPS_MEASURE_PATTERNS))
        .withColumn("field", _first_match(key, MEASURE_FIELD_PATTERNS))
        .where(F.col("measure").isNotNull() & F.col("field").isNotNull())
    )
    fields = [name for name, _ in MEASURE_FIELD_PATTERNS]
    wide = (
        classified.groupBy("facility_id", "fiscal_year", "measure", "_batch_id")
        .pivot("field", fields)
        .agg(F.first("value"))
        .select(
            "facility_id",
            "fiscal_year",
            "measure",
            "_batch_id",
            *[F.col(name).alias(f"_{name}") for name in fields],
        )
    )
    flag = F.upper(F.trim("_penalty_indicator"))
    wide = wide.withColumn(
        "_penalty_indicator", F.when(flag == "Y", "true").when(flag == "N", "false").otherwise(flag)
    )
    types = {f.name: f.dataType.simpleString() for f in SILVER_HRRP_PAYMENT_MEASURE_SCHEMA}
    df, parse_rules = with_parsed(wide, [(name, f"_{name}", types[name]) for name in fields])
    rules = [*missing_rules(["facility_id"]), *parse_rules, *constraint_rules(MEASURE_CONSTRAINTS)]
    return _split(
        df,
        rules,
        table=MEASURE_TABLE,
        schema=SILVER_HRRP_PAYMENT_MEASURE_SCHEMA,
        key=MEASURE_KEY,
    )


def silver_ipps_impact(bronze_impact: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_ipps_impact rows, quarantine rows). Column names carry the year
    (`FY 2027 Wage Index`, `CASETA44`), so each row looks its own year's columns up."""
    record = F.col("record")
    grouper = (F.col("fiscal_year") - GROUPER_OFFSET).cast("string")
    wage_index = F.concat(F.lit("FY "), F.col("fiscal_year").cast("string"), F.lit(" Wage Index"))
    df = latest_batch_per_year(bronze_impact).select(
        pad_ccn(record["Provider Number"]).alias("facility_id"),
        "fiscal_year",
        "_batch_id",
        F.trim(record["Provider Type"]).alias("provider_type"),
        record[wage_index].alias("_wage_index"),
        record["Bills"].alias("_bills"),
        record[F.concat(F.lit("CASETA"), grouper)].alias("_transfer_adjusted_cases"),
        record[F.concat(F.lit("TACMIV"), grouper)].alias("_transfer_adjusted_cmi"),
    )
    df, parse_rules = with_parsed(
        df,
        [
            ("wage_index", "_wage_index", "double"),
            ("bills", "_bills", "int"),
            ("transfer_adjusted_cases", "_transfer_adjusted_cases", "double"),
            ("transfer_adjusted_cmi", "_transfer_adjusted_cmi", "double"),
        ],
    )
    rules = [*missing_rules(["facility_id"]), *parse_rules, *constraint_rules(IMPACT_CONSTRAINTS)]
    return _split(df, rules, table=IMPACT_TABLE, schema=SILVER_IPPS_IMPACT_SCHEMA, key=IMPACT_KEY)
