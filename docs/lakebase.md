# Contoso's operational database on Lakebase

Contoso Health's operational Postgres is a Lakebase Autoscaling project in the Free Edition
workspace. What is in it and how it maps to Bronze and Silver: `docs/data-sources.md`, section
"Contoso operational database". Why the load uses JDBC: `engagement/adr/0001-contoso-lakebase-connector.md`.

## Which Lakebase

Databricks has two generations of Lakebase: provisioned database instances (`databricks database
...`, bundle resource `database_instances`) and Autoscaling projects (`databricks postgres ...`,
bundle resources `postgres_projects`, `postgres_branches`, `postgres_endpoints` and so on).
Free Edition offers "one Lakebase project per account, with scale-to-zero compute" (Free Edition
limitations page, checked 2026-10-21), so this is an Autoscaling project. Databricks CLI v1.21.0
supports `postgres_projects` in Asset Bundles, so the project is a bundle resource, not a set of
CLI commands.

## The project (`resources/lakebase.yml`)

`databricks bundle deploy -t dev` creates project `contoso-ops` (Postgres 17). Creating a project
also creates:

| Resource | Name |
|---|---|
| Branch | `projects/contoso-ops/branches/production` |
| Read-write compute | `projects/contoso-ops/branches/production/endpoints/primary` |
| Database | `databricks_postgres` (the seed adds schema `contoso`) |
| Postgres role | the project owner's Databricks user name |

Compute: a request for 0.5 CU (the API's stated minimum) came back as 1 CU, so the bundle pins
min and max at 1 CU to avoid drift on every deploy. It scales to zero after 300 seconds idle
and wakes on the next connection.

Check it:

```
databricks postgres get-project projects/contoso-ops
databricks postgres list-endpoints projects/contoso-ops/branches/production
```

The bundle shares deploy state with every other resource in `databricks.yml`. Removing
`resources/lakebase.yml` and deploying soft-deletes the project (`purge_on_delete` is unset, so
`databricks postgres undelete-project` can bring it back).

## Credentials

No password exists anywhere. Every connection, local or in the job, asks Lakebase for a
one-hour OAuth token for whoever is running and uses it as the Postgres password
(`readmission.ingest.contoso.connection_params`):

```python
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
endpoint = "projects/contoso-ops/branches/production/endpoints/primary"
host = w.postgres.get_endpoint(endpoint).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=endpoint).token
user = w.current_user.me().user_name
```

Locally that identity is the CLI profile; in the `readmission_pipeline` job it is the job's run
as identity, the project owner, whose Postgres role exists from project creation. The token is
checked only at login, so one token covers a whole load. Password login
(`enable_pg_native_login`) stays off. So there is nothing to put in a secret scope, and none was
created for Lakebase; if the job later runs as a service principal, that principal needs a
Postgres role (`databricks postgres create-role` with `identity_type: SERVICE_PRINCIPAL`) and
still uses tokens.

The serverless environment's preinstalled `databricks-sdk` has no `postgres` service; the project
wheel requires `databricks-sdk>=0.155.0`, which the job environment installs.

## Seeding

```
uv run python scripts/seed_contoso.py --profile readmission   # 36 months, 2023-09 to 2026-08
uv run python scripts/seed_contoso.py --append-month          # the next month
uv run python scripts/seed_contoso.py --reset                 # drop the schema and reseed
```

A plain run refuses to seed twice. Each run is one transaction; `updated_at` is the insert time.

## Incremental load test (2026-10-21)

| Step | `facilities` | `discharges` | `followup_calls` |
|---|---|---|---|
| Seed: rows in Postgres | 12 | 1,800 | 3,600 |
| Run 1 (full load): rows appended to Bronze | 12 | 1,800 | 3,600 |
| `--append-month` (2026-09): rows added in Postgres | 0 | 50 | 100 |
| Run 2: rows appended to Bronze | 0 | 50 | 100 |
| Run 3 (job task, `bundle run readmission_pipeline --only contoso`): rows appended | 0 | 0 | 0 |
| Silver after run 3 | 12 | 1,850 | 3,700 |

Runs 1 and 2 were one-off serverless runs of the same wheel entry point; run 3 was the bundled
job task. Each took about a minute, most of it environment start.

To repeat it: `uv run python scripts/seed_contoso.py --append-month`, then
`databricks bundle run readmission_pipeline -t dev --only contoso`; the task log prints the rows
appended per table.
