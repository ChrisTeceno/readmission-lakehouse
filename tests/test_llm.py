import importlib.util
import json
import random
from pathlib import Path

import httpx
import pytest
from pyspark.sql import functions as F

from readmission.llm import build as llm_build
from readmission.llm.extract import (
    COMORBIDITY_TERMS,
    PROMPT,
    SCHEMA,
    ai_query_sql,
    normalize_term,
    retry_suffix,
    validate_answer,
)
from readmission.llm.metrics import FieldScore, field_counts, flatten, mean_f1, score
from readmission.llm.sample import SAMPLE_SIZE, allocate, stratified_sample
from readmission.ops import RunRecorder

LABELS = Path(__file__).parent / "fixtures" / "llm_labels.jsonl"

NOTE = (
    "DISCHARGE DIAGNOSES:,1. Congestive heart failure.,2. Hypertension.,SOCIAL HISTORY: She lives "
    "by herself.  She was admitted last year for pneumonia.,DISPOSITION: , To home.,FOLLOWUP: , "
    "She will follow up with Dr. X in 2 weeks."
)


def answer(**overrides) -> dict:
    base = {
        "primary_condition": {"value": "HF", "evidence": "Congestive heart failure."},
        "comorbidities": [
            {"term": "heart_failure", "evidence": "Congestive heart failure."},
            {"term": "hypertension", "evidence": "Hypertension."},
        ],
        "prior_admissions_mentioned": {
            "value": True,
            "evidence": "She was admitted last year for pneumonia.",
        },
        "discharge_disposition": {"value": "home", "evidence": "To home."},
        "social_risk_factors": [{"factor": "lives_alone", "evidence": "She lives by herself."}],
        "follow_up_planned": {"value": True, "evidence": "follow up with Dr. X in 2 weeks"},
    }
    return base | overrides


def label(**overrides) -> dict:
    base = {
        "primary_condition": "other",
        "comorbidities": [],
        "prior_admissions_mentioned": False,
        "discharge_disposition": "not_stated",
        "social_risk_factors": [],
        "follow_up_planned": False,
    }
    return base | overrides


# Sample


def test_allocate_is_proportional_and_sums_to_n():
    quotas = allocate({"Surgery": 976, "Radiology": 248, "Letters": 7, "Dentistry": 1}, 50)
    assert sum(quotas.values()) == 50
    assert quotas == {"Surgery": 40, "Radiology": 10}


def test_stratified_sample_ignores_input_order():
    notes = [(i, "Surgery" if i % 3 else "Radiology") for i in range(300)]
    shuffled = notes[:]
    random.Random(1).shuffle(shuffled)
    sample = stratified_sample(notes, n=30)
    assert sample == stratified_sample(shuffled, n=30)
    assert len(sample) == 30
    assert sum(s == "Radiology" for _, s in sample) == 10


def test_label_fixture_is_the_draft_sample():
    rows = [json.loads(line) for line in LABELS.read_text().splitlines()]
    assert len(rows) == SAMPLE_SIZE
    assert len({r["note_id"] for r in rows}) == SAMPLE_SIZE
    # The lead checked every label against the written rules; Chris decided the judgment calls.
    assert not any(r["needs_review"] for r in rows)
    enums = SCHEMA["properties"]
    for r in rows:
        g = r["labels"]
        assert g["primary_condition"] in enums["primary_condition"]["properties"]["value"]["enum"]
        assert set(g["comorbidities"]) <= set(COMORBIDITY_TERMS)
        assert (
            g["discharge_disposition"]
            in (enums["discharge_disposition"]["properties"]["value"]["enum"])
        )
        assert set(g["social_risk_factors"]) <= {
            "lives_alone",
            "transportation",
            "medication_access",
        }


# Validation


def test_valid_answer_passes():
    checked = validate_answer(json.dumps(answer()), NOTE)
    assert checked.valid and checked.schema_valid
    assert checked.answer["primary_condition"]["value"] == "HF"


def test_code_fence_is_stripped():
    assert validate_answer("```json\n" + json.dumps(answer()) + "\n```", NOTE).valid


def test_evidence_must_be_quoted_from_the_note():
    bad = answer(discharge_disposition={"value": "home", "evidence": "Discharged home."})
    checked = validate_answer(json.dumps(bad), NOTE)
    assert not checked.valid and checked.schema_valid
    assert checked.reason.startswith("discharge_disposition: evidence is not a quote")


def test_evidence_match_ignores_whitespace_runs_and_case():
    # The note has two spaces after "herself."; the quote has one and starts lowercase.
    quote = "she lives by herself. She was admitted last year"
    ok = answer(prior_admissions_mentioned={"value": True, "evidence": quote})
    assert validate_answer(json.dumps(ok), NOTE).valid


def test_positive_value_needs_evidence():
    bad = answer(follow_up_planned={"value": True, "evidence": None})
    assert validate_answer(json.dumps(bad), NOTE).reason == "follow_up_planned: missing evidence"


def test_negative_value_may_omit_evidence():
    ok = answer(follow_up_planned={"value": False, "evidence": None}, social_risk_factors=[])
    assert validate_answer(json.dumps(ok), NOTE).valid


def test_long_evidence_is_rejected():
    long = answer(primary_condition={"value": "HF", "evidence": NOTE + NOTE})
    assert "longer than" in validate_answer(json.dumps(long), NOTE).reason


def test_synonyms_are_normalized_before_the_schema_check():
    synonyms = answer(
        comorbidities=[
            {"term": "CHF", "evidence": "Congestive heart failure."},
            {"term": "Congestive heart failure", "evidence": "Congestive heart failure."},
            {"term": "HTN", "evidence": "Hypertension."},
        ]
    )
    checked = validate_answer(json.dumps(synonyms), NOTE)
    assert checked.valid
    assert [c["term"] for c in checked.answer["comorbidities"]] == ["heart_failure", "hypertension"]


def test_term_outside_the_vocabulary_breaks_the_schema():
    bad = answer(comorbidities=[{"term": "migraine", "evidence": "Hypertension."}])
    checked = validate_answer(json.dumps(bad), NOTE)
    assert not checked.schema_valid
    assert checked.reason.startswith("schema: comorbidities/0/term")


def test_missing_key_breaks_the_schema():
    bad = answer()
    del bad["follow_up_planned"]
    assert validate_answer(json.dumps(bad), NOTE).reason.startswith("schema: answer:")


@pytest.mark.parametrize(
    "raw, error, reason",
    [
        (None, "rate limit", "endpoint error: rate limit"),
        ("", None, "empty response"),
        ('{"primary_condition": ', None, "not JSON"),
        ("[]", None, "not a JSON object"),
    ],
)
def test_unusable_replies(raw, error, reason):
    checked = validate_answer(raw, NOTE, error)
    assert not checked.valid and not checked.schema_valid
    assert checked.reason.startswith(reason)


def test_normalize_term():
    assert normalize_term(" End-Stage Renal Disease ") == "chronic_kidney_disease"
    assert normalize_term("sleep apnea") == "sleep_apnea"


def test_ai_query_sql_quotes_the_prompt_and_adds_the_schema_when_structured():
    sql = ai_query_sql("databricks-x", "transcription", structured=True, suffix_column="suffix")
    assert sql.startswith("ai_query('databricks-x', concat('You extract")
    assert ", transcription, suffix)" in sql
    assert "failOnError => false" in sql
    assert 'responseFormat => \'{"type": "json_schema"' in sql
    assert "responseFormat" not in ai_query_sql("databricks-x", "t", structured=False)
    assert "Return as needed" in PROMPT and "Return as needed" in sql


# Metrics


def test_scalar_fields_count_a_wrong_positive_both_ways():
    counts = field_counts(flatten(answer()), label(primary_condition="AMI"))
    assert counts["primary_condition"] == (0, 1, 1)
    assert counts["discharge_disposition"] == (0, 1, 0)
    counts = field_counts(flatten(answer()), label(discharge_disposition="home"))
    assert counts["discharge_disposition"] == (1, 0, 0)


def test_list_fields_match_normalized_terms():
    g = label(comorbidities=["Hypertension", "diabetes"], social_risk_factors=["lives_alone"])
    counts = field_counts(flatten(answer()), g)
    assert counts["comorbidities"] == (1, 1, 1)
    assert counts["social_risk_factors"] == (1, 0, 0)


def test_no_answer_costs_recall_only():
    counts = field_counts(
        flatten(None), label(comorbidities=["hypertension"], follow_up_planned=True)
    )
    assert counts["comorbidities"] == (0, 0, 1)
    assert counts["follow_up_planned"] == (0, 0, 1)
    assert counts["primary_condition"] == (0, 0, 0)


def test_score_and_mean_f1():
    pairs = [(flatten(answer()), label(primary_condition="HF")), (flatten(None), label())]
    scores = score(pairs)
    assert scores["primary_condition"] == FieldScore(1, 0, 0)
    assert scores["primary_condition"].f1 == 1.0
    assert scores["comorbidities"].precision == 0.0
    assert scores["comorbidities"].recall is None
    # Six fields: primary_condition 1.0, every other field 0 (no true positive or no positives).
    assert mean_f1(scores) == pytest.approx(1 / 6)


# The llm_extract task


@pytest.fixture
def llm_db(spark, monkeypatch):
    # Unity Catalog tags and grants do not exist on local Spark.
    monkeypatch.setattr(llm_build, "apply_governance", lambda *a: None)
    spark.sql("CREATE DATABASE IF NOT EXISTS llm_test")
    spark.createDataFrame(
        [(i, "General Medicine", NOTE) for i in (1, 2, 3, 4)],
        "note_id int, medical_specialty string, transcription string",
    ).write.format("delta").saveAsTable("spark_catalog.llm_test.silver_clinical_notes")
    yield "spark_catalog.llm_test"
    spark.sql("DROP DATABASE IF EXISTS llm_test CASCADE")


def fake_model(replies: dict[tuple[int, bool], str | None]):
    """A stand-in for ai_query: the reply for (note_id, is a retry)."""

    def call(notes):
        rows = [
            (
                r["note_id"],
                r["transcription"],
                r["suffix"],
                replies[(r["note_id"], r["suffix"] != "")],
            )
            for r in notes.collect()
        ]
        df = notes.sparkSession.createDataFrame(
            rows, "note_id int, transcription string, suffix string, result string"
        )
        return df.withColumn(
            "response",
            F.struct(F.col("result"), F.lit(None).cast("string").alias("errorMessage")),
        )

    return call


def test_build_llm_retries_once_then_rejects(spark, llm_db):
    good = json.dumps(answer())
    unquoted = json.dumps(answer(discharge_disposition={"value": "home", "evidence": "Went home."}))
    replies = {(1, False): good, (2, False): "not json", (2, True): good}
    replies |= {(3, False): unquoted, (3, True): unquoted}
    run = RunRecorder("llm_extract")

    counts = llm_build.build_llm(
        spark, llm_db, run, batch_size=3, job_run_id="7", call=fake_model(replies)
    )

    assert counts == {"notes": 3, "first_attempt_valid": 1, "retried": 2, "rejected": 1}
    gold = {r["note_id"]: r for r in spark.table(f"{llm_db}.gold_note_risk_factors").collect()}
    assert sorted(gold) == [1, 2]
    assert (gold[1]["attempts"], gold[2]["attempts"]) == (1, 2)
    assert gold[1]["primary_condition"] == "HF"
    assert [c["term"] for c in gold[1]["comorbidities"]] == ["heart_failure", "hypertension"]
    assert gold[1]["model_endpoint"] == llm_build.MODEL_ENDPOINT
    assert gold[1]["job_run_id"] == "7"
    (reject,) = spark.table(f"{llm_db}.silver_llm_rejects").collect()
    assert (reject["note_id"], reject["attempts"]) == (3, 2)
    assert reject["reason"].startswith("discharge_disposition: evidence is not a quote")
    assert reject["raw_response"] == unquoted

    # The next run takes only note 4: 1 and 2 are extracted, 3 was rejected under this prompt.
    counts = llm_build.build_llm(
        spark, llm_db, run, batch_size=3, call=fake_model({(4, False): good})
    )
    assert counts["notes"] == 1
    assert spark.table(f"{llm_db}.gold_note_risk_factors").count() == 3


def test_retry_suffix_names_the_reason():
    assert "rejected: not JSON" in retry_suffix("not JSON")


# Rescoring a logged evaluation run


def load_evaluate_script():
    path = Path(__file__).parent.parent / "scripts" / "evaluate_llm_extraction.py"
    spec = importlib.util.spec_from_file_location("evaluate_llm_extraction", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def saved_result(note_id: int, reply: dict | None) -> dict:
    return {
        "note_id": note_id,
        "latency_s": 2.0,
        "schema_valid": reply is not None,
        "first_valid": reply is not None,
        "first_reason": None,
        "first_raw": None,
        "valid": reply is not None,
        "answer": reply,
    }


def test_rescore_run_scores_saved_predictions_on_the_current_labels(tmp_path):
    import mlflow

    evaluate = load_evaluate_script()
    mlflow.set_tracking_uri(f"file://{tmp_path / 'mlruns'}")
    results = [saved_result(1, answer()), saved_result(2, None)]
    saved = tmp_path / "databricks-x.json"
    saved.write_text(json.dumps(results))
    with mlflow.start_run() as source:
        mlflow.log_param("model_endpoint", "databricks-x")
        mlflow.log_artifact(str(saved), "predictions")

    labels = [
        {"note_id": 1, "needs_review": False, "labels": label(comorbidities=["hypertension"])},
        {"note_id": 2, "needs_review": False, "labels": label(follow_up_planned=True)},
    ]
    endpoint, metrics, loaded = evaluate.rescore_run(source.info.run_id, labels)
    assert endpoint == "databricks-x"
    assert loaded == results
    assert metrics == evaluate.summarize(results, labels, False)
    assert metrics["comorbidities_tp"] == 1 and metrics["comorbidities_fp"] == 1
    assert metrics["follow_up_planned_fn"] == 1
    assert metrics["reject_rate"] == 0.5

    # A relabelled note changes the label-dependent metrics and nothing else.
    labels[0]["labels"]["comorbidities"] = ["hypertension", "heart_failure"]
    relabelled = evaluate.rescore(loaded, labels)
    assert relabelled["comorbidities_fp"] == 0
    assert relabelled["reject_rate"] == metrics["reject_rate"]
    assert relabelled["latency_p50_s"] == metrics["latency_p50_s"]

    run_id = evaluate.log_run(
        endpoint, relabelled, loaded, labels, tmp_path, source_run_id=source.info.run_id
    )
    tags = mlflow.get_run(run_id).data.tags
    assert tags["labels_status"] == "reviewed"
    assert tags["rescored_from_run"] == source.info.run_id
    assert "metrics_provisional" not in tags


# The local (omlx) backend, with the HTTP client stubbed


def stub_client(handler) -> httpx.Client:
    return httpx.Client(base_url="http://omlx.test/v1", transport=httpx.MockTransport(handler))


def completion(content: str, tokens: int = 50) -> httpx.Response:
    body = {"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": tokens}}
    return httpx.Response(200, json=body)


def test_local_ask_sends_the_jobs_prompt_and_schema():
    evaluate = load_evaluate_script()
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return completion("{}", tokens=40)

    ask = evaluate.local_ask(stub_client(handler), "gemma-test")
    reply, error, seconds, tokens = ask(1, NOTE, suffix=" RETRY")
    ask(1, NOTE, structured=False)

    structured, plain = seen
    assert (reply, error, tokens) == ("{}", None, 40) and seconds >= 0
    assert structured["model"] == "gemma-test" and structured["temperature"] == 0.0
    assert structured["messages"] == [{"role": "user", "content": PROMPT + NOTE + " RETRY"}]
    assert structured["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "response_format" not in plain


def test_local_ask_reports_http_errors_as_endpoint_errors():
    evaluate = load_evaluate_script()
    ask = evaluate.local_ask(stub_client(lambda request: httpx.Response(500)), "gemma-test")
    reply, error, _, tokens = ask(1, NOTE)
    assert reply is None and tokens is None and "HTTPStatusError" in error


def test_evaluate_note_retries_a_local_answer_and_summarize_adds_tokens_per_second():
    evaluate = load_evaluate_script()
    unquoted = json.dumps(answer(discharge_disposition={"value": "home", "evidence": "Went home."}))
    replies = iter([unquoted, json.dumps(answer())])
    ask = evaluate.local_ask(stub_client(lambda request: completion(next(replies), 100)), "g")

    result = evaluate.evaluate_note(ask, {"note_id": 1}, NOTE, without_format=False)
    assert (result["first_valid"], result["valid"]) == (False, True)
    assert result["first_reason"].startswith("discharge_disposition: evidence is not a quote")
    assert result["completion_tokens"] == 100

    labels = [{"note_id": 1, "needs_review": False, "labels": label()}]
    result["latency_s"] = 4.0
    metrics = evaluate.summarize([result], labels, False)
    assert metrics["tokens_per_s_mean"] == 25.0
    assert metrics["retry_rate"] == 1.0 and metrics["reject_rate"] == 0.0


def test_log_run_tags_a_local_model(tmp_path):
    import mlflow

    evaluate = load_evaluate_script()
    mlflow.set_tracking_uri(f"file://{tmp_path / 'mlruns'}")
    labels = [{"note_id": 1, "needs_review": False, "labels": label()}]
    results = [saved_result(1, answer())]
    run_id = evaluate.log_run(
        evaluate.LOCAL_MODEL, evaluate.summarize(results, labels, False), results, labels, tmp_path
    )
    run = mlflow.get_run(run_id)
    assert run.data.tags["backend"] == "local"
    assert run.data.tags["model"] == "gemma-4-26b-a4b-it-8bit"
    assert run.data.tags["labels_status"] == "reviewed"
    assert run.data.params["model_name"] == "gemma-4-26b-a4b-it-8bit"
