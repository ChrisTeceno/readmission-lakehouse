"""HRRP performance windows: their length and which earlier window shares no discharges.

Each fiscal year's ratio covers a 3-year window that overlaps the next fiscal year's by 2 years
(see docs/data-sources.md), so FY t against FY t-1 mostly compares the same claims. Gold compares
a fiscal year with its prior non-overlapping window instead.
"""

from datetime import date

from readmission.silver.hrrp_history import HRRP_PERIODS

# FY2024's window runs 07/2019 to 06/2022 but CMS excluded January to June 2020 (COVID).
EXCLUDED_MONTHS = {2024: 6}


def prior_window_year(
    fiscal_year: int, periods: dict[int, tuple[date, date]] = HRRP_PERIODS
) -> int | None:
    """The latest fiscal year whose performance period ends before this one's starts.

    Usually t-3. FY2027's two-year window (07/2023 to 06/2025) starts the day after FY2025's
    ends, so its prior window is FY2025; FY2025's starts after FY2022's COVID-shortened window.
    None for FY2013 to FY2015, whose prior windows predate the program.
    """
    start = periods[fiscal_year][0]
    earlier = [year for year, (_, end) in periods.items() if end < start]
    return max(earlier) if earlier else None


def window_months(fiscal_year: int, periods: dict[int, tuple[date, date]] = HRRP_PERIODS) -> int:
    """Calendar months of discharges in the window: 36 for most years, 30 for FY2022 and FY2024,
    24 for FY2027."""
    start, end = periods[fiscal_year]
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    return months - EXCLUDED_MONTHS.get(fiscal_year, 0)


PRIOR_WINDOW_YEARS = {
    year: prior for year in HRRP_PERIODS if (prior := prior_window_year(year)) is not None
}
WINDOW_MONTHS = {year: window_months(year) for year in HRRP_PERIODS}
