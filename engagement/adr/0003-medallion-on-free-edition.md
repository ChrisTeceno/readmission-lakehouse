# ADR 0003: Build the medallion on Free Edition with Bronze append-only and Silver and Gold rebuilt

Status: accepted (2026-10-21)

## Context

Contoso needs CMS, Census, operational and notes data in one place with documented, tested
tables. The workspace is Databricks Free Edition: serverless compute only, five concurrent job
tasks, one 2X-Small SQL warehouse, one Lakebase project, and no account console (`SPEC.md`, Open
Questions; `docs/governance.md`). Unity Catalog is on, in catalog `workspace`, schema
`readmission`.

Options considered:

1. **Bronze, Silver, Gold in Unity Catalog Delta tables**, one job, Silver and Gold rebuilt from
   the layer below on every run.
2. **Incremental merges** into Silver and Gold, keyed on the business key.
3. **A single wide table per source**, no layers.

## Decision

Option 1.

- **Bronze** is append-only, one table per source, every column a string as received, with
  `_ingested_at`, `_source`, `_batch_id` and, for landed files, `_sha256`. Loads are idempotent by
  batch id and hash (`docs/data-sources.md`).
- **Silver** has an explicit `StructType` per table, Delta CHECK constraints, a quarantine table
  for rejected rows and expectations that fail the run before a table is overwritten
  (`docs/governance.md`).
- **Gold** holds the facility-condition facts, penalty mart, Contoso scorecard, model features
  and predictions (`docs/gold.md`).
- One Asset Bundle job, `readmission_pipeline`, runs the tasks on serverless from the project
  wheel (`resources/readmission_pipeline.yml`).

## Why

- **Rebuild is the simpler correct choice at this size.** The clean run of 2026-10-21 wrote
  316,246 Bronze rows, 480,282 Silver rows and 599,782 Gold rows in 12 minutes 58 seconds
  (`docs/data-sources.md`), inside the 15-minute criterion, so incremental merges would add
  logic without a need.
- **Layers give a place for each kind of failure.** Parse problems stop at Silver and land in
  quarantine; business logic problems show up in Gold; Bronze keeps the received bytes so either
  can be rerun.
- **Constraints and expectations make quality testable.** The expectations are plain functions
  with unit tests, run before the overwrite so the previous table stays in place on a failure.

## Consequences

- **Silver is the slow step** (6.6 minutes of the 12.98). If the data grew by an order of
  magnitude the tables with large keys, `silver_hrrp_history` first, would move to merges.
- **No analyst grant takes effect.** Unity Catalog grants go to account-level principals and
  Free Edition has no account console. The job issues the grant and logs it as skipped when the
  principal is missing; on a workspace with an `analysts` account group it applies unchanged
  (`docs/governance.md`).
- **Registry quirk.** Model versions 1 and 2 are stuck in `PENDING_REGISTRATION` from a first
  run on an older MLflow; the job pins a newer one (`docs/model-card.md`).
- **Contoso's database is read directly**, not through the landing volume, because it is a live
  system rather than a file drop (ADR 0001, ADR 0004).
