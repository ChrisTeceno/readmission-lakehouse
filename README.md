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
| Orchestration and monitoring (Databricks Workflows, ADF) | Asset Bundle job with schedule, retries and failure email ([`resources/readmission_pipeline.yml`](resources/readmission_pipeline.yml)); `ops_run_log` and `ops_dq_metrics` feed the dashboard's pipeline-health page. An ADF pipeline triggers the same job ([evidence](docs/evidence/azure.md)) |
| Azure Data Factory, Key Vault | [`infra/`](infra/README.md) Bicep for Key Vault and an ADF pipeline, deployed and run twice, with the Census key read from Key Vault ([`docs/azure.md`](docs/azure.md), [evidence](docs/evidence/azure.md)); `infra/teardown.sh` removes the resources |
| Power BI | [`powerbi/`](powerbi/README.md) `.pbip` project over Gold tables |
| Dashboards, Genie | AI/BI dashboard ([`src/dashboards/`](src/dashboards/)) and Genie space ([`src/genie/`](src/genie/)) as bundle resources, built from [`engagement/wireframe.md`](engagement/wireframe.md); each client question tested in [`docs/genie.md`](docs/genie.md); [screenshots of the published dashboard and a Genie answer](docs/evidence/dashboard.md) |
| Statistics: experimental design, hypothesis testing, causal inference | [`docs/analysis-plan.md`](docs/analysis-plan.md) (hypotheses before results), [`analysis/`](analysis/) EDA and RDD + DiD notebooks, [`docs/causal-findings.md`](docs/causal-findings.md), follow-up call pilot with simulation-based power in [`docs/pilot-design.md`](docs/pilot-design.md) and [`analysis/03_power.py`](analysis/03_power.py) |
| Exploration, anomalies, storytelling | [`analysis/01_eda.py`](analysis/01_eda.py), anomaly flags in Gold ([`docs/gold.md`](docs/gold.md)), [`docs/eda/what-the-data-can-answer.md`](docs/eda/what-the-data-can-answer.md) |
| AI-assisted development and judgment | [`docs/ai-assisted-dev.md`](docs/ai-assisted-dev.md): cases tied to PRs of AI output that was accepted, corrected or rejected; gitleaks in CI ([`ci.yml`](.github/workflows/ci.yml)); LLM output schema-validated and evaluated |
| LLMs in solutions | [`llm/`](src/readmission/llm/) notes extraction through `ai_query`, schema- and quote-validated, four models (one local) compared on 50 labelled notes in MLflow ([`docs/llm-extraction.md`](docs/llm-extraction.md), [ADR 0002](engagement/adr/0002-llm-risk-factor-extraction.md)) |
| Git workflows | PR-per-task history, CI on GitHub Actions ([`ci.yml`](.github/workflows/ci.yml)) |
| Client communication | [`engagement/`](engagement/README.md): [discovery notes](engagement/discovery.md), [user stories](engagement/user-stories.md) with acceptance criteria, [dashboard wireframe](engagement/wireframe.md), [ADR index](engagement/adr/README.md), [status update](engagement/status-update.md), [PHI note](engagement/phi-note.md), [readout deck](engagement/readout.md), [executive summary](engagement/executive-summary.md) |
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

The full readout is [`engagement/readout.md`](engagement/readout.md) ([PDF](engagement/readout.pdf)), with every number's source on its last slide; the one-page version is [`engagement/executive-summary.md`](engagement/executive-summary.md).

1. **The penalty is concentrated.** In FY2027, 10 of Contoso's 12 facilities and 25 of its 67 reporting facility-condition pairs are above the cutoff; in FY2026 hip/knee was above 1.0 at 8 of the 9 facilities that report it ([EDA](docs/eda/what-the-data-can-answer.md), [Genie tests](docs/genie.md)).
2. **The penalty model ranks well on approximate-cutoff years and less well on the exact one:** LightGBM test AUC 0.874 on FY2026 against 0.678 on FY2027, a small lead over logistic regression, and overconfident on FY2027 ([model card](docs/model-card.md)).
3. **Being penalized does not change a hospital's later readmission ratio:** RDD estimate +0.0004 (95% interval -0.0027 to +0.0047, detectable effect 0.0053), a precise null, with a retention caveat and a DiD check of opposite sign reported ([causal findings](docs/causal-findings.md)).
4. **Notes extraction:** gpt-oss-120b reaches a mean field F1 of 0.81 on 50 labelled notes against 0.70 and 0.54 for two Llama models, and a local Gemma 4 26B A4B on the Mac reaches 0.85, so keeping note text inside the client's network costs no accuracy here; the lead checked all 50 labels against the written rules and Chris Teceno, a clinician, reviewed all 50 and decided the four judgment calls ([ADR 0002](engagement/adr/0002-llm-risk-factor-extraction.md)).
5. **Pilot:** a stepped-wedge rollout of follow-up calls across 12 facilities over 21 months (about 17,885 synthetic discharges) detects a 3.82-point drop in readmissions at 80 percent power ([readout](engagement/readout.md), [design](docs/pilot-design.md)).
6. **Penalty dollars (estimates):** about $1.96M in FY2026 and $1.76M in FY2027 across Contoso's 12 facilities, from CMS's published payment adjustment factors and base payments estimated from the IPPS impact file; a 72-hour nurse-call program at about $371K a year pays for itself on Contoso's ledger at a drop of about half a point (0.36 to 0.71), while pooled trials of calls alone find no effect, so the pilot cannot confirm break-even ([penalty dollars](docs/penalty-dollars.md)).

## Limits

- Contoso is fictional: its CMS numbers are real public data for relabeled Ohio hospitals, and its operational data (volumes, follow-up calls) are synthetic.
- FY2019 to FY2026 penalty cutoffs use 1.0 in place of the peer group median ([ADR 0006](engagement/adr/0006-approximate-cutoff-stand-in.md)); FY2013 to FY2018 and FY2027 are exact.
- Penalty dollars are estimates: CMS publishes the payment adjustment factor, and the base payments it applies to are modelled from CMS's IPPS impact file, not Contoso's paid claims. Nurse-call program costs rest on stated time, setup and management assumptions, and the 1-point central effect is a judgment ([penalty dollars](docs/penalty-dollars.md)).
- An HRRP fiscal year names the year the penalty is paid: FY2027 is charged on July 2023 to June 2025 discharges and is already fixed, so a program started in 2027 first moves the penalty around FY2029 to FY2031.
