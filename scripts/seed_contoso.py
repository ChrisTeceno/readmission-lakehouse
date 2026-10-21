"""Seed Contoso Health's operational database on Lakebase with synthetic data.

    uv run python scripts/seed_contoso.py [--profile readmission]   # 36 months, once
    uv run python scripts/seed_contoso.py --append-month             # the next month
    uv run python scripts/seed_contoso.py --reset                    # drop and reseed

Values are deterministic (fixed seed, one generator per row key), so a month has the same rows
whether it came from the first seed or a later --append-month. `updated_at` is the insert time,
which is what the Bronze load's watermark reads. Each run writes in one transaction.
"""

import argparse

import psycopg
from databricks.sdk import WorkspaceClient

from readmission.ingest.contoso import (
    DDL,
    PG_SCHEMA,
    TABLES,
    add_months,
    connection_params,
    discharge_rows,
    facility_rows,
    followup_rows,
    seed_months,
)


def insert_months(cur: psycopg.Cursor, months: list) -> int:
    discharges = [row for month in months for row in discharge_rows(month)]
    cur.executemany(
        f"INSERT INTO {PG_SCHEMA}.discharges"
        " (facility_id, discharge_month, condition, discharges) VALUES (%s, %s, %s, %s)",
        discharges,
    )
    calls = followup_rows(discharges)
    cur.executemany(
        f"INSERT INTO {PG_SCHEMA}.followup_calls (facility_id, discharge_month, condition,"
        " within_72h, calls_attempted, calls_completed) VALUES (%s, %s, %s, %s, %s, %s)",
        calls,
    )
    return len(discharges) + len(calls)


def seed(conn: psycopg.Connection, reset: bool) -> str:
    with conn.cursor() as cur:
        if reset:
            cur.execute(f"DROP SCHEMA IF EXISTS {PG_SCHEMA} CASCADE")
        for statement in DDL:
            cur.execute(statement)
        if cur.execute(f"SELECT count(*) FROM {PG_SCHEMA}.facilities").fetchone()[0]:
            raise SystemExit("already seeded; use --append-month, or --reset to start over")
        cur.executemany(
            f"INSERT INTO {PG_SCHEMA}.facilities (facility_id, facility_name, ccn, state)"
            " VALUES (%s, %s, %s, %s)",
            facility_rows(),
        )
        months = seed_months()
        rows = insert_months(cur, months)
    return f"seeded {len(months)} months from {months[0]} to {months[-1]}: {rows} rows"


def append_month(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        last = cur.execute(f"SELECT max(discharge_month) FROM {PG_SCHEMA}.discharges").fetchone()[0]
        if last is None:
            raise SystemExit("nothing seeded yet; run without --append-month first")
        month = add_months(last, 1)
        rows = insert_months(cur, [month])
    return f"appended {month}: {rows} rows"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed the Contoso operational database.")
    parser.add_argument("--profile", help="Databricks CLI profile that owns the Lakebase project")
    parser.add_argument("--append-month", action="store_true", help="add the next month only")
    parser.add_argument("--reset", action="store_true", help="drop the contoso schema first")
    args = parser.parse_args(argv)
    params = connection_params(WorkspaceClient(profile=args.profile))
    with psycopg.connect(**params, port=5432, sslmode="require") as conn:
        print(append_month(conn) if args.append_month else seed(conn, args.reset))
        with conn.cursor() as cur:
            for table in TABLES:
                count = cur.execute(f"SELECT count(*) FROM {PG_SCHEMA}.{table}").fetchone()[0]
                print(f"{PG_SCHEMA}.{table}: {count} rows")


if __name__ == "__main__":
    main()
