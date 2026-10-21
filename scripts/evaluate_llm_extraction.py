"""Compare Foundation Model endpoints on the 50 labelled notes and log one MLflow run per model.

    uv run --group ml python scripts/evaluate_llm_extraction.py [--profile readmission]

Each note is one `ai_query` statement on the SQL warehouse, built by the same
`readmission.llm.extract.ai_query_sql` the `llm_extract` task uses, so the prompt, the schema
and the checks are the job's. A first answer that fails validation is asked once more with the
reason, as in the job. Latency is the statement's wall time seen from here, warehouse overhead
included. With `--without-response-format` every note is also asked once without the JSON schema,
which measures how often each model keeps to the schema on its own.

MLflow runs go to `EXPERIMENT`, tagged `labels_status=draft` while any label in
`tests/fixtures/llm_labels.jsonl` still needs review: metrics against draft labels are provisional.
"""

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from databricks.sdk import WorkspaceClient

from readmission.llm.build import model_name
from readmission.llm.extract import (
    MAX_TOKENS,
    PROMPT_SHA256,
    PROMPT_VERSION,
    ai_query_sql,
    retry_suffix,
    validate_answer,
)
from readmission.llm.metrics import FIELDS, flatten, mean_f1, score
from readmission.silver.governance import _quote

EXPERIMENT = "/Users/chris.teceno@gmail.com/readmission-llm-extraction"
MODELS = [
    "databricks-gpt-oss-120b",
    "databricks-meta-llama-3-3-70b-instruct",
    "databricks-meta-llama-3-1-8b-instruct",
]
LABELS = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "llm_labels.jsonl"
NOTES_TABLE = "workspace.readmission.silver_clinical_notes"


def statement(w: WorkspaceClient, warehouse_id: str, sql: str) -> list[list]:
    r = w.statement_execution.execute_statement(
        statement=sql, warehouse_id=warehouse_id, wait_timeout="50s"
    )
    while r.status.state.value in ("PENDING", "RUNNING"):
        time.sleep(2)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state.value != "SUCCEEDED":
        raise RuntimeError(f"statement {r.status.state.value}: {r.status.error}")
    return r.result.data_array or []


def ask(w, warehouse_id, endpoint, note_id, *, structured=True, suffix=""):
    """One note through ai_query; returns (reply, endpoint error, seconds)."""
    expr = ai_query_sql(
        endpoint, "transcription", structured=structured, suffix_column=_quote(suffix)
    )
    started = time.monotonic()
    rows = statement(
        w, warehouse_id, f"SELECT to_json({expr}) FROM {NOTES_TABLE} WHERE note_id = {note_id}"
    )
    response = json.loads(rows[0][0])
    return response.get("result"), response.get("errorMessage"), time.monotonic() - started


def evaluate_note(w, warehouse_id, endpoint, label, note, without_format):
    raw, error, seconds = ask(w, warehouse_id, endpoint, label["note_id"])
    first = validate_answer(raw, note, error)
    result = {
        "note_id": label["note_id"],
        "latency_s": seconds,
        "schema_valid": first.schema_valid,
        "first_valid": first.valid,
        "first_reason": first.reason,
        "first_raw": raw,
    }
    final = first
    if not first.valid:
        raw, error, _ = ask(
            w, warehouse_id, endpoint, label["note_id"], suffix=retry_suffix(first.reason)
        )
        final = validate_answer(raw, note, error)
        result |= {"retry_reason": final.reason, "retry_raw": raw}
    result |= {"valid": final.valid, "answer": final.answer}
    if without_format:
        raw, error, _ = ask(w, warehouse_id, endpoint, label["note_id"], structured=False)
        unformatted = validate_answer(raw, note, error)
        result |= {"schema_valid_without_format": unformatted.schema_valid}
    return result


def summarize(results: list[dict], labels: list[dict], without_format: bool) -> dict:
    n = len(results)
    by_id = {r["note_id"]: r for r in results}
    scores = score([(flatten(by_id[g["note_id"]]["answer"]), g["labels"]) for g in labels])
    latencies = sorted(r["latency_s"] for r in results)
    metrics = {
        "schema_valid_rate": sum(r["schema_valid"] for r in results) / n,
        "first_attempt_valid_rate": sum(r["first_valid"] for r in results) / n,
        "retry_rate": sum(not r["first_valid"] for r in results) / n,
        "final_valid_rate": sum(r["valid"] for r in results) / n,
        "reject_rate": sum(not r["valid"] for r in results) / n,
        "latency_mean_s": statistics.mean(latencies),
        "latency_p50_s": statistics.median(latencies),
        "latency_p90_s": latencies[int(0.9 * (n - 1))],
        "mean_f1": mean_f1(scores),
    }
    if without_format:
        metrics["schema_valid_rate_without_response_format"] = (
            sum(r["schema_valid_without_format"] for r in results) / n
        )
    for name, s in scores.items():
        metrics |= {f"{name}_tp": s.tp, f"{name}_fp": s.fp, f"{name}_fn": s.fn}
        for kind in ("precision", "recall", "f1"):
            if getattr(s, kind) is not None:
                metrics[f"{name}_{kind}"] = getattr(s, kind)
    return metrics


def log_run(endpoint: str, metrics: dict, results: list[dict], labels: list[dict], out: Path):
    import mlflow

    confirmed = sum(not g["needs_review"] for g in labels)
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=endpoint) as run:
        mlflow.log_params(
            {
                "model_endpoint": endpoint,
                "model_name": model_name(endpoint),
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": PROMPT_SHA256,
                "response_format": "json_schema",
                "temperature": 0.0,
                "max_tokens": MAX_TOKENS,
                "notes": len(labels),
            }
        )
        mlflow.set_tags(
            {
                "labels_status": "draft" if confirmed < len(labels) else "confirmed",
                "labels_confirmed": f"{confirmed} of {len(labels)}",
                "metrics_provisional": str(confirmed < len(labels)).lower(),
                "phi": "none: MTSamples public transcription samples",
            }
        )
        mlflow.log_metrics(metrics)
        path = out / f"{endpoint}.json"
        path.write_text(json.dumps(results, indent=1))
        mlflow.log_artifact(str(path), "predictions")
        return run.info.run_id


def table(summaries: dict[str, dict]) -> str:
    def cell(m: dict, key: str) -> str:
        return f"{m[key]:.2f}" if key in m else "n/a"

    lines = ["| Metric | " + " | ".join(summaries) + " |", "|---|" + "---|" * len(summaries)]
    for key in ["mean_f1", "schema_valid_rate", "first_attempt_valid_rate", "retry_rate"]:
        lines.append(f"| {key} | " + " | ".join(cell(m, key) for m in summaries.values()) + " |")
    for field in FIELDS:
        for kind in ("precision", "recall"):
            key = f"{field}_{kind}"
            lines.append(
                f"| {key} | " + " | ".join(cell(m, key) for m in summaries.values()) + " |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate LLM risk factor extraction.")
    parser.add_argument("--profile", default="readmission")
    parser.add_argument("--warehouse-id", help="defaults to the workspace's first warehouse")
    parser.add_argument("--models", nargs="+", default=MODELS)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--without-response-format", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("dist/llm-eval"))
    parser.add_argument("--no-mlflow", action="store_true")
    parser.add_argument("--limit", type=int, help="first N labelled notes only, for a smoke test")
    args = parser.parse_args(argv)

    w = WorkspaceClient(profile=args.profile)
    warehouse_id = args.warehouse_id or next(iter(w.warehouses.list())).id
    labels = [json.loads(line) for line in LABELS.read_text().splitlines()][: args.limit]
    ids = ", ".join(str(g["note_id"]) for g in labels)
    query = f"SELECT note_id, transcription FROM {NOTES_TABLE} WHERE note_id IN ({ids})"
    notes = {int(i): text for i, text in statement(w, warehouse_id, query)}
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.no_mlflow:
        import mlflow

        mlflow.set_tracking_uri(f"databricks://{args.profile}")

    summaries = {}
    for endpoint in args.models:
        with ThreadPoolExecutor(args.concurrency) as pool:
            results = list(
                pool.map(
                    lambda g, endpoint=endpoint: evaluate_note(
                        w,
                        warehouse_id,
                        endpoint,
                        g,
                        notes[g["note_id"]],
                        args.without_response_format,
                    ),
                    labels,
                )
            )
        summaries[endpoint] = summarize(results, labels, args.without_response_format)
        (args.out / f"{endpoint}.json").write_text(json.dumps(results, indent=1))
        if not args.no_mlflow:
            run_id = log_run(endpoint, summaries[endpoint], results, labels, args.out)
            print(f"{endpoint}: MLflow run {run_id}")
        print(f"{endpoint}: {json.dumps({k: round(v, 3) for k, v in summaries[endpoint].items()})}")
    (args.out / "summary.json").write_text(json.dumps(summaries, indent=1))
    print(table(summaries))


if __name__ == "__main__":
    main()
