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
    # State FIPS plus county FIPS. Null passes: an unmatched hospital has no county.
    "county_fips_format": "county_fips RLIKE '^[0-9]{5}$'",
    "acs_percent_range": (
        "pct_below_poverty BETWEEN 0 AND 100 AND pct_uninsured BETWEEN 0 AND 100 "
        "AND pct_age_65_plus BETWEEN 0 AND 100 AND pct_disability BETWEEN 0 AND 100"
    ),
}


def constraint_rules(names: list[str]) -> list[tuple[str, Column]]:
    """(rule name, broken) pairs for quarantine routing."""
    return [(name, ~F.expr(CONSTRAINTS[name])) for name in names]


def add_constraint_statement(table: str, name: str) -> str:
    return f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({CONSTRAINTS[name]})"
