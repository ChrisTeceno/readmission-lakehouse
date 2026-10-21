"""Expectations that fail the Silver or Gold build, unlike quarantine, which never does.

Each check runs before the table is overwritten, so a failing batch leaves the previous table
in place.
"""

# CMS republishes each dataset whole, and its row counts move by a few percent a year as
# hospitals open, close or merge (HRRP lost 1 percent of rows from FY2025 to FY2026, and no
# fiscal year from FY2013 to FY2027 lost 5 percent of its hospitals). A drop beyond 20 percent
# means a truncated download or a format change that sent rows to quarantine, not a real change
# in hospitals.
MAX_ROW_COUNT_DROP = 0.20

# HRRP leaves the excess readmission ratio blank for hospitals under 25 eligible discharges.
# That is 36.1 percent of FY2026 rows, the highest of any fiscal year (FY2013: 19.1 percent; see
# the history row counts in docs/data-sources.md). 45 percent allows for a normal year and
# catches a parsing break that nulls a large share of ratios.
MAX_ERR_NULL_RATE = 0.45


# Hospitals matched to a county FIPS in the latest ACS vintage. Every hospital matched on
# 2026-10-21; below 98 percent means CMS changed how it writes county names, or the ACS
# geography changed, and the new names need rules in readmission.silver.county_match.
MIN_COUNTY_MATCH_RATE = 0.98


class ExpectationFailed(Exception):
    pass


def check_row_count(table: str, previous: int | None, current: int) -> None:
    """`previous` is the row count after the last successful build, None on the first build."""
    if previous and current < previous * (1 - MAX_ROW_COUNT_DROP):
        raise ExpectationFailed(
            f"{table}: {current} rows, down from {previous}, more than a "
            f"{MAX_ROW_COUNT_DROP:.0%} drop"
        )


def check_err_null_rate(table: str, nulls: int, total: int) -> None:
    if total and nulls / total > MAX_ERR_NULL_RATE:
        raise ExpectationFailed(
            f"{table}: {nulls / total:.1%} of excess_readmission_ratio is null, above "
            f"{MAX_ERR_NULL_RATE:.0%}"
        )


def check_match_rate(table: str, matched: int, total: int) -> None:
    if total and matched / total < MIN_COUNTY_MATCH_RATE:
        raise ExpectationFailed(
            f"{table}: {matched} of {total} hospitals matched a county, below "
            f"{MIN_COUNTY_MATCH_RATE:.0%}"
        )


def check_same_row_count(table: str, expected: int, current: int) -> None:
    """A Gold table built one row per Silver row: any other count means a join fanned out or
    dropped rows."""
    if current != expected:
        raise ExpectationFailed(f"{table}: {current} rows, expected {expected}, one per Silver row")
