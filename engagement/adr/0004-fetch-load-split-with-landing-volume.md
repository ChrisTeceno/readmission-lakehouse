# ADR 0004: Split ingestion into fetch and load around a landing volume

Status: accepted (2026-10-21)

## Context

Free Edition serverless compute reaches only "a limited set of trusted domains" (Databricks
documentation, checked 2026-10-21), so it was not known whether data.cms.gov, cms.gov and
api.census.gov were reachable from a job. The sources also change under us: CMS republishes
files, and a CMS download sometimes ends early with HTTP 200 and a truncated body
(`docs/data-sources.md`).

Options considered:

1. **Read the internet inside the Bronze load.**
2. **Fetch and load as separate steps**, with raw files landed unchanged in a Unity Catalog volume
   between them.
3. **Fetch outside Databricks** (a scheduled GitHub Actions workflow or Data Factory) and upload.

## Decision

Option 2. Fetch is pure Python with no Spark: it downloads with retries on 429, 5xx and
timeouts and lands each file unchanged in the managed volume `workspace.readmission.landing`
(`resources/landing.yml`) through the Files API, with a `_manifest.json` per batch (source URL,
dataset id, snapshot date, sha256, row count) written last. Load reads only the volume and skips a
batch whose id is already in Bronze or whose files all match a loaded sha256.

An egress probe on 2026-10-21 showed serverless reaches data.cms.gov, www.cms.gov,
api.census.gov, login.microsoftonline.com and pypi.org (`SPEC.md`, Data Sources). So fetch runs
as the first task of the Databricks job and the Actions fallback of option 3 is not used.

## Why

- **Bronze is reproducible.** The landed bytes are exactly what CMS published, so Bronze can be
  rebuilt without going back to the network.
- **Load never touches the internet**, so a CMS outage fails fetch, not the load, and the
  failure is visible as its own task.
- **Where fetch runs can change without a code change.** If a domain is blocked later, the same
  fetch runs from Actions or Data Factory.
- **Idempotence is cheap.** A manifest per batch and a hash check mean a second run with no
  source changes appends nothing.

## Consequences

- **CSV over the datastore API.** Fetch reads the dataset metadata and lands the CSV its
  `downloadURL` names: one request takes 1 to 3 seconds where the datastore takes 40 to 55
  seconds per 1,500-row page. If CMS's `modified` date matches the latest manifest, nothing is
  landed (`docs/data-sources.md`).
- **Archive snapshots are chosen at run time**, never hardcoded: each fiscal year keeps the
  latest snapshot that carries it, opened through HTTP Range requests so the zip is not
  downloaded whole.
- **Census needs a key.** api.census.gov redirects keyless data queries to `missing_key.html`;
  the key lives in a secret scope and is never written to a manifest, file or error message.
  The Key Vault path is pending (SPEC task 3.1).
- **Contoso's Lakebase is the exception**: it is a live database, read incrementally over JDBC
  (ADR 0001), not landed as files.
- **The volume holds raw data of public origin only.** With real PHI the volume would need its
  own grants (`engagement/phi-note.md`).
