from collections import defaultdict
from datetime import date
from types import SimpleNamespace

from readmission.ingest.contoso import (
    ALL_PAYER_FACTOR,
    CONDITIONS,
    FACILITIES,
    HRRP_WINDOW_MONTHS,
    SEED_MONTHS,
    add_months,
    connection_params,
    discharge_rows,
    facility_rows,
    followup_rows,
    seed_months,
)


def test_roster_is_twelve_real_ccns_from_one_state():
    rows = facility_rows()
    assert len(rows) == 12
    assert len({r[0] for r in rows}) == 12
    assert len({r[2] for r in rows}) == 12
    assert all(len(r[2]) == 6 and r[2].isdigit() for r in rows)
    assert {r[3] for r in rows} == {"OH"}
    assert all(r[1].startswith("Contoso Health ") for r in rows)
    assert all(set(f.hrrp_discharges) <= set(CONDITIONS) for f in FACILITIES)


def test_seed_months_are_36_consecutive_months():
    months = seed_months()
    assert len(months) == SEED_MONTHS == 36
    assert months[0] == date(2023, 9, 1)
    assert months[-1] == date(2026, 8, 1)
    assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)


def test_rows_are_deterministic_per_month():
    month = date(2024, 1, 1)
    assert discharge_rows(month) == discharge_rows(month)
    assert followup_rows(discharge_rows(month)) == followup_rows(discharge_rows(month))
    assert discharge_rows(month) != discharge_rows(date(2024, 2, 1))


def test_discharges_only_for_conditions_cms_reports():
    pairs = {(r[0], r[2]) for r in discharge_rows(date(2024, 1, 1))}
    expected = {(f.facility_id, c) for f in FACILITIES for c in f.hrrp_discharges}
    assert pairs == expected


def test_discharges_scale_with_public_counts():
    totals = defaultdict(int)
    for month in seed_months():
        for facility_id, _, condition, count in discharge_rows(month):
            totals[(facility_id, condition)] += count
    for facility in FACILITIES:
        for condition, public in facility.hrrp_discharges.items():
            expected = public * ALL_PAYER_FACTOR * SEED_MONTHS / HRRP_WINDOW_MONTHS
            # Monthly counts are noisy around the mean; three standard deviations of the total.
            assert abs(totals[(facility.facility_id, condition)] - expected) < 3 * expected**0.5


def test_followup_calls_are_consistent_with_discharges():
    discharges = discharge_rows(date(2025, 3, 1))
    calls = followup_rows(discharges)
    assert len(calls) == 2 * len(discharges)
    by_key = defaultdict(list)
    for facility_id, month, condition, within_72h, attempted, completed in calls:
        assert 0 <= completed <= attempted
        by_key[(facility_id, month, condition)].append((within_72h, attempted))
    for facility_id, month, condition, count in discharges:
        flags = by_key[(facility_id, month, condition)]
        assert sorted(f for f, _ in flags) == [False, True]
        assert sum(a for _, a in flags) <= count


def test_connection_params_use_a_fresh_oauth_token():
    calls = []

    class Postgres:
        def get_endpoint(self, name):
            calls.append(("get_endpoint", name))
            return SimpleNamespace(status=SimpleNamespace(hosts=SimpleNamespace(host="ep-1.db")))

        def generate_database_credential(self, endpoint):
            calls.append(("credential", endpoint))
            return SimpleNamespace(token="tok")

    workspace = SimpleNamespace(
        postgres=Postgres(),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="analyst@contoso.test")),
    )
    params = connection_params(workspace, "projects/p/branches/b/endpoints/e")
    assert params == {
        "host": "ep-1.db",
        "dbname": "databricks_postgres",
        "user": "analyst@contoso.test",
        "password": "tok",
    }
    assert ("credential", "projects/p/branches/b/endpoints/e") in calls
