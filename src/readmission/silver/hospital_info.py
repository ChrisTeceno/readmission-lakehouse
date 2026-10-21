"""silver_hospital_info: CMS Hospital General Information, one row per facility.

Keeps the identity, type and ownership columns, the overall star rating and the readmission
measure group counts. The other measure group counts (mortality, safety, patient experience,
timely and effective care) stay in Bronze until a Gold table needs them.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.quality.constraints import constraint_rules
from readmission.silver.governance import TableDoc, field, silver_tags
from readmission.silver.normalize import pad_ccn, suppressed_to_null
from readmission.silver.quarantine import (
    first_violation,
    missing_rules,
    raw_row,
    split_rows,
    with_parsed,
)

TABLE = "silver_hospital_info"
KEY = ["facility_id"]
CONSTRAINTS = ["ccn_format"]

SILVER_HOSPITAL_INFO_SCHEMA = T.StructType(
    [
        field("facility_id", T.StringType(), "CMS Certification Number (CCN)", nullable=False),
        field("facility_name", T.StringType(), "Hospital name as CMS publishes it"),
        field("address", T.StringType(), "Street address"),
        field("city", T.StringType(), "City or town"),
        field("state", T.StringType(), "Two-letter state code"),
        field("zip_code", T.StringType(), "Five-digit ZIP code, kept as text for leading zeros"),
        field("county", T.StringType(), "County or parish"),
        field("telephone_number", T.StringType(), "Main telephone number"),
        field(
            "hospital_type", T.StringType(), "CMS hospital type, for example Acute Care Hospitals"
        ),
        field("hospital_ownership", T.StringType(), "CMS ownership category"),
        field(
            "emergency_services", T.BooleanType(), "Whether the hospital provides emergency care"
        ),
        field(
            "birthing_friendly",
            T.BooleanType(),
            "Meets the CMS criteria for the birthing-friendly designation",
        ),
        field(
            "overall_rating",
            T.IntegerType(),
            "CMS overall hospital star rating, 1 to 5; null when not rated",
        ),
        field("overall_rating_footnote", T.StringType(), "CMS footnote codes for the rating"),
        field(
            "readm_measure_count",
            T.IntegerType(),
            "Readmission measures CMS reports for this facility",
        ),
        field(
            "readm_measures_better",
            T.IntegerType(),
            "Readmission measures better than the national rate",
        ),
        field(
            "readm_measures_no_different",
            T.IntegerType(),
            "Readmission measures no different from the national rate",
        ),
        field(
            "readm_measures_worse",
            T.IntegerType(),
            "Readmission measures worse than the national rate",
        ),
        field("_batch_id", T.StringType(), "Bronze batch the row came from", nullable=False),
    ]
)

SILVER_HOSPITAL_INFO_DOC = TableDoc(
    comment=(
        "Hospital General Information: identity, type, ownership, star rating and readmission "
        "measure counts, one row per facility. Source: CMS Provider Data dataset xubh-q36u via "
        "bronze_hospital_info."
    ),
    tags=silver_tags("cms", dataset="xubh-q36u"),
)

NUMERIC_COLUMNS = [
    ("overall_rating", "hospital_overall_rating", "int"),
    ("readm_measure_count", "count_of_facility_readm_measures", "int"),
    ("readm_measures_better", "count_of_readm_measures_better", "int"),
    ("readm_measures_no_different", "count_of_readm_measures_no_different", "int"),
    ("readm_measures_worse", "count_of_readm_measures_worse", "int"),
]

TEXT_COLUMNS = {
    "facility_name": "facility_name",
    "address": "address",
    "city": "city_town",
    "state": "state",
    "zip_code": "zip_code",
    "county": "county_parish",
    "telephone_number": "telephone_number",
    "hospital_type": "hospital_type",
    "hospital_ownership": "hospital_ownership",
    "overall_rating_footnote": "hospital_overall_rating_footnote",
}


def silver_hospital_info(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_hospital_info rows, quarantine rows)."""
    df = bronze.withColumn("_raw", raw_row(bronze))
    df, parse_rules = with_parsed(df, NUMERIC_COLUMNS)
    for name, source in TEXT_COLUMNS.items():
        df = df.withColumn(name, suppressed_to_null(source))
    emergency = F.trim("emergency_services")
    df = (
        df.withColumn("facility_id", pad_ccn(F.col("facility_id")))
        .withColumn("emergency_services", F.when(emergency.isin("Yes", "No"), emergency == "Yes"))
        # CMS writes Y when a hospital meets the criteria and leaves the field blank otherwise.
        .withColumn(
            "birthing_friendly",
            F.coalesce(
                F.trim("meets_criteria_for_birthing_friendly_designation") == "Y", F.lit(False)
            ),
        )
    )
    rules = [
        *missing_rules(KEY),
        *parse_rules,
        ("rating_out_of_range", ~F.col("overall_rating").between(1, 5)),
        ("unknown_emergency_services", F.col("emergency_services").isNull()),
        *constraint_rules(CONSTRAINTS),
    ]
    checked = df.withColumn("_violation", first_violation(rules))
    return split_rows(checked, table=TABLE, schema=SILVER_HOSPITAL_INFO_SCHEMA, key=KEY)
