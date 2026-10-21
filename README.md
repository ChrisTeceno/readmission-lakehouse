# readmission-lakehouse

A portfolio project framed as a delivery engagement for the fictional client Contoso Health, a regional hospital network. Contoso asks which facilities and conditions drive its CMS Hospital Readmissions Reduction Program (HRRP) penalty, what would reduce it, and whether the penalty itself changes hospital behavior. The answer is a Databricks medallion lakehouse (Bronze, Silver, Gold) with an MLflow penalty model, a causal analysis, an LLM notes extraction step, a dashboard, a Genie space and a Power BI project. [`SPEC.md`](SPEC.md) is the source of truth; [`docs/architecture.md`](docs/architecture.md) has the diagram.

## Requirement coverage

| JD requirement | Where it is shown |
|---|---|
| Databricks, Spark/PySpark, Delta | [`src/readmission/`](src/readmission/) pipeline code, Delta tables in Unity Catalog `workspace.readmission` |
| Medallion architecture | [`bronze/`](src/readmission/bronze/), [`silver/`](src/readmission/silver/), [`gold/`](src/readmission/gold/) modules and the [architecture diagram](docs/architecture.md) |
| Pipelines from APIs, databases, other sources | CMS datastore API and archive zips, Census ACS API, Contoso's Lakebase Postgres (JDBC, incremental on an `updated_at` watermark), MTSamples Delta table; see [`docs/data-sources.md`](docs/data-sources.md) and [`docs/lakebase.md`](docs/lakebase.md) |
| MLflow experiments | [`ml/`](src/readmission/ml/), experiment `/Shared/readmission-penalty`, Unity Catalog model `penalty_model` with a `champion` alias, [`docs/model-card.md`](docs/model-card.md) |
| Data governance, schema enforcement, testing, quality | Explicit Silver schemas, Delta constraints, expectation checks, quarantine table, [`tests/`](tests/) pytest suite; comments, tags and grants in [`docs/governance.md`](docs/governance.md) |
| Orchestration and monitoring (Databricks Workflows, ADF) | Asset Bundle job with schedule, retries and failure email ([`resources/readmission_pipeline.yml`](resources/readmission_pipeline.yml)); `ops_run_log` and `ops_dq_metrics` feed the dashboard's pipeline-health page. The ADF pipeline is written but not deployed yet |
| Azure Data Factory, Key Vault | [`infra/`](infra/README.md) Bicep for Key Vault and an ADF pipeline: pending, not on `main` yet |
| Power BI | [`powerbi/`](powerbi/README.md) `.pbip` project over Gold tables |
| Dashboards, Genie | AI/BI dashboard ([`src/dashboards/`](src/dashboards/)) and Genie space ([`src/genie/`](src/genie/)) as bundle resources, built from [`engagement/wireframe.md`](engagement/wireframe.md); each client question tested in [`docs/genie.md`](docs/genie.md) |
| Statistics: experimental design, hypothesis testing, causal inference | [`docs/analysis-plan.md`](docs/analysis-plan.md) (hypotheses before results), [`analysis/`](analysis/) EDA and RDD + DiD notebooks, [`docs/causal-findings.md`](docs/causal-findings.md) |
| Exploration, anomalies, storytelling | [`analysis/01_eda.py`](analysis/01_eda.py), anomaly flags in Gold ([`docs/gold.md`](docs/gold.md)), [`docs/eda/what-the-data-can-answer.md`](docs/eda/what-the-data-can-answer.md) |
| AI-assisted development and judgment | [`docs/ai-assisted-dev.md`](docs/ai-assisted-dev.md): cases tied to PRs of AI output that was accepted, corrected or rejected; gitleaks in CI ([`ci.yml`](.github/workflows/ci.yml)); LLM output schema-validated and evaluated |
| LLMs in solutions | [`llm/`](src/readmission/llm/) notes extraction through `ai_query`, schema- and quote-validated, three models compared on 50 labelled notes in MLflow ([`docs/llm-extraction.md`](docs/llm-extraction.md), [ADR 0002](engagement/adr/0002-llm-risk-factor-extraction.md)) |
| Git workflows | PR-per-task history, CI on GitHub Actions ([`ci.yml`](.github/workflows/ci.yml)) |
| Client communication | [`engagement/`](engagement/README.md): [discovery notes](engagement/discovery.md), [user stories](engagement/user-stories.md) with acceptance criteria, [dashboard wireframe](engagement/wireframe.md), [ADR index](engagement/adr/README.md), [status update](engagement/status-update.md), [PHI note](engagement/phi-note.md); the readout is still to come |
| Healthcare bonus | The domain; no PHI is used |

## Contoso's data

Contoso Health is fictional. Its operational database (a Lakebase Postgres, [`docs/lakebase.md`](docs/lakebase.md)) is synthetic, seeded by [`scripts/seed_contoso.py`](scripts/seed_contoso.py). Its 12 facilities are real public CMS Certification Numbers from Ohio relabeled with invented names, so Contoso's numbers line up with the public HRRP data; no real hospital's data is altered, and no Contoso number describes a real hospital.

## How to run

Needs [`uv`](https://docs.astral.sh/uv/), Java 17 for the local Spark tests, and the Databricks CLI logged in to a workspace for the bundle commands.

```
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
databricks bundle deploy -t dev
databricks bundle run readmission_pipeline -t dev
```

The job runs `fetch`, `bronze` and `contoso`, `silver`, `gold`, then `train` and `score` and `llm_extract`. A clean run took about 13 minutes (see [`docs/data-sources.md`](docs/data-sources.md)).

## Findings

