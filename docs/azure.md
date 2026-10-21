# Azure: Key Vault and Data Factory

A small Azure footprint for the parts of the job description that are Azure-specific: Key Vault
holds the pipeline's secrets, and an Azure Data Factory pipeline starts the Databricks job. It
is defined in Bicep (`infra/main.bicep`), deployed into one resource group to capture evidence,
and torn down afterwards. Contoso Health is fictional; nothing here belongs to a real client.

Status: deployed on 2026-10-21 with Chris's approval, and run twice from ADF; evidence in
`docs/evidence/azure.md`. To be torn down with `infra/teardown.sh`.

## What gets deployed

Everything lives in resource group `rg-readmission` (region `eastus` by default; `LOCATION`
overrides it), so deleting the group removes all of it.

| Resource | Name | SKU | Purpose |
|---|---|---|---|
| Key Vault | `kv-readm-<10 chars of uniqueString(rg id)>` | Standard | Holds `census-api-key` and `databricks-token` |
| Data Factory (V2) | `adf-readmission-<uniqueString(rg id)>` | pay per use, Azure integration runtime | Pipeline `run_readmission_pipeline` |
| Role assignment | Key Vault Secrets User on the vault | none | The factory's system-assigned identity reads `databricks-token` |
| Role assignment | Key Vault Secrets User on the vault | none | Service principal `sp-readmission-databricks` (the Databricks job) reads `census-api-key` |
| Role assignment | Key Vault Secrets Officer on the vault | none | The person deploying writes the two secrets |
| Entra ID app and service principal | `sp-readmission-databricks` | none | Created by `infra/deploy.sh`, not Bicep (Bicep does not create app registrations without the Microsoft Graph extension) |

The vault uses Azure RBAC, not access policies. Soft delete is on with the minimum 7-day
retention; purge protection is off, because it cannot be turned off once on and would hold the
vault name for 7 days after teardown. API versions were checked against the Microsoft template
reference on 2026-10-21: Key Vault `2026-05-15` (newest non-preview), Data Factory
`2018-06-01` (the only one), role assignments `2022-04-01`. The pipeline reads Key Vault's
data plane at `api-version=2025-07-01`.

### Expected cost

Prices from the Azure retail price API for `eastus`, 2026-10-21:

| Meter | Price | Use here |
|---|---|---|
| Key Vault Standard operations | $0.03 per 10,000 | a few dozen reads and writes |
| ADF orchestration activity runs | $1.00 per 1,000 | about 46 per pipeline run (3 setup and check activities plus 3 per minute of polling over a 13-minute job) |
| ADF pipeline activity (Wait, Until) | $0.005 per hour | about 15 minutes per run |
| ADF external pipeline activity (Web) | $0.00025 per hour | seconds per run |

Neither resource has a standing monthly charge, and the pipeline has no trigger, so it runs only
when started by hand. One deploy plus two or three pipeline runs costs well under $1.

## Why Key Vault holds these two secrets

- **Census API key.** api.census.gov refuses keyless queries (see `docs/data-sources.md`, Census
  ACS). The fetch task reads it through `readmission.secrets.get_secret`, which now reads Key
  Vault first.
- **Databricks token.** The ADF pipeline needs a token to call the Databricks Jobs API. Keeping
  it in Key Vault means the factory reads it at run time with its own managed identity, and the
  token never appears in ADF's definition, a linked service or the run history.

### How the pipeline code reads Key Vault

`get_secret(scope, key)` keeps its signature, so no caller changed. The backend is chosen by the
Databricks secret scope `readmission` itself, which works on serverless, where a job cannot set
environment variables:

| Scope key | Set by | Meaning |
|---|---|---|
| `key_vault_url` | `infra/deploy.sh`, last | Present: read Key Vault first. Absent: read the scope only (the behavior before this change). |
| `azure_tenant_id`, `azure_client_id`, `azure_client_secret` | `infra/deploy.sh` | The service principal `get_secret` signs in as, with `azure-identity`'s `ClientSecretCredential` |
| `census_api_key` | by hand, earlier | The fallback, and the source `infra/deploy.sh` copies into Key Vault |

Key Vault names allow only letters, digits and dashes, so `census_api_key` is read as
`census-api-key`. A secret the vault does not hold falls back to the scope; a vault that refuses
the service principal raises, so a bad credential is never mistaken for a missing key. Each read
logs which backend answered (`secrets: census_api_key read from Key Vault https://...` or
`... read from secret scope 'readmission'`), never the value. The fetch task reads the key on
every run, even when every ACS vintage is already landed and no Census call follows, so each
scheduled or ADF-triggered run checks the Key Vault path and its fetch log names the backend. Tests use a fake vault client and a fake workspace (`tests/test_secrets.py`), with no
network.

Serverless egress to `login.microsoftonline.com` was confirmed by the 2026-10-21 probe (SPEC.md,
Data Sources). `*.vault.azure.net` was not in that probe; the first job run after deploy is the
check.

## How ADF triggers the Databricks job

The workspace is Databricks Free Edition, not an Azure Databricks workspace, so ADF's Azure
Databricks linked service and Databricks activities do not apply. Pipeline
`run_readmission_pipeline` calls the Jobs API with Web activities instead:

1. `GetDatabricksToken`: GET `<vault>/secrets/databricks-token` with the factory's managed
   identity (`MSI` authentication, resource `https://vault.azure.net`). Output is marked secure.
2. `RunReadmissionPipeline`: POST `<host>/api/2.2/jobs/run-now` with `{"job_id": <id>}` and the
   token as a bearer header. Input is marked secure. Not retried, so a lost response cannot
   start a second run.
3. `WaitForRun`: every 60 seconds, GET `<host>/api/2.2/jobs/runs/get?run_id=<run_id>` until
   `state.result_state` appears (one hour timeout; a clean run takes about 13 minutes).
4. `CheckResult`: fails the ADF run with the Databricks result state unless it is `SUCCESS`, so
   ADF's monitoring shows the job's outcome, not only that the call was accepted.

The host and job id are deployment parameters (`infra/main.bicepparam`), which `infra/deploy.sh`
looks up through the Databricks CLI profile (`databricks jobs list --name readmission_pipeline`).

There is no `AzureKeyVault` linked service. ADF resolves a linked-service secret reference only
inside another linked service's properties, and a Web activity's headers cannot use one. The
linked service that would hold the token is the Azure Databricks one, which a Free Edition
workspace cannot use, so the token is read with a Web activity and the factory's managed
identity instead.

### Why the fetch stays in Databricks

The optional ADF copy activity (download a CMS archive file to the landing volume) is not built.
The 2026-10-21 egress probe showed data.cms.gov, www.cms.gov and api.census.gov reachable from
serverless, so `fetch` runs as the job's first task and lands every source already. A copy in
ADF would need a second path to the landing volume (the Files API with the same token) and would
duplicate the manifest and sha256 bookkeeping in `readmission.ingest.landing`. If serverless
egress ever closes, that is the place to add it.

## Deploy

Prerequisites: Azure CLI with Bicep (`brew install azure-cli`, `az bicep install`), `jq`, the
Databricks CLI profile `readmission`, and `databricks bundle deploy -t dev` done, so the job
exists. Then:

```
az login                       # a user who can create role assignments in the subscription
az account set -s <subscription>
infra/deploy.sh --what-if      # after the resource group exists: what would change
infra/deploy.sh
```

`infra/deploy.sh` is safe to rerun. It creates the service principal if missing (or resets its
secret if the scope lost it) and writes its credentials to the scope, creates the resource group,
deploys `infra/main.bicep`, copies the Census key from the scope into Key Vault, prompts for the
Databricks token (hidden input, Enter skips), and sets `key_vault_url` in the scope last. Every
value moves through a pipe; nothing is printed or written to disk.

Run the pipeline once:

```
az datafactory pipeline create-run -g rg-readmission --factory-name <factory> --name run_readmission_pipeline
```

Offline validation, which needs no login:

```
az bicep build --file infra/main.bicep
az bicep lint --file infra/main.bicep
```

## Teardown

```
infra/teardown.sh
```

It deletes the scope keys that point at Key Vault first (so the pipeline goes back to reading the
Census key from the scope), then the resource group, then purges the soft-deleted vault, then
deletes the service principal and its app registration. `census_api_key` stays in the scope.
Safe to rerun. The Databricks token in Key Vault is gone with the vault; revoke it in the
workspace too (Settings, Developer, Access tokens).
