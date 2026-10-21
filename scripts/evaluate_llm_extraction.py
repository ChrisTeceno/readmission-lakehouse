"""Compare Foundation Model endpoints on the 50 labelled notes and log one MLflow run per model.

    uv run --group ml python scripts/evaluate_llm_extraction.py [--profile readmission]

Each note is one `ai_query` statement on the SQL warehouse, built by the same
`readmission.llm.extract.ai_query_sql` the `llm_extract` task uses, so the prompt, the schema
and the checks are the job's. A first answer that fails validation is asked once more with the
reason, as in the job. Latency is the statement's wall time seen from here, warehouse overhead
included. With `--without-response-format` every note is also asked once without the JSON schema,
which measures how often each model keeps to the schema on its own.

    uv run --group ml python scripts/evaluate_llm_extraction.py --local [--local-model ID]

adds one more candidate, a model served on this Mac by omlx (OpenAI-compatible chat completions
on 127.0.0.1:8000), with the same prompt, schema, checks and retry. Only the note read goes to the
SQL warehouse; inference stays on the Mac. The API key is read from `OMLX_API_KEY` or
`~/.omlx/settings.json` and is never printed or logged. Latency is the request's wall time, and
tokens/s is completion tokens over that time. The local run is tagged `backend=local`. It is not
part of the Databricks job, because serverless cannot reach this Mac.

MLflow runs go to `EXPERIMENT`, tagged `labels_status=draft` while any label in
`tests/fixtures/llm_labels.jsonl` still needs review: metrics against draft labels are provisional.

    uv run --group ml python scripts/evaluate_llm_extraction.py --rescore-run RUN_ID [RUN_ID ...]

scores the predictions an earlier run logged against the current labels, without calling a model,
and logs a new run tagged with the source run id. Latency and validity come from the saved
predictions, so only the label-dependent metrics can differ from the source run.
"""

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from databricks.sdk import WorkspaceClient

from readmission.llm.build import model_name
from readmission.llm.extract import (
    MAX_TOKENS,
    PROMPT,
    PROMPT_SHA256,
    PROMPT_VERSION,
    ai_query_sql,
    response_format,
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
LOCAL_URL = "http://127.0.0.1:8000/v1"
LOCAL_MODEL = "gemma-4-26b-it-8bit"
LOCAL_MODEL_NAME = "gemma-4-26b-a4b-it-8bit"
OMLX_SETTINGS = Path.home() / ".omlx" / "settings.json"


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


def databricks_ask(w, warehouse_id, endpoint):
    """The `ask` of a Foundation Model endpoint: one note through ai_query."""

    def ask(note_id, note, *, structured=True, suffix=""):
        """Returns (reply, endpoint error, seconds, completion tokens or None)."""
        expr = ai_query_sql(
            endpoint, "transcription", structured=structured, suffix_column=_quote(suffix)
        )
        started = time.monotonic()
        rows = statement(
            w, warehouse_id, f"SELECT to_json({expr}) FROM {NOTES_TABLE} WHERE note_id = {note_id}"
        )
        response = json.loads(rows[0][0])
        return (
            response.get("result"),
            response.get("errorMessage"),
            time.monotonic() - started,
            None,
        )

    return ask


def omlx_api_key() -> str:
    key = os.environ.get("OMLX_API_KEY")
    return key or json.loads(OMLX_SETTINGS.read_text())["auth"]["api_key"]


def local_ask(client: httpx.Client, model: str):
    """The `ask` of a model served by omlx: the job's prompt and schema as chat completions."""

    def ask(note_id, note, *, structured=True, suffix=""):
        body = {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT + note + suffix}],
            "temperature": 0.0,
            "max_tokens": MAX_TOKENS,
        }
        if structured:
            body["response_format"] = json.loads(response_format())
        started = time.monotonic()
        try:
            r = client.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return None, f"{type(e).__name__}: {e}", time.monotonic() - started, None
        seconds = time.monotonic() - started
        message = data["choices"][0]["message"]
        return message.get("content"), None, seconds, data.get("usage", {}).get("completion_tokens")

    return ask


def evaluate_note(ask, label, note, without_format):
    raw, error, seconds, tokens = ask(label["note_id"], note)
    first = validate_answer(raw, note, error)
    result = {
        "note_id": label["note_id"],
        "latency_s": seconds,
        "schema_valid": first.schema_valid,
        "first_valid": first.valid,
        "first_reason": first.reason,
        "first_raw": raw,
    }
    if tokens is not None:
        result["completion_tokens"] = tokens
    final = first
    if not first.valid:
        raw, error, _, _ = ask(label["note_id"], note, suffix=retry_suffix(first.reason))
        final = validate_answer(raw, note, error)
        result |= {"retry_reason": final.reason, "retry_raw": raw}
    result |= {"valid": final.valid, "answer": final.answer}
    if without_format:
        raw, error, _, _ = ask(label["note_id"], note, structured=False)
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
    if all("completion_tokens" in r for r in results):
        metrics["tokens_per_s_mean"] = statistics.mean(
            r["completion_tokens"] / r["latency_s"] for r in results
        )
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


def is_local(endpoint: str) -> bool:
    return endpoint == LOCAL_MODEL


def log_run(
    endpoint: str,
    metrics: dict,
    results: list[dict],
    labels: list[dict],
    out: Path,
    source_run_id: str | None = None,
):
    import mlflow

    pending = sum(g["needs_review"] for g in labels)
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=endpoint) as run:
        mlflow.log_params(
            {
                "model_endpoint": endpoint,
                "model_name": LOCAL_MODEL_NAME if is_local(endpoint) else model_name(endpoint),
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": PROMPT_SHA256,
                "response_format": "json_schema",
                "temperature": 0.0,
                "max_tokens": MAX_TOKENS,
                "notes": len(labels),
            }
        )
        tags = {
            "labels_status": "draft" if pending else "reviewed",
            "labels_needing_review": f"{pending} of {len(labels)}",
            "phi": "none: MTSamples public transcription samples",
            "backend": "local" if is_local(endpoint) else "databricks",
        }
        if is_local(endpoint):
            tags["model"] = LOCAL_MODEL_NAME
        if pending:
            tags["metrics_provisional"] = "true"
        if source_run_id:
            tags["rescored_from_run"] = source_run_id
        mlflow.set_tags(tags)
        mlflow.log_metrics(metrics)
        path = out / f"{endpoint}.json"
        path.write_text(json.dumps(results, indent=1))
        mlflow.log_artifact(str(path), "predictions")
        return run.info.run_id


def rescore_run(run_id: str, labels: list[dict]) -> tuple[str, dict, list[dict]]:
    """Endpoint, metrics on `labels` and saved predictions of an earlier evaluation run."""
    from mlflow.artifacts import download_artifacts
    from mlflow.tracking import MlflowClient

    endpoint = MlflowClient().get_run(run_id).data.params["model_endpoint"]
    path = download_artifacts(run_id=run_id, artifact_path=f"predictions/{endpoint}.json")
    results = json.loads(Path(path).read_text())
    return endpoint, rescore(results, labels), results


def rescore(results: list[dict], labels: list[dict]) -> dict:
    """Metrics for saved predictions against `labels`, by the same code as a live run."""
    return summarize(results, labels, "schema_valid_without_format" in results[0])


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


def rescore_runs(args: argparse.Namespace) -> None:
    import mlflow

    mlflow.set_tracking_uri(f"databricks://{args.profile}")
    labels = [json.loads(line) for line in LABELS.read_text().splitlines()]
    args.out.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for source in args.rescore_run:
        endpoint, metrics, results = rescore_run(source, labels)
        summaries[endpoint] = metrics
        run_id = log_run(endpoint, metrics, results, labels, args.out, source_run_id=source)
        print(f"{endpoint}: MLflow run {run_id}, rescored from {source}")
        print(f"{endpoint}: {json.dumps({k: round(v, 3) for k, v in metrics.items()})}")
    (args.out / "summary.json").write_text(json.dumps(summaries, indent=1))
    print(table(summaries))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate LLM risk factor extraction.")
    parser.add_argument("--profile", default="readmission")
    parser.add_argument("--warehouse-id", help="defaults to the workspace's first warehouse")
    parser.add_argument("--models", nargs="*", default=MODELS)
    parser.add_argument(
        "--local",
        action="store_true",
        help="also run the model served by omlx on this Mac (--local-model)",
    )
    parser.add_argument("--local-model", default=LOCAL_MODEL)
    parser.add_argument("--local-url", default=LOCAL_URL)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--without-response-format", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("dist/llm-eval"))
    parser.add_argument("--no-mlflow", action="store_true")
    parser.add_argument(
        "--rescore-run",
        nargs="+",
        metavar="RUN_ID",
        help="score these runs' logged predictions on the current labels; no model is called",
    )
    parser.add_argument("--limit", type=int, help="first N labelled notes only, for a smoke test")
    args = parser.parse_args(argv)

    if args.rescore_run:
        rescore_runs(args)
        return

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

    asks = {m: databricks_ask(w, warehouse_id, m) for m in args.models}
    if args.local:
        client = httpx.Client(
            base_url=args.local_url,
            headers={"Authorization": f"Bearer {omlx_api_key()}"},
            timeout=600,
        )
        asks[args.local_model] = local_ask(client, args.local_model)

    summaries = {}
    for endpoint, ask in asks.items():
        with ThreadPoolExecutor(args.concurrency) as pool:
            results = list(
                pool.map(
                    lambda g, ask=ask: evaluate_note(
                        ask, g, notes[g["note_id"]], args.without_response_format
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
