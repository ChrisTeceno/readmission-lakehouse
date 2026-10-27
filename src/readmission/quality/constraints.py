"""Silver CHECK constraints, defined once for Delta and for quarantine routing.

The same SQL expression is added to the Delta table as a CHECK constraint and evaluated by the
Silver build before the write, so a row that would break a constraint goes to quarantine
instead of failing the write. A null result passes, as it does for a Delta CHECK.
"""

from pyspark.sql import Column
from pyspark.sql import functions as F

CONSTRAINTS: dict[str, str] = {
    # A CMS Certification Number: two state digits plus four characters, letters allowed.
    "ccn_format": "facility_id RLIKE '^[0-9A-Z]{6}$'",
    # An excess readmission ratio is predicted over expected readmissions, so it is positive;
    # CMS leaves it blank (null here) when it computes none.
    "err_positive": "excess_readmission_ratio IS NULL OR excess_readmission_ratio > 0",
    "period_order": "period_end >= period_start",
    # The HRRP payment adjustment factor: a reduction of at most 3 percent (the floor was 0.99 in
    # FY2013 and 0.98 in FY2014), never an increase.
    "paf_range": (
        "payment_adjustment_factor IS NULL OR payment_adjustment_factor BETWEEN 0.97 AND 1.0"
    ),
    # State FIPS plus county FIPS; an unmatched hospital has none. Delta rejects a row whose CHECK
    # evaluates to null (seen on serverless 2026-10-21), so nullable columns say IS NULL OR.
    "county_fips_format": "county_fips IS NULL OR county_fips RLIKE '^[0-9]{5}$'",
    "acs_percent_range": " AND ".join(
        f"({c} IS NULL OR {c} BETWEEN 0 AND 100)"
        for c in ("pct_below_poverty", "pct_uninsured", "pct_age_65_plus", "pct_disability")
    ),
}


def constraint_rules(names: list[str]) -> list[tuple[str, Column]]:
    """(rule name, broken) pairs for quarantine routing."""
    return [(name, ~F.expr(CONSTRAINTS[name])) for name in names]


def add_constraint_statement(table: str, name: str) -> str:
    return f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({CONSTRAINTS[name]})"
