"""Cleaning rules every Silver table shares: CMS suppression codes, CCNs, measure names, dedup."""

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

# CMS writes suppressed or unavailable values as text; '.' is the IPPS files' missing marker and
# 'Not Applicable' is how Unplanned Hospital Visits marks patient counts for rate-only measures.
SUPPRESSION_CODES = ["N/A", "Not Available", "Not Applicable", "Too Few to Report", "."]

MEASURES = ["AMI", "HF", "PN", "COPD", "HIP_KNEE", "CABG"]

# A CMS Certification Number: two state digits plus four characters, letters allowed.
CCN_PATTERN = "^[0-9A-Z]{6}$"

CMS_DATE_FORMAT = "MM/dd/yyyy"


def suppressed_to_null(name: str) -> Column:
    """Suppression codes and blanks become null; thousands separators are stripped."""
    value = F.trim(F.col(name))
    return F.when(value.isin(SUPPRESSION_CODES) | (value == ""), None).otherwise(
        F.regexp_replace(value, ",", "")
    )


def pad_ccn(column: Column) -> Column:
    """CCNs are 6 characters; spreadsheet round trips drop the leading zero (010001 -> 10001).
    Longer values are left as they are, so validation sees them instead of a truncated CCN, and
    a blank one is null rather than 000000."""
    value = F.trim(column)
    return (
        F.when(value == "", None).when(F.length(value) < 6, F.lpad(value, 6, "0")).otherwise(value)
    )


def canonical_measure(column: Column) -> Column:
    """READM_30_AMI_HRRP (HRRP 2019), READM-30-AMI-HRRP (HRRP 2020 on) and READM_30_AMI
    (Unplanned Hospital Visits) all become AMI; anything that is not an HRRP condition is null."""
    normalized = F.regexp_replace(F.upper(F.trim(column)), "-", "_")
    measure = F.regexp_extract(normalized, r"^READM_30_(.+?)(?:_HRRP)?$", 1)
    return F.when(measure.isin(MEASURES), measure)


def try_cast(name: str, data_type: str) -> Column:
    """Null instead of an error when a value does not parse; serverless runs with ANSI mode on."""
    return F.expr(f"try_cast(`{name}` AS {data_type})")


def parse_cms_date(name: str) -> Column:
    return F.to_date(F.try_to_timestamp(F.col(name), F.lit(CMS_DATE_FORMAT)))


def latest_per_key(df: DataFrame, key: list[str]) -> DataFrame:
    """One row per business key, from the most recent batch. Batch ids are UTC timestamps
    (20261021T143411Z), so they sort in load order."""
    window = Window.partitionBy(*key).orderBy(F.col("_batch_id").desc())
    return df.withColumn("_rank", F.row_number().over(window)).where("_rank = 1").drop("_rank")
