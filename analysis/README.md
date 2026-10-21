Exploratory and causal analysis notebooks (RDD and difference-in-differences), in Databricks source format.

- `01_eda.py`: EDA over the Gold tables, testing the descriptive hypotheses pre-registered in
  `docs/analysis-plan.md`. HTML export with outputs: `docs/eda/01_eda.html`; readout summary:
  `docs/eda/what-the-data-can-answer.md`.
- `02_causal.py`: the pre-registered causal analysis of `docs/analysis-plan.md` (RDD at the HRRP
  penalty cutoff, validity checks, sensitivity, DiD check), using `rdrobust` and `rddensity`,
  which the notebook installs. HTML export with outputs: `docs/causal/02_causal.html`; findings:
  `docs/causal-findings.md`.
- `03_power.py`: simulation-based power for the follow-up call pilot in `docs/pilot-design.md`
  (fixed seed). It also runs locally, `uv run python analysis/03_power.py`, from the baseline
  extract in `analysis/data/` (Ohio HRRP rows for FY2023 and FY2026, and Contoso's latest 12
  months of synthetic discharges and calls); on Databricks it reads the tables and checks them
  against that extract. The simulator is `readmission.analysis.power`. It needs `src/` next to
  `analysis/` in the workspace: `databricks workspace import-dir src /Users/<you>/readmission/src`
  and the same for `analysis/data`.

The notebooks are not part of the bundle or the pipeline job. To run one on serverless, import it
and submit a one-off run:

```
databricks workspace import /Users/<you>/readmission/analysis/01_eda --file analysis/01_eda.py --language PYTHON --format SOURCE --overwrite
databricks jobs submit --json '{"run_name": "01_eda", "tasks": [{"task_key": "eda", "notebook_task": {"notebook_path": "/Users/<you>/readmission/analysis/01_eda", "source": "WORKSPACE"}}]}'
databricks jobs export-run <task run id> --views-to-export CODE
```

The export's `views[0].content` is the HTML page.
