# ADR 0001: Load Contoso's Lakebase database over JDBC, incremental on `updated_at`

Status: accepted (2026-10-21)

## Context

Contoso Health's operational data (facility roster, monthly discharges, follow-up call logs)
lives in a Lakebase Autoscaling Postgres in the same Free Edition workspace. The lakehouse needs
it in Bronze and Silver beside the CMS data, refreshed by the `readmission_pipeline` job on
serverless compute. Free Edition allows one Lakebase project and only serverless compute.

Options considered:

1. **Spark JDBC** (`spark.read.format("jdbc")`, PostgreSQL driver bundled with the runtime), with
   an incremental query on an `updated_at` watermark.
2. **Spark's `postgresql` data source** (`spark.read.format("postgresql")`), the same driver
   behind named options.
3. **Register the database in Unity Catalog** (`databricks postgres create-catalog`) and read it
   as a foreign catalog with SQL, no connection handling in code.
4. **Synced tables / change data feed**, which move data from Delta to Lakebase, or are in beta;
   they solve the opposite direction or add a moving part.
5. **psycopg in the driver**, then `createDataFrame`.

## Decision

Option 1. The `contoso` job task reads each table with Spark JDBC, `query` set to
`SELECT * FROM contoso.<table> WHERE updated_at > '<watermark>'::timestamptz`, appends to
`bronze_contoso_<table>`, and records the largest `updated_at` loaded in `ops_watermarks`.
The password is a one-hour OAuth token from `generate_database_credential`, minted at the start
of each run for the job's own identity; no credential is stored.

A probe on 2026-10-21 (serverless, environment version 2) confirmed that both the `jdbc` and
`postgresql` formats connect with an OAuth token, so JDBC on serverless is not a constraint.

## Why

- **JDBC is the portable pattern.** The same code reads any Postgres, Azure SQL or Oracle a
  client already runs; only the URL and driver change. That is what a forward deployed engagement
  meets most often, and what the job description asks for ("pipelines from databases").
- **Incremental by watermark is explicit and testable.** The query and the watermark logic are
  plain functions with unit tests (`tests/test_contoso_load.py`); a foreign catalog would push
  the filter into a federation layer we cannot test locally.
- **No stored secret.** OAuth tokens from the job identity mean there is no password to rotate
  or leak, which a native Postgres password in a secret scope would need.
- `postgresql` format (option 2) works equally well; `jdbc` was kept because its options are the
  same for every database.
- A UC foreign catalog (option 3) is the better choice for ad hoc SQL over the live database
  (Genie, analysts). It can be added later without touching this load.

## Consequences

- A row is picked up only if its `updated_at` changes. The source sets it on insert; a real
  client system must also set it on update (a trigger), which is a discovery question for the
  client's DBA.
- Postgres `now()` is the transaction start time. A long transaction that commits after a load
  could hold rows older than the watermark, and they would be skipped. The seed commits in one
  short transaction per run, so it cannot happen here; on a busy source, read with a small
  overlap (watermark minus a few minutes) and let Silver's dedup drop the repeats.
- Deletes in the source are not seen. Contoso's tables are append-mostly logs; a source with
  deletes needs soft-delete flags or change data capture.
- Bronze is appended before the watermark moves. A failure between the two re-reads the same
  rows next run; Silver keeps one row per key (latest `updated_at`, then latest `_ingested_at`).
- The job identity must own, or have a Postgres role in, the Lakebase project. Moving the job to
  a service principal means creating that role.
