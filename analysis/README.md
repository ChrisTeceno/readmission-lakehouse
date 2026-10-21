Exploratory and causal analysis notebooks (RDD and difference-in-differences), in Databricks source format.

- `01_eda.py`: EDA over the Gold tables, testing the descriptive hypotheses pre-registered in
  `docs/analysis-plan.md`. HTML export with outputs: `docs/eda/01_eda.html`; readout summary:
  `docs/eda/what-the-data-can-answer.md`.

The notebooks are not part of the bundle or the pipeline job. To run one on serverless, import it
and submit a one-off run:

```
databricks workspace import /Users/<you>/readmission/analysis/01_eda --file analysis/01_eda.py --language PYTHON --format SOURCE --overwrite
databricks jobs submit --json '{"run_name": "01_eda", "tasks": [{"task_key": "eda", "notebook_task": {"notebook_path": "/Users/<you>/readmission/analysis/01_eda", "source": "WORKSPACE"}}]}'
databricks jobs export-run <task run id> --views-to-export CODE
```

The export's `views[0].content` is the HTML page.
