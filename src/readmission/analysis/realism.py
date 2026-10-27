"""Realism check for Contoso's synthetic operational data (docs/synthetic-data.md).

Every synthetic value here is computed from the generator in `readmission.ingest.contoso`
itself: it is seeded and deterministic, so the check needs no workspace and gives the numbers
the Lakebase seed holds. Every benchmark is an independent public figure with its source and
the date it was checked. A check whose synthetic value is set by the generator's own inputs is
labelled `by construction`: agreement there proves nothing.
"""

from collections import defaultdict
from dataclasses import dataclass

from readmission.ingest.contoso import (
    ALL_PAYER_FACTOR,
    FACILITIES,
    HRRP_WINDOW_MONTHS,
    SEED_MONTHS,
    discharge_rows,
    followup_rows,
    seed_months,
)

MATCHES = "matches"
PLAUSIBLE = "plausible"
OFF = "off"
BY_CONSTRUCTION = "by construction, proves nothing"
NO_BENCHMARK = "no benchmark"

# Medicare Inpatient Hospitals by Provider, data year 2023 (Medicare fee-for-service, MedPAR),
# `Tot_Dschrgs`: https://data.cms.gov/data-api/v1/dataset/ad17abc4-0e93-4828-bba5-e8f39ddafbdb/data
# Hospital Provider Cost Report, 2023 file, Worksheet S-3 `Total Discharges Title XVIII` and
# `Total Discharges (V + XVIII + XIX + Unknown)`, the hospital's cost-report year starting in
# 2023: https://data.cms.gov/data-api/v1/dataset/f08d4c46-c0e4-4bcb-a54a-c62c87d59251/data
# Both checked 2026-10-27. CCN: (MedPAR FFS, cost report Title XVIII, cost report all payers).
CCN_DISCHARGES_2023 = {
    "360085": (8063, 7493, 46949),
    "360155": (3917, 3735, 15473),
    "360068": (7693, 7364, 33135),
    "360051": (9611, 9083, 45242),
    "360027": (4534, 4217, 24767),
    "360039": (3634, 3536, 13826),
    "360072": (2127, 2096, 8727),
    "360040": (910, 903, 3211),
    "360054": (953, 947, 3528),
    "360218": (1861, 1823, 8003),
    "360125": (1572, 1560, 4242),
    "360029": (587, 601, 2307),
}

# Share of each condition's stays with Medicare (fee-for-service and managed care) as expected
# payer, all US hospitals, 2018: HCUP Statistical Brief #277, Supplemental Table 1,
# https://hcup-us.ahrq.gov/reports/statbriefs/sb277-Top-Reasons-Hospital-Stays-2018.jsp
# (checked 2026-10-27). Hip/knee uses the osteoarthritis row and CABG the coronary
# atherosclerosis row, the principal diagnoses behind most of those procedures.
MEDICARE_SHARE_2018 = {
    "AMI": 0.575,
    "HF": 0.735,
    "PN": 0.631,
    "COPD": 0.692,
    "HIP_KNEE": 0.575,
    "CABG": 0.588,
}

# Ohio Medicare beneficiaries, annual: (total, Original Medicare). Medicare Monthly Enrollment,
# https://data.cms.gov/data-api/v1/dataset/cf1be15c-b102-4735-9bb1-1f5db074b76d/data
# (state OH, month `Year`, checked 2026-10-27).
OHIO_ENROLLMENT = {
    2021: (2407758, 1227231),
    2022: (2441883, 1175250),
    2023: (2489013, 1143769),
    2024: (2536384, 1122290),
}
# Years' weight in the HRRP performance period, 07/2021 to 06/2024.
HRRP_YEAR_WEIGHTS = {2021: 0.5, 2022: 1.0, 2023: 1.0, 2024: 0.5}

# Admissions in winter (December to February) over summer (June to August), all payers, New
# York and California, 2005 to 2011: Butala et al., Health Serv Res 2018;53(4):2185-2202,
# https://pmc.ncbi.nlm.nih.gov/articles/PMC6051982/ (checked 2026-10-27).
WINTER_EXCESS = {"PN": 0.513, "HF": 0.177, "AMI": 0.063}
WINTER = (12, 1, 2)
SUMMER = (6, 7, 8)

# Share of discharged patients reached by a post-discharge nurse call.
# Lukanski et al., J Healthc Qual 2023;45(6):315-323, PMID 37788411: 22 hospitals, calls
# targeted within 72 hours, "57.92% of patients were successfully contacted within 7 days".
# https://pubmed.ncbi.nlm.nih.gov/37788411/ (checked 2026-10-27).
CALL_REACH_7_DAYS = 0.5792
# Harrison et al., J Gen Intern Med 2014;29(11):1519-25, PMID 25103122: two attempts within 72
# hours; 155 readmissions (5.8 percent) among patients who completed the call and 123 (8.6
# percent) among those who did not, so about 2,672 patients completed it: 65 percent of the
# 4,102 those counts imply, 49 percent of the 5,507 eligible. The abstract does not say which
# denominator applies. https://pubmed.ncbi.nlm.nih.gov/25103122/ (checked 2026-10-27).
HARRISON_COMPLETED = 155 / 0.058
CALL_REACH_72_HOURS = (
    HARRISON_COMPLETED / 5507,
    HARRISON_COMPLETED / (HARRISON_COMPLETED + 123 / 0.086),
)


@dataclass(frozen=True)
class Check:
    property: str
    benchmark: str
    source: str
    synthetic: str
    gap: str
    verdict: str


def generated() -> tuple[list[tuple], list[tuple]]:
    """Every discharge and follow-up row the 36-month seed writes."""
    discharges = [row for month in seed_months() for row in discharge_rows(month)]
    return discharges, followup_rows(discharges)


def volume_totals(discharges: list[tuple]) -> dict[tuple[str, str], int]:
    totals = defaultdict(int)
    for facility_id, _, condition, count in discharges:
        totals[(facility_id, condition)] += count
    return dict(totals)


def ohio_ffs_share() -> float:
    """Share of Ohio Medicare beneficiaries in Original Medicare over the HRRP window."""
    weight = sum(HRRP_YEAR_WEIGHTS.values())
    return (
        sum(w * OHIO_ENROLLMENT[y][1] / OHIO_ENROLLMENT[y][0] for y, w in HRRP_YEAR_WEIGHTS.items())
        / weight
    )


def condition_all_payer_factor(condition: str) -> float:
    """All-payer discharges per Medicare fee-for-service discharge for a condition, if Ohio's
    Medicare patients split between Original Medicare and Medicare Advantage as its enrollees do."""
    return 1 / (MEDICARE_SHARE_2018[condition] * ohio_ffs_share())


def network_all_payer_factor() -> float:
    """`condition_all_payer_factor` weighted by Contoso's HRRP discharges."""
    public = {}
    for facility in FACILITIES:
        for condition, count in facility.hrrp_discharges.items():
            public[condition] = public.get(condition, 0) + count
    return sum(public[c] * condition_all_payer_factor(c) for c in public) / sum(public.values())


def all_cause_ratio() -> tuple[float, float, float]:
    """All-payer over Medicare fee-for-service discharges, all conditions, across the 12 CCNs:
    network, lowest facility and highest facility."""
    ratios = [total / ffs for ffs, _, total in CCN_DISCHARGES_2023.values()]
    network = sum(t for _, _, t in CCN_DISCHARGES_2023.values()) / sum(
        f for f, _, _ in CCN_DISCHARGES_2023.values()
    )
    return network, min(ratios), max(ratios)


def winter_excess(discharges: list[tuple], condition: str) -> float:
    """Winter discharges over summer discharges, minus one, across the network."""
    by_month = defaultdict(int)
    for _, month, row_condition, count in discharges:
        if row_condition == condition:
            by_month[month.month] += count
    winter = sum(by_month[m] for m in WINTER)
    summer = sum(by_month[m] for m in SUMMER)
    return winter / summer - 1


def call_shares(discharges: list[tuple], calls: list[tuple]) -> dict[str, float]:
    """Shares of all discharges: attempted, completed at any time, completed within 72 hours."""
    total = sum(r[3] for r in discharges)
    attempted = sum(r[4] for r in calls)
    completed = sum(r[5] for r in calls)
    early = sum(r[5] for r in calls if r[3])
    return {
        "attempted": attempted / total,
        "completed": completed / total,
        "completed_72h": early / total,
    }


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _points(x: float) -> str:
    return f"{100 * x:+.1f} points"


def checks() -> list[Check]:
    discharges, calls = generated()
    rows = []

    totals = volume_totals(discharges)
    gaps = []
    expected_total = 0.0
    for facility in FACILITIES:
        for condition, public in facility.hrrp_discharges.items():
            expected = public * ALL_PAYER_FACTOR * SEED_MONTHS / HRRP_WINDOW_MONTHS
            expected_total += expected
            gaps.append(totals[(facility.facility_id, condition)] / expected - 1)
    rows.append(
        Check(
            "36-month volume per facility and condition",
            f"CMS eligible discharges x {ALL_PAYER_FACTOR}, the generator's own input:"
            f" {expected_total:,.0f}",
            "CMS HRRP FY2026 (dataset 9n3s-kdb3)",
            f"{sum(totals.values()):,} over {len(gaps)} pairs",
            f"{sum(totals.values()) / expected_total - 1:+.1%} in total;"
            f" {min(gaps):+.1%} to {max(gaps):+.1%} per pair (noise)",
            BY_CONSTRUCTION,
        )
    )

    network, low, high = all_cause_ratio()
    factor = network_all_payer_factor()
    rows.append(
        Check(
            "All-payer discharges per Medicare FFS discharge, HRRP conditions",
            f"{factor:.2f} (Contoso mix; {min(map(condition_all_payer_factor, MEDICARE_SHARE_2018)):.2f}"
            f" HF to {max(map(condition_all_payer_factor, MEDICARE_SHARE_2018)):.2f} AMI and"
            f" hip/knee); all conditions at these 12 CCNs {network:.2f} ({low:.2f} to {high:.2f})",
            "HCUP SB #277 (2018 payer shares); CMS Medicare Monthly Enrollment (Ohio 2021 to"
            " 2024); CMS cost reports and MedPAR 2023",
            f"{ALL_PAYER_FACTOR:.2f}",
            f"{ALL_PAYER_FACTOR / factor - 1:+.0%} against the condition estimate",
            OFF,
        )
    )

    # Judged, not thresholded: pneumonia's peak is stronger than the benchmark's, heart failure's
    # is close to it, and AMI's 6 percent winter rise is missing from the generator altogether.
    for condition, verdict in (("PN", PLAUSIBLE), ("HF", MATCHES), ("AMI", OFF)):
        synthetic = winter_excess(discharges, condition)
        rows.append(
            Check(
                f"Winter (Dec to Feb) over summer (Jun to Aug) admissions, {condition}",
                f"{WINTER_EXCESS[condition]:+.1%}",
                "Butala 2018, HCUP SID NY and CA",
                f"{synthetic:+.1%}",
                _points(synthetic - WINTER_EXCESS[condition]),
                verdict,
            )
        )
    rows.append(
        Check(
            "Winter over summer admissions, COPD; none for hip/knee and CABG",
            "none checked",
            "",
            f"COPD {winter_excess(discharges, 'COPD'):+.1%}; hip/knee"
            f" {winter_excess(discharges, 'HIP_KNEE'):+.1%} and CABG"
            f" {winter_excess(discharges, 'CABG'):+.1%}, noise around a flat mean",
            "",
            NO_BENCHMARK,
        )
    )

    shares = call_shares(discharges, calls)
    low, high = CALL_REACH_72_HOURS
    rows.append(
        Check(
            "Discharges with a completed call within 72 hours",
            f"{_pct(low)} to {_pct(high)} completed (a structured program: two attempts within"
            " 72 hours, one academic medicine service)",
            "Harrison 2014",
            _pct(shares["completed_72h"]),
            f"{_points(shares['completed_72h'] - low)} to {_points(shares['completed_72h'] - high)}",
            PLAUSIBLE,
        )
    )
    rows.append(
        Check(
            "Discharges with a completed call at any time",
            f"{_pct(CALL_REACH_7_DAYS)} reached within 7 days (22 hospitals, all inpatients)",
            "Lukanski 2023",
            _pct(shares["completed"]),
            _points(shares["completed"] - CALL_REACH_7_DAYS),
            PLAUSIBLE,
        )
    )
    rows.append(
        Check(
            "Readmission rates",
            "not generated: every rate in the analysis is CMS's own, per CCN",
            "CMS HRRP",
            "none",
            "",
            NO_BENCHMARK,
        )
    )
    rows.append(
        Check(
            "Age, sex, payer, length of stay, diagnosis codes",
            "not generated: the database holds monthly counts only",
            "",
            "none",
            "",
            NO_BENCHMARK,
        )
    )
    return rows


def markdown(rows: list[Check]) -> str:
    lines = [
        "| Property | Benchmark | Source | Synthetic | Gap | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.property} | {r.benchmark} | {r.source} | {r.synthetic} | {r.gap} | {r.verdict} |"
        )
    return "\n".join(lines)
