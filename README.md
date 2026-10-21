# readmission-lakehouse

A portfolio project framed as a delivery engagement for the fictional client Contoso Health, a regional hospital network. The client asks which facilities and conditions drive their CMS Hospital Readmissions Reduction Program (HRRP) penalty, what would reduce it, and whether the penalty itself changes hospital behavior. The answer is built as a Databricks medallion lakehouse (Bronze, Silver, Gold) with an MLflow penalty model, a causal analysis and an LLM notes extraction step.

## Requirement coverage

| JD requirement | Where it is shown |
|---|---|
| Databricks, Spark/PySpark, Delta | `src/` pipeline code, Delta tables in Unity Catalog `workspace.readmission` |
| Medallion architecture | `bronze/`, `silver/`, `gold/` modules and `docs/architecture.md` diagram |
| Pipelines from APIs, databases, other sources | CMS datastore API (paginated JSON), Census ACS API, Contoso's Lakebase Postgres (JDBC, incremental on an `updated_at` watermark), MTSamples Delta table, CMS CSV files |
| MLflow experiments | `ml/train.py`, experiment `/Shared/readmission-penalty`, Unity Catalog model `penalty_model` with a `champion` alias, `docs/model-card.md` |
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

## Contoso's data

Contoso Health is fictional. Its operational database (a Lakebase Postgres, `docs/lakebase.md`) is synthetic, seeded by `scripts/seed_contoso.py`. Its 12 facilities are real public CMS Certification Numbers from Ohio relabeled with invented names, so Contoso's numbers line up with the public HRRP data; no real hospital's data is altered, and no Contoso number describes a real hospital.

## Local development

```
Setup:        uv sync
Lint:         uv run ruff check . && uv run ruff format --check .
Unit tests:   uv run pytest -q
```

Unit tests run a local Spark session with Delta Lake and need Java 17.
