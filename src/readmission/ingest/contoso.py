"""Contoso Health's operational database: the fictional client's own Postgres on Lakebase.

Contoso is fictional. Its 12 facilities are real Ohio hospitals' public CMS Certification Numbers
(CCNs) relabeled with invented names, so Contoso data joins to the public HRRP tables; no real
hospital's data is altered and every operational number below is synthetic.

This module holds what both sides share: the facility roster, the Postgres DDL, the
deterministic generators `scripts/seed_contoso.py` writes, and the connection settings the seed
and the Bronze load use. Credentials are never stored: each connection gets a one-hour OAuth
token from `generate_database_credential` for whoever is running (a user locally, the job's
identity on Databricks).
"""

import math
import random
from dataclasses import dataclass
from datetime import date
from typing import Any

# The Lakebase project, branch and compute from resources/lakebase.yml, and the database the
# project creates by default.
ENDPOINT = "projects/contoso-ops/branches/production/endpoints/primary"
DATABASE = "databricks_postgres"
PG_SCHEMA = "contoso"
TABLES = ["facilities", "discharges", "followup_calls"]

SEED = 20261021
CONDITIONS = ["AMI", "HF", "PN", "COPD", "HIP_KNEE", "CABG"]
FIRST_MONTH = date(2023, 9, 1)
SEED_MONTHS = 36

# HRRP discharges count Medicare fee-for-service patients only; Contoso's own systems count every
# payer, so monthly volumes are the public count spread over its 36-month window times this.
# 3.2 rounds the public estimate of 3.21 all-payer discharges per fee-for-service discharge for
# Contoso's condition mix (HCUP 2018 Medicare payer shares and Ohio's Original Medicare share,
# docs/synthetic-data.md section 6); it replaced a round 2.0 on 2026-10-28.
ALL_PAYER_FACTOR = 3.2
HRRP_WINDOW_MONTHS = 36

# Respiratory admissions and heart failure peak in winter; elective joints and CABG do not.
WINTER_AMPLITUDE = {"PN": 0.25, "COPD": 0.2, "HF": 0.1}


@dataclass(frozen=True)
class Facility:
    facility_id: str
    facility_name: str
    ccn: str
    state: str
    # Eligible discharges per HRRP measure in the FY2026 file (CMS dataset 9n3s-kdb3, performance
    # period 07/01/2021 to 06/30/2024, downloaded 2026-10-21). A measure CMS left blank (`N/A`,
    # fewer than 25 cases, or no such service line) is absent, and Contoso reports no volume for
    # it.
    hrrp_discharges: dict[str, int]


FACILITIES = [
    Facility(
        "CH01",
        "Contoso Health Alder",
        "360085",
        "OH",
        {"AMI": 214, "HF": 871, "PN": 388, "COPD": 187, "HIP_KNEE": 157, "CABG": 131},
    ),
    Facility(
        "CH02",
        "Contoso Health Birch",
        "360155",
        "OH",
        {"AMI": 165, "HF": 427, "PN": 545, "COPD": 236, "HIP_KNEE": 255},
    ),
    Facility(
        "CH03",
        "Contoso Health Cedar",
        "360068",
        "OH",
        {"AMI": 519, "HF": 885, "PN": 787, "COPD": 228, "CABG": 318},
    ),
    Facility(
        "CH04",
        "Contoso Health Dogwood",
        "360051",
        "OH",
        {"AMI": 337, "HF": 986, "PN": 684, "COPD": 250, "CABG": 138},
    ),
    Facility(
        "CH05",
        "Contoso Health Elm",
        "360027",
        "OH",
        {"AMI": 238, "HF": 605, "PN": 630, "COPD": 170},
    ),
    Facility(
        "CH06",
        "Contoso Health Fir",
        "360039",
        "OH",
        {"AMI": 253, "HF": 568, "PN": 531, "COPD": 202},
    ),
    Facility(
        "CH07",
        "Contoso Health Hawthorn",
        "360072",
        "OH",
        {"AMI": 132, "HF": 351, "PN": 367, "COPD": 103},
    ),
    Facility(
        "CH08",
        "Contoso Health Juniper",
        "360040",
        "OH",
        {"AMI": 86, "HF": 183, "PN": 149, "COPD": 96},
    ),
    Facility(
        "CH09",
        "Contoso Health Linden",
        "360054",
        "OH",
        {"AMI": 117, "HF": 172, "PN": 187, "COPD": 75},
    ),
    Facility(
        "CH10",
        "Contoso Health Maple",
        "360218",
        "OH",
        {"AMI": 121, "HF": 309, "PN": 240, "COPD": 106},
    ),
    Facility("CH11", "Contoso Health Oak", "360125", "OH", {"HF": 247, "PN": 245, "COPD": 121}),
    Facility("CH12", "Contoso Health Spruce", "360029", "OH", {"HF": 119, "PN": 74}),
]

DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.facilities (
        facility_id text PRIMARY KEY,
        facility_name text NOT NULL,
        ccn char(6) NOT NULL UNIQUE,
        state char(2) NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now()
    )""",
    f"""CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.discharges (
        facility_id text NOT NULL REFERENCES {PG_SCHEMA}.facilities,
        discharge_month date NOT NULL,
        condition text NOT NULL,
        discharges integer NOT NULL CHECK (discharges >= 0),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (facility_id, discharge_month, condition)
    )""",
    f"""CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.followup_calls (
        facility_id text NOT NULL REFERENCES {PG_SCHEMA}.facilities,
        discharge_month date NOT NULL,
        condition text NOT NULL,
        within_72h boolean NOT NULL,
        calls_attempted integer NOT NULL CHECK (calls_attempted >= 0),
        calls_completed integer NOT NULL CHECK (calls_completed BETWEEN 0 AND calls_attempted),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (facility_id, discharge_month, condition, within_72h)
    )""",
    *[f"CREATE INDEX IF NOT EXISTS {t}_updated_at ON {PG_SCHEMA}.{t} (updated_at)" for t in TABLES],
]


def add_months(month: date, n: int) -> date:
    index = month.year * 12 + month.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def seed_months() -> list[date]:
    return [add_months(FIRST_MONTH, i) for i in range(SEED_MONTHS)]


def _rng(*parts: object) -> random.Random:
    """One generator per row key, so a month's rows are the same whenever it is generated."""
    return random.Random(":".join(str(p) for p in (SEED, *parts)))


def _noisy(rng: random.Random, mean: float) -> int:
    return max(0, round(rng.gauss(mean, math.sqrt(mean))))


def facility_rows() -> list[tuple]:
    return [(f.facility_id, f.facility_name, f.ccn, f.state) for f in FACILITIES]


def discharge_rows(month: date) -> list[tuple]:
    """(facility_id, discharge_month, condition, discharges) for one month."""
    rows = []
    for facility in FACILITIES:
        for condition in CONDITIONS:
            public = facility.hrrp_discharges.get(condition)
            if not public:
                continue
            season = 1 + WINTER_AMPLITUDE.get(condition, 0.0) * math.cos(
                2 * math.pi * (month.month - 1) / 12
            )
            mean = public / HRRP_WINDOW_MONTHS * ALL_PAYER_FACTOR * season
            rng = _rng("discharges", facility.facility_id, condition, month)
            rows.append((facility.facility_id, month, condition, _noisy(rng, mean)))
    return rows


def _facility_rates(facility_id: str) -> tuple[float, float, float]:
    """Attempt rate, completion rate and share of attempts made within 72 hours: fixed per
    facility, so facilities differ the way a follow-up program's sites would."""
    rng = _rng("rates", facility_id)
    return rng.uniform(0.55, 0.9), rng.uniform(0.6, 0.85), rng.uniform(0.6, 0.85)


def followup_rows(discharges: list[tuple]) -> list[tuple]:
    """(facility_id, discharge_month, condition, within_72h, calls_attempted, calls_completed),
    two rows per discharge row. Calls made after 72 hours reach fewer patients."""
    rows = []
    for facility_id, month, condition, count in discharges:
        attempt_rate, completion_rate, early_share = _facility_rates(facility_id)
        rng = _rng("followup", facility_id, condition, month)
        attempted = min(count, round(count * attempt_rate * rng.uniform(0.9, 1.1)))
        early = round(attempted * early_share)
        for within_72h, calls, rate in (
            (True, early, completion_rate),
            (False, attempted - early, completion_rate * 0.7),
        ):
            completed = min(calls, round(calls * rate * rng.uniform(0.9, 1.1)))
            rows.append((facility_id, month, condition, within_72h, calls, completed))
    return rows


def connection_params(workspace: Any, endpoint: str = ENDPOINT) -> dict[str, str]:
    """Host, user and a fresh OAuth token for the Lakebase compute.

    `workspace` is a `databricks.sdk.WorkspaceClient`. The Postgres role is the caller's
    Databricks user name; the project owner's role exists from creation. The token lasts an
    hour and is checked only at login, so one token covers a whole load.
    """
    host = workspace.postgres.get_endpoint(endpoint).status.hosts.host
    token = workspace.postgres.generate_database_credential(endpoint=endpoint).token
    return {
        "host": host,
        "dbname": DATABASE,
        "user": workspace.current_user.me().user_name,
        "password": token,
    }
