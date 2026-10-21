# Spec: readmission-lakehouse

Status: draft for review (2026-10-21)

## Objective

A public portfolio project that demonstrates every requirement in a Senior Forward Deployed Engineer (Data & AI, Databricks) job description, framed as a delivery engagement for a fictional client.

**The fictional client:** "Contoso Health" (Microsoft's stock fictional company, so nobody mistakes it for a real hospital), a regional hospital network. Their question:

> "CMS penalizes us under the Hospital Readmissions Reduction Program. Which of our facilities and conditions drive the penalty, what would actually reduce it, and does the penalty itself change hospital behavior?"

**What gets built:**

1. A Databricks medallion lakehouse (Bronze, Silver, Gold) ingesting CMS Provider Data APIs, Census ACS, and the existing MTSamples clinical notes table.
2. An MLflow-tracked model predicting which hospitals land above the penalty threshold (excess readmission ratio > 1.0).
3. A causal analysis of whether being penalized changes a hospital's next-cycle readmission ratio (regression discontinuity at ERR = 1.0, with a difference-in-differences check).
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
| Pipelines from APIs, databases, other sources | CMS datastore API (paginated JSON), Census ACS API, MTSamples Delta table, CMS CSV files |
| MLflow experiments | `ml/train_penalty_model.py`, experiment `/Shared/readmission-penalty`, registered model |
| Data governance, schema enforcement, testing, quality | Explicit Silver schemas, Delta constraints, expectation checks, quarantine table, pytest suite |
| Orchestration (Databricks Workflows, ADF) | Databricks Asset Bundle job (`databricks.yml`); one ADF pipeline that triggers it |
| Azure Data Factory, Key Vault | `infra/` Bicep: Key Vault holding the Census key and Databricks token, ADF pipeline |
| Power BI | `powerbi/` `.pbip` project over Gold tables |
| Dashboards, Genie | AI/BI dashboard JSON in the bundle, Genie space with documented sample questions |
| Statistics: experimental design, hypothesis testing, causal inference | `analysis/` RDD + DiD notebook, pre-registered hypotheses in `docs/analysis-plan.md` |
| Exploration, anomalies, storytelling | EDA notebook, anomaly flags in Gold, the readout deck |
| AI-assisted development and judgment | `docs/ai-assisted-dev.md` log: what AI produced, how it was checked, what was rejected |
| LLMs in solutions | Notes extraction step with eval metrics |
| Git workflows | PR-per-task history, CI on GitHub Actions |
| Client communication | `engagement/` discovery notes, requirements, ADRs, readout |
| Healthcare bonus | The domain |

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

| Source | Access | Bronze table |
|---|---|---|
| HRRP by hospital (`9n3s-kdb3`) | CMS datastore API, paginated | `bronze_hrrp` |
| Hospital General Information (`xubh-q36u`) | CMS datastore API | `bronze_hospital_info` |
| Unplanned Hospital Visits (`632h-zaca`) | CMS datastore API | `bronze_unplanned_visits` |
| Historical HRRP cycles (FY2019 to FY2025) | CMS archived data zips | `bronze_hrrp_history` |
| Census ACS 5-year county socioeconomics | Census API, key in Key Vault | `bronze_acs_county` |
| MTSamples clinical notes | Existing Delta table in the workspace volume | `bronze_clinical_notes` |

Verified 2026-10-21: the three CMS datastore endpoints respond; HRRP has 18,330 rows for FY2026 (period 07/2021 to 06/2024).

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

## Plan (2 to 3 days)

**Day 1: data platform**
- 1.1 Repo scaffold: uv, ruff, pytest, bundle skeleton, CI. Rotate the old Databricks token; set up a secret scope.
- 1.2 CMS API client with pagination and retry, plus recorded fixtures.
- 1.3 Bronze ingestion for the 3 CMS datasets and MTSamples.
- 1.4 Resolve historical HRRP access (open question 1); land `bronze_hrrp_history`.
- 1.5 Silver: schemas, cleaning, constraints, quarantine, tests.
- 1.6 Gold: facility-condition fact, penalty mart, feature table. Workflow job scheduling Bronze to Gold.

**Day 2: analysis and ML**
- 2.1 EDA notebook, analysis plan with hypotheses written before outcomes are examined.
- 2.2 MLflow penalty model: baseline logistic regression vs gradient boosting, registration, metric gate.
- 2.3 Causal: RDD at ERR = 1.0 on cycle t vs ratio change in t+1, bandwidth sensitivity, placebo cutoffs, DiD check.
- 2.4 LLM notes extraction plus 50-note eval.
- 2.5 Census ACS ingestion via Key Vault (needs Azure, 3.1).

**Day 3: delivery surface**
- 3.1 Bicep for Key Vault and ADF, deploy, trigger once, capture evidence, tear down.
- 3.2 AI/BI dashboard and Genie space in the bundle; `.pbip`.
- 3.3 Engagement docs, ADRs, readout deck.
- 3.4 AI-assisted dev log, README with coverage map, architecture diagram.
- 3.5 Case study page on christeceno.com; video script and recording.

Parallel lanes for the factory: 1.2 to 1.6 sequential; 3.1 and 3.3 independent of the pipeline; 2.4 depends only on 1.3.

## Open Questions

1. **Historical HRRP cycles.** The CMS archive page renders with JavaScript and did not yield scriptable zip links on first try. If archives cannot be fetched, the causal design falls back to a cross-sectional analysis (inverse probability weighting on hospital characteristics), which is weaker. Resolve in task 1.4.
2. **Free Edition limits.** Confirm Free Edition supports Genie, AI/BI dashboards, model registry in Unity Catalog, and a Foundation Model endpoint. Any gap changes 2.4 or 3.2.
3. **Key Vault and Free Edition.** Key Vault-backed secret scopes require Azure Databricks. Plan: the ingest job reads the Census key from Key Vault via `azure-identity` with a service principal whose credentials live in a Databricks secret scope. Acceptable, or should the Azure piece run in an Azure Databricks trial instead?
4. **Video tooling.** Screen recording plus voiceover by you, or a scripted walkthrough generated locally?
