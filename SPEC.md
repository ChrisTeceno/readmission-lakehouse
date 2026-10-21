# Spec: readmission-lakehouse

Status: draft for review (2026-10-21)

## Objective

A public portfolio project that demonstrates every requirement in a Senior Forward Deployed Engineer (Data & AI, Databricks) job description, framed as a delivery engagement for a fictional client.

**The fictional client:** "Contoso Health" (Microsoft's stock fictional company, so nobody mistakes it for a real hospital), a regional hospital network. Their question:

> "CMS penalizes us under the Hospital Readmissions Reduction Program. Which of our facilities and conditions drive the penalty, what would actually reduce it, and does the penalty itself change hospital behavior?"

**What gets built:**

1. A Databricks medallion lakehouse (Bronze, Silver, Gold) ingesting CMS Provider Data APIs, CMS archive files, Census ACS, Contoso's own operational database (a Lakebase Postgres seeded with synthetic facility and discharge data), and the existing MTSamples clinical notes table.
2. An MLflow-tracked model predicting which hospital-condition pairs land above the penalty cutoff next cycle, served as a scheduled batch scoring job that writes predictions to Gold.
3. A causal analysis of whether being penalized changes a hospital's later readmission ratio (regression discontinuity at the penalty cutoff, with a difference-in-differences check), plus a designed pilot: a powered experiment Contoso could run to test a discharge follow-up intervention.
4. An LLM step that extracts structured readmission risk factors from the clinical notes, with an evaluation against a hand-labeled sample.
5. A Databricks AI/BI dashboard, a Genie space, and a Power BI project file on the Gold tables.
6. Engagement documents (discovery, requirements, decisions, readout), an AI-assisted development log, a portfolio case study, and a 3 to 5 minute video.

**Who reads it:** a hiring manager skimming the README for 2 minutes, and an interviewer who opens the code for 15.

### Requirement coverage map

Every job description line maps to a concrete artifact. The README carries this table.

| JD requirement | Where it is shown |
|---|---|
| Databricks, Spark/PySpark, Delta | `src/` pipeline code, Delta tables in Unity Catalog `workspace.readmission` |
| Medallion architecture | `bronze/`, `silver/`, `gold/` modules and `docs/architecture.md` diagram |
| Pipelines from APIs, databases, other sources | CMS datastore API (paginated JSON), Census ACS API, Contoso Lakebase Postgres (JDBC, incremental by watermark), CMS archive zips, MTSamples Delta table |
| MLflow experiments | `ml/train_penalty_model.py`, experiment `/Shared/readmission-penalty`, registered model |
| Data governance, schema enforcement, testing, quality | Explicit Silver schemas, Delta constraints, expectation checks, quarantine table, pytest suite; Unity Catalog table and column comments, tags, grants to an analyst group, lineage |
| Orchestration and monitoring (Databricks Workflows, ADF) | Asset Bundle job with schedule, retries, task dependencies and failure notifications; `ops_run_log` and `ops_dq_metrics` tables feeding a pipeline-health dashboard page; ADF pipeline that lands external files and triggers the job |
| Azure Data Factory, Key Vault | `infra/` Bicep: Key Vault holding the Census key and Databricks token, ADF pipeline |
| Power BI | `powerbi/` `.pbip` project over Gold tables |
| Dashboards, Genie | AI/BI dashboard JSON in the bundle, Genie space with documented sample questions |
| Statistics: experimental design, hypothesis testing, causal inference | `docs/analysis-plan.md` (hypotheses before results), RDD + DiD notebook, pilot design with power calculation, explicit tests with multiple-comparison correction |
| Exploration, anomalies, storytelling | EDA notebook, anomaly flags in Gold, the readout deck |
| AI-assisted development and judgment | `docs/ai-assisted-dev.md`: concrete cases tied to PRs of AI output that was accepted, corrected or rejected and why; secret scanning (gitleaks) in CI; LLM outputs schema-validated and evaluated |
| LLMs and AI/ML product work | Notes extraction step with eval metrics; batch scoring job using the registered model's champion alias; Genie space |
| Git workflows | PR-per-task history, CI on GitHub Actions |
| Client communication, working with PM and design | `engagement/`: discovery notes, user stories with acceptance criteria, a low-fidelity dashboard wireframe agreed before building, ADRs, status update, readout deck, executive summary for a non-technical reader |
| Healthcare and consulting bonus | The domain; a short PHI note (no PHI used, how real PHI would change the design); the engagement framing |

## Tech Stack

- Databricks Free Edition (serverless), workspace already provisioned. Unity Catalog `workspace` catalog, schema `readmission`.
- Databricks Asset Bundles via Databricks CLI (v0.2xx, pin at setup).
- Python 3.11, PySpark (runtime-provided), Delta Lake.
- Local dev: `uv`, `pyspark` + `delta-spark` for unit tests, `pytest`, `ruff`.
- ML: scikit-learn / LightGBM, MLflow (Databricks-hosted tracking).
- Stats: `statsmodels`, `rdrobust` (or a hand-rolled local linear RDD if the package is a problem).
- LLM: a Databricks Foundation Model API endpoint if Free Edition exposes one, otherwise the Claude API. Decided in task 4.1.
- Azure (free tier, torn down after recording): Key Vault, Data Factory, defined in Bicep.
- Power BI: `.pbip` (PBIR format) authored in Power BI Service or a Windows VM.
- CI: GitHub Actions (lint, unit tests, `databricks bundle validate`).

## Data Sources

**Network constraint (Free Edition).** Serverless compute on Free Edition only reaches "a limited set of trusted domains" (Databricks docs, Free Edition limitations, checked 2026-10-21). data.cms.gov, cms.gov and api.census.gov may be blocked. Ingestion is therefore split:
- **Fetch** (pure Python, no Spark) downloads from the source and uploads raw files to the landing volume `/Volumes/workspace/readmission/landing/<source>/<batch_id>/` through the Databricks Files API. It runs wherever egress works: inside Databricks if the probe succeeds, otherwise from a scheduled GitHub Actions workflow or the ADF pipeline.
- **Load** (Databricks job) reads the landing volume into Bronze. It never calls the internet.
The first ingest task runs a one-line egress probe from serverless and records the result in `docs/data-sources.md`. The design works either way, so a blocked domain changes where fetch runs, not the code.

| Source | Access | Bronze table |
|---|---|---|
| HRRP by hospital (`9n3s-kdb3`) | CMS datastore API, paginated | `bronze_hrrp` |
| Hospital General Information (`xubh-q36u`) | CMS datastore API | `bronze_hospital_info` |
| Unplanned Hospital Visits (`632h-zaca`) | CMS datastore API | `bronze_unplanned_visits` |
| Historical HRRP, FY2019 to FY2026 (long format, with readmission counts) | Provider Data Catalog archive API, one fall snapshot zip per year | `bronze_hrrp_history_pdc` |
| Historical HRRP, FY2013 to FY2018 and FY2027 (wide format, ERR and discharges only) | CMS.gov IPPS Final Rule supplemental data zips | `bronze_hrrp_history_ipps` |
| Census ACS 5-year county socioeconomics | Census API, key in Key Vault | `bronze_acs_county` |
| MTSamples clinical notes | Existing Delta table in the workspace volume | `bronze_clinical_notes` |
| Contoso operational DB: facility roster mapped to CCNs, monthly discharges and follow-up-call logs (synthetic, seeded by a script) | Lakebase Postgres, JDBC, incremental on `updated_at` watermark, credentials in a secret scope | `bronze_contoso_*` |

Verified 2026-10-21: the three CMS datastore endpoints respond; HRRP has 18,330 rows for FY2026 (period 07/2021 to 06/2024).

### Historical HRRP: how to fetch it (verified 2026-10-21)

**PDC archive, FY2019 to FY2026.** Index: `https://data.cms.gov/provider-data/api/1/archive/aggregate/theme/hospitals/relative` (JSON list of `url`, `date`, `type`). Zips live at `https://data.cms.gov/provider-data/sites/default/files/dataset-archives/theme/hospitals/hospitals_<YYYY-MM-DD>.zip`. Pick the October/November snapshot per fiscal year from the index on each run, never hardcode dates. The HRRP file name inside varies by year; match it by pattern (`*Readmissions_Reduction_Program_Hospital*.csv`, `9n3s-kdb3*.csv`, or `*RRP_HOSPITAL.csv` for FY2019) and strip nested folder prefixes.

| FY | Snapshot | Performance period |
|---|---|---|
| 2019 | 2019-10-31 | 07/2014 to 06/2017 |
| 2020 | 2020-10-16 | 07/2015 to 06/2018 |
| 2021 | 2021-10-27 | 07/2016 to 06/2019 |
| 2022 | 2022-10-06 | 07/2017 to 12/2019 (COVID exclusion) |
| 2023 | 2023-10-06 | 07/2018 to 06/2021 |
| 2024 | 2024-10-30 | 07/2019 to 12/2019 plus 07/2020 to 06/2022 (file says 07/2019 to 06/2022) |
| 2025 | 2025-11-26 | 07/2020 to 06/2023 |
| 2026 | 2026-08-13 | 07/2021 to 06/2024 |

**CMS.gov IPPS supplemental files, FY2013 to FY2018 and FY2027.** Index: `https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/archived-supplemental-data-files`. Exact zip URLs per year are recorded in `docs/data-sources.md` (created by the historical-ingest task). Tab-separated, title rows before the header, one row per hospital with wide `<condition> cases` and `<condition> ERR` columns. No readmission counts or predicted/expected rates. FY2013 to FY2014 cover AMI, HF and pneumonia only; COPD and hip/knee arrive in FY2015, CABG in FY2017. FY2027 is not on PDC yet.

**Normalization rules.** Left-pad CCN to 6 digits. Accept `Provider ID` and `Facility ID`. Normalize measure names (`READM_30_AMI_HRRP` and `READM-30-AMI-HRRP` are the same). Strip thousands separators. Treat `N/A`, `Not Available`, `Too Few to Report`, `.` and blank as null. Do not use the CMS.gov FY2023 and FY2026 links: they serve the wrong year's file.

## Commands

```
Setup:        uv sync
Lint:         uv run ruff check . && uv run ruff format --check .
Unit tests:   uv run pytest -q
Validate:     databricks bundle validate -t dev
Deploy:       databricks bundle deploy -t dev
Run pipeline: databricks bundle run readmission_pipeline -t dev
Infra:        az deployment group create -g rg-readmission -f infra/main.bicep
Teardown:     az group delete -n rg-readmission --yes
```

## Project Structure

```
SPEC.md, README.md
databricks.yml            Asset Bundle: jobs, dashboard, targets
resources/                Job and dashboard definitions
src/readmission/
  ingest/                 API clients (CMS, Census), pagination, retries
  bronze/                 Raw landing, append-only, ingest metadata columns
  silver/                 Typed schemas, dedup, N/A handling, quarantine
  gold/                   Facility-condition facts, penalty marts, model features
  quality/                Expectations and constraint definitions
  ml/                     Training, evaluation, registration
  llm/                    Notes extraction and eval
analysis/                 EDA and causal notebooks (Databricks source format)
tests/                    pytest, local Spark fixtures, small CSV fixtures
infra/                    Bicep for Key Vault and ADF
powerbi/                  .pbip project
engagement/               discovery.md, requirements.md, adr/, readout (slides)
docs/                     architecture.md, analysis-plan.md, ai-assisted-dev.md, data-dictionary.md
.github/workflows/        CI
```

## Code Style

Pure transformation functions that take and return DataFrames, so they unit test locally. I/O stays at the job entry point.

```python
SILVER_HRRP_SCHEMA = T.StructType([
    T.StructField("facility_id", T.StringType(), nullable=False),
    T.StructField("measure_name", T.StringType(), nullable=False),
    T.StructField("excess_readmission_ratio", T.DoubleType()),
    T.StructField("number_of_discharges", T.IntegerType()),
    T.StructField("period_start", T.DateType()),
    T.StructField("period_end", T.DateType()),
])

def clean_hrrp(raw: DataFrame) -> DataFrame:
    """CMS reports suppressed counts as 'N/A' or 'Too Few to Report'; both become null."""
    return (
        raw.withColumn("number_of_discharges", suppressed_to_null("number_of_discharges").cast("int"))
        .withColumn("excess_readmission_ratio", F.col("excess_readmission_ratio").cast("double"))
        .withColumn("period_start", F.to_date("start_date", "MM/dd/yyyy"))
        .withColumn("period_end", F.to_date("end_date", "MM/dd/yyyy"))
        .select(*SILVER_HRRP_SCHEMA.fieldNames())
    )
```

- snake_case, type hints, ruff defaults, line length 100.
- Table names `<layer>_<entity>`. Every Bronze row carries `_ingested_at`, `_source`, `_batch_id`.
- Comments explain domain rules (CMS suppression codes, measure definitions), not the code.

## Testing Strategy

- **Unit (pytest, local Spark):** every Silver and Gold transform, against small fixture files cut from real API responses. Suppression codes, date parsing, dedup, schema mismatch routing to quarantine.
- **Contract:** the API client against recorded responses, including pagination and a 429/5xx retry.
- **Data quality (in pipeline):** Delta `CHECK` constraints on Silver (ratio > 0, valid facility_id format), row-count and null-rate expectations that fail the job above thresholds, quarantine table for rejected rows.
- **Model:** a held-out metric gate (the job fails if AUC drops below a stated baseline) and a leakage test (no feature built from the target cycle).
- **LLM extraction:** precision and recall against 50 hand-labeled notes, logged to MLflow.
- **CI:** lint, unit tests, `bundle validate` on every PR.

## Boundaries

- **Always:** keep secrets in Key Vault or Databricks secrets, never in code or memory; cite data sources and their vintage; state model and causal limitations in the readout; record AI-assisted decisions in the log.
- **Ask first:** creating Azure resources (anything that could bill), making the GitHub repo public, adding a paid API, changing the causal design after seeing outcome data.
- **Never:** commit tokens or `.env`; use real patient data (MTSamples is public, synthetic-style transcription samples); use the employer's name or branding; present the fictional client as real.

## Success Criteria

1. `databricks bundle run readmission_pipeline -t dev` runs Bronze to Gold end to end from a clean schema, on a schedule, in under 15 minutes.
2. Silver tables enforce schemas and constraints; a deliberately malformed fixture lands in quarantine and does not fail the run.
3. MLflow shows at least 2 compared runs and 1 registered model with metrics, parameters, and a feature importance artifact.
4. The causal notebook states its hypotheses before results, reports an RDD estimate with confidence interval and bandwidth sensitivity, plus a placebo test.
5. The dashboard and Genie space answer 5 documented client questions; the `.pbip` opens and reads the Gold tables.
6. The Key Vault-backed Census key is used by a run, and ADF triggers the Databricks job at least once (screenshot evidence in `docs/`).
7. CI is green; `pytest` passes locally.
8. README opens with the requirement coverage map and a 5-line summary of findings.
9. Engagement docs, AI-assisted dev log, case study page, and video exist and link from the README.
10. The Contoso Lakebase source loads incrementally: a second run after inserting rows picks up only the new rows.
11. A failed job task sends a notification and shows up on the pipeline-health page.
12. The pilot design states sample size, power, minimum detectable effect and the analysis it commits to.

## Plan

**Day 1: data platform**
- 1.1 Repo scaffold: uv, ruff, pytest, bundle skeleton, CI. (Chris rotates the old Databricks token by hand.)
- 1.2 CMS API client with pagination and retry, plus recorded fixtures.
- 1.3 Bronze ingestion for the 3 CMS datasets and MTSamples, using the fetch/load split and egress probe from Data Sources.
- 1.4 Historical HRRP ingest from both sources (see Data Sources), same fetch/load split, unified in `silver_hrrp_history` with one row per facility, measure and fiscal year, FY2013 to FY2027.
- 1.5 Silver: schemas, cleaning, constraints, quarantine, tests; Unity Catalog comments, tags and grants.
- 1.5b Contoso Lakebase: seed script for synthetic facility, discharge and follow-up data (Contoso's facilities are a fixed set of real CCNs relabeled, stated in the README), JDBC incremental load to Bronze and Silver.
- 1.6 Gold: facility-condition fact, penalty mart, feature table, anomaly flags. Job with schedule, retries, failure notifications, `ops_run_log` and `ops_dq_metrics`.

**Day 2: analysis and ML**
- 2.1 EDA notebook, analysis plan with hypotheses and tests (with multiple-comparison correction) written before outcomes are examined.
- 2.2 MLflow penalty model: baseline logistic regression vs gradient boosting, time-based split, registration with a champion alias, metric gate, scheduled batch scoring into `gold_penalty_predictions`.
- 2.3 Causal: RDD at the penalty cutoff on cycle t, outcome is the change in ERR at cycle t+3, bandwidth sensitivity, placebo cutoffs, DiD check. Two design facts the analysis plan must handle:
  - Performance windows are 3 years and overlap by 2 years between consecutive fiscal years, so t vs t+1 is mostly the same claims. Compare non-overlapping windows (t vs t+3), or say why not.
  - The cutoff is ERR = 1.0 for FY2013 to FY2018. From FY2019 CMS compares each hospital to the median ERR of its dual-eligibility peer group, so the cutoff becomes that median. Peer group assignments come from the supplemental files; confirm per year which ones carry them.
- 2.4 LLM notes extraction plus 50-note eval, outputs validated against a JSON schema.
- 2.6 Pilot design: a randomized (or stepped-wedge) trial of post-discharge follow-up calls across Contoso facilities, with power calculation, primary metric, guardrails and analysis plan.
- 2.5 Census ACS ingestion via Key Vault (needs Azure, 3.1).

**Day 3: delivery surface**
- 3.1 Bicep for Key Vault and ADF, deploy, run once (fetch to landing if needed, then trigger the Databricks job), capture evidence, tear down.
- 3.2 Wireframe first (in `engagement/`), then AI/BI dashboard with a pipeline-health page and Genie space in the bundle; `.pbip`.
- 3.3 Engagement docs: discovery, user stories, ADRs, status update, readout deck, one-page executive summary, PHI note.
- 3.4 AI-assisted dev log, gitleaks in CI, README with coverage map, architecture diagram.
- 3.5 Case study page on christeceno.com; video script and recording.

Parallel lanes for the factory: 1.2 to 1.6 sequential; 1.4 and 1.5b need only 1.1; 3.1 and 3.3 independent of the pipeline; 2.4 depends only on 1.3; 2.6 depends only on 1.5b.

## Open Questions

1. ~~Historical HRRP cycles.~~ Resolved 2026-10-21: FY2013 to FY2027 are all scriptable (see Data Sources). Remaining gap: FY2013 to FY2018 have no readmission counts, so the causal outcome is ERR, which every year carries.
2. **Free Edition limits.** Docs confirm: serverless only, outbound internet limited to trusted domains, 5 concurrent job tasks, one 2X-Small SQL warehouse, one Lakebase project, capped model serving endpoints. Still to confirm in the workspace: Genie, AI/BI dashboards, Unity Catalog model registry, which Foundation Model endpoints are available (decides 2.4), and egress to CMS/Census (decides where fetch runs).
3. ~~Key Vault and Free Edition.~~ Decided 2026-10-21: the Census key lives in Key Vault, read via `azure-identity` with a service principal whose credentials sit in a Databricks secret scope. If egress to Azure is blocked from serverless, ADF reads Key Vault natively and the fetch runs there.
4. ~~Video tooling.~~ Decided 2026-10-21: Claude generates the walkthrough (scripted screen captures plus narration), no live recording by Chris.
