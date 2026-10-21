"""LLM extraction: readmission risk factors from the next batch of notes.

    readmission-llm-extract [--catalog workspace] [--schema readmission] [--batch-size 100]

The `llm_extract` task of the `readmission_pipeline` job, after `silver`. Each run takes up to
`--batch-size` notes from `silver_clinical_notes` that are not yet in `gold_note_risk_factors`
(lowest note ids first) and asks `MODEL_ENDPOINT` for their risk factors through `ai_query`, with
the JSON schema as `responseFormat`. An answer that fails `extract.validate_answer` is asked once
more with the reason appended to the prompt; a second failure goes to `silver_llm_rejects` with
the reason and the raw reply, and the note is not asked again under the same model and prompt
version. Both tables are appended to, so a run never rewrites earlier extractions.
"""

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.llm.extract import (
    PROMPT_SHA256,
    PROMPT_VERSION,
    Checked,
    ai_query_sql,
    retry_suffix,
    validate_answer,
)
from readmission.ops import RunRecorder, add_ops_arguments, track_run
from readmission.ops.tables import create_statement
from readmission.silver.build import apply_governance
from readmission.silver.governance import TableDoc, field, gold_tags, silver_tags

# Chosen by the three-model comparison in engagement/adr/0002-llm-risk-factor-extraction.md.
MODEL_ENDPOINT = "databricks-gpt-oss-120b"

# Notes per run. Free Edition caps Foundation Model API throughput, and a run of 100 notes takes
# a few minutes; the 2,357 notes are covered after 24 daily runs.
BATCH_SIZE = 100

NOTES_TABLE = "silver_clinical_notes"
TABLE = "gold_note_risk_factors"
REJECTS_TABLE = "silver_llm_rejects"

_EVIDENCE = "Quote from the note supporting the value; null when the value is negative"

GOLD_NOTE_RISK_FACTORS_SCHEMA = T.StructType(
    [
        field("note_id", T.IntegerType(), "MTSamples note id", nullable=False),
        field("medical_specialty", T.StringType(), "Specialty the note is filed under"),
        field(
            "primary_condition",
            T.StringType(),
            "HRRP condition that is the main reason for the encounter (AMI, HF, PN, COPD, CABG, "
            "HIP_KNEE) or other",
            nullable=False,
        ),
        field("primary_condition_evidence", T.StringType(), _EVIDENCE),
        field(
            "comorbidities",
            T.ArrayType(
                T.StructType(
                    [
                        T.StructField("term", T.StringType()),
                        T.StructField("evidence", T.StringType()),
                    ]
                )
            ),
            "Chronic conditions documented as present, from the schema's vocabulary, each with "
            "its quote",
            nullable=False,
        ),
        field(
            "prior_admissions_mentioned",
            T.BooleanType(),
            "The note mentions a hospital admission before this encounter",
            nullable=False,
        ),
        field("prior_admissions_evidence", T.StringType(), _EVIDENCE),
        field(
            "discharge_disposition",
            T.StringType(),
            "Where the patient went at the end of the encounter, or not_stated",
            nullable=False,
        ),
        field("discharge_disposition_evidence", T.StringType(), _EVIDENCE),
        field(
            "social_risk_factors",
            T.ArrayType(
                T.StructType(
                    [
                        T.StructField("factor", T.StringType()),
                        T.StructField("evidence", T.StringType()),
                    ]
                )
            ),
            "lives_alone, transportation or medication_access when documented, each with its quote",
            nullable=False,
        ),
        field(
            "follow_up_planned",
            T.BooleanType(),
            "The note plans a follow-up visit or contact with a clinician",
            nullable=False,
        ),
        field("follow_up_evidence", T.StringType(), _EVIDENCE),
        field("model_endpoint", T.StringType(), "Serving endpoint ai_query called", nullable=False),
        field(
            "model_name",
            T.StringType(),
            "Foundation model behind the endpoint; pay-per-token endpoints expose no version",
        ),
        field("prompt_version", T.StringType(), "Prompt version in readmission.llm.extract"),
        field("prompt_sha256", T.StringType(), "SHA-256 of the prompt text", nullable=False),
        field("attempts", T.IntegerType(), "1, or 2 when the first answer was rejected"),
        field("extracted_at", T.TimestampType(), "When the run ended (UTC)", nullable=False),
        field("job_run_id", T.StringType(), "Databricks job run id; empty outside the job"),
    ]
)

GOLD_NOTE_RISK_FACTORS_DOC = TableDoc(
    comment=(
        "Readmission risk factors an LLM extracted from MTSamples notes, one row per note, each "
        "value with a quote from the note. Every row passed schema and quote validation. Public "
        "de-identified samples, no PHI. Source: silver_clinical_notes."
    ),
    tags=gold_tags("mtsamples"),
)

SILVER_LLM_REJECTS_SCHEMA = T.StructType(
    [
        field("note_id", T.IntegerType(), "MTSamples note id", nullable=False),
        field("model_endpoint", T.StringType(), "Serving endpoint ai_query called", nullable=False),
        field("model_name", T.StringType(), "Foundation model behind the endpoint"),
        field("prompt_version", T.StringType(), "Prompt version in readmission.llm.extract"),
        field("prompt_sha256", T.StringType(), "SHA-256 of the prompt text", nullable=False),
        field("attempts", T.IntegerType(), "Answers requested before the note was rejected"),
        field("reason", T.StringType(), "Why the last answer failed validation", nullable=False),
        field("raw_response", T.StringType(), "The last answer as returned, or null"),
        field("rejected_at", T.TimestampType(), "When the run ended (UTC)", nullable=False),
        field("job_run_id", T.StringType(), "Databricks job run id; empty outside the job"),
    ]
)

SILVER_LLM_REJECTS_DOC = TableDoc(
    comment=(
        "Notes whose LLM answer failed validation twice (not JSON, off schema, or evidence not "
        "quoted from the note), with the reason and the raw answer. Appended by llm_extract."
    ),
    tags=silver_tags("mtsamples"),
)

# (note_id, transcription, suffix) rows in, the same rows plus `response` (ai_query's
# struct<result, errorMessage>) out. The job calls ai_query; tests pass a stand-in.
ModelCall = Callable[[DataFrame], DataFrame]


def ai_query_call(endpoint: str) -> ModelCall:
    def call(notes: DataFrame) -> DataFrame:
        expr = ai_query_sql(endpoint, "transcription", structured=True, suffix_column="suffix")
        return notes.withColumn("response", F.expr(expr))

    return call


def pending_notes(notes: DataFrame, gold: DataFrame, rejects: DataFrame, batch_size: int):
    """Notes with no extraction yet and no rejection under the current endpoint and prompt."""
    rejected = rejects.where(
        (F.col("model_endpoint") == MODEL_ENDPOINT) & (F.col("prompt_sha256") == PROMPT_SHA256)
    )
    return (
        notes.join(gold.select("note_id"), "note_id", "left_anti")
        .join(rejected.select("note_id"), "note_id", "left_anti")
        .orderBy("note_id")
        .limit(batch_size)
        .select("note_id", "medical_specialty", "transcription")
    )


def ask(
    spark: SparkSession, call: ModelCall, rows: list[tuple[int, str, str]]
) -> dict[int, tuple[str | None, str | None]]:
    """Ask the model about (note_id, transcription, suffix) rows; returns note_id to (reply,
    endpoint error)."""
    if not rows:
        return {}
    schema = "note_id int, transcription string, suffix string"
    answered = call(spark.createDataFrame(rows, schema)).select(
        "note_id",
        F.col("response.result").alias("result"),
        F.col("response.errorMessage").alias("error"),
    )
    return {r["note_id"]: (r["result"], r["error"]) for r in answered.collect()}


def gold_row(note: dict, answer: dict, attempts: int, meta: dict) -> tuple:
    pc, pa = answer["primary_condition"], answer["prior_admissions_mentioned"]
    dd, fu = answer["discharge_disposition"], answer["follow_up_planned"]
    return (
        note["note_id"],
        note["medical_specialty"],
        pc["value"],
        pc["evidence"],
        [(c["term"], c["evidence"]) for c in answer["comorbidities"]],
        pa["value"],
        pa["evidence"],
        dd["value"],
        dd["evidence"],
        [(s["factor"], s["evidence"]) for s in answer["social_risk_factors"]],
        fu["value"],
        fu["evidence"],
        meta["model_endpoint"],
        meta["model_name"],
        PROMPT_VERSION,
        PROMPT_SHA256,
        attempts,
        meta["at"],
        meta["job_run_id"],
    )


def reject_row(note_id: int, checked: Checked, raw: str | None, attempts: int, meta: dict):
    return (
        note_id,
        meta["model_endpoint"],
        meta["model_name"],
        PROMPT_VERSION,
        PROMPT_SHA256,
        attempts,
        checked.reason,
        raw,
        meta["at"],
        meta["job_run_id"],
    )


def extract_batch(
    spark: SparkSession, pending: DataFrame, call: ModelCall, meta: dict
) -> tuple[list[tuple], list[tuple], dict[str, int]]:
    """Ask, validate, retry once, and split the batch into (gold rows, reject rows, counts)."""
    notes = {r["note_id"]: r.asDict() for r in pending.collect()}

    def check(note_id: int, reply: tuple[str | None, str | None]) -> Checked:
        raw, error = reply
        return validate_answer(raw, notes[note_id]["transcription"], error)

    first = ask(spark, call, [(i, n["transcription"], "") for i, n in notes.items()])
    checked = {i: check(i, first[i]) for i in notes}
    failed = [i for i, c in checked.items() if not c.valid]
    second = ask(
        spark,
        call,
        [(i, notes[i]["transcription"], retry_suffix(checked[i].reason)) for i in failed],
    )
    gold, rejects = [], []
    for i, c in checked.items():
        attempts, raw = 1, first[i][0]
        if not c.valid:
            attempts, raw = 2, second[i][0]
            c = check(i, second[i])
        if c.valid:
            gold.append(gold_row(notes[i], c.answer, attempts, meta))
        else:
            rejects.append(reject_row(i, c, raw, attempts, meta))
    counts = {"notes": len(notes), "first_attempt_valid": len(notes) - len(failed)}
    counts |= {"retried": len(failed), "rejected": len(rejects)}
    return gold, rejects, counts


def model_name(endpoint: str) -> str | None:
    """The foundation model an endpoint serves, as the serving API reports it."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import DatabricksError

    try:
        config = WorkspaceClient().serving_endpoints.get(endpoint).config
        entity = config.served_entities[0]
        return entity.foundation_model.name if entity.foundation_model else entity.entity_name
    except (DatabricksError, IndexError) as e:
        print(f"{endpoint}: could not read the served model: {e}")
        return None


def ensure_tables(spark: SparkSession, prefix: str) -> None:
    for name, schema, doc in (
        (TABLE, GOLD_NOTE_RISK_FACTORS_SCHEMA, GOLD_NOTE_RISK_FACTORS_DOC),
        (REJECTS_TABLE, SILVER_LLM_REJECTS_SCHEMA, SILVER_LLM_REJECTS_DOC),
    ):
        spark.sql(create_statement(f"{prefix}.{name}", schema))


def build_llm(
    spark: SparkSession,
    prefix: str,
    run: RunRecorder,
    *,
    batch_size: int = BATCH_SIZE,
    job_run_id: str = "",
    call: ModelCall | None = None,
) -> dict[str, int]:
    ensure_tables(spark, prefix)
    gold_name, rejects_name = f"{prefix}.{TABLE}", f"{prefix}.{REJECTS_TABLE}"
    pending = pending_notes(
        spark.table(f"{prefix}.{NOTES_TABLE}"),
        spark.table(gold_name),
        spark.table(rejects_name),
        batch_size,
    )
    meta = {
        "model_endpoint": MODEL_ENDPOINT,
        "model_name": model_name(MODEL_ENDPOINT) if call is None else None,
        "at": datetime.now(UTC),
        "job_run_id": job_run_id,
    }
    gold, rejects, counts = extract_batch(
        spark, pending, call or ai_query_call(MODEL_ENDPOINT), meta
    )
    for name, schema, rows in (
        (gold_name, GOLD_NOTE_RISK_FACTORS_SCHEMA, gold),
        (rejects_name, SILVER_LLM_REJECTS_SCHEMA, rejects),
    ):
        if rows:
            spark.createDataFrame(rows, schema).write.format("delta").mode("append").saveAsTable(
                name
            )
        run.wrote(name, len(rows))
    apply_governance(spark, gold_name, GOLD_NOTE_RISK_FACTORS_SCHEMA, GOLD_NOTE_RISK_FACTORS_DOC)
    apply_governance(spark, rejects_name, SILVER_LLM_REJECTS_SCHEMA, SILVER_LLM_REJECTS_DOC)
    if counts["notes"]:
        run.metric(
            gold_name, "first_attempt_valid_rate", counts["first_attempt_valid"] / counts["notes"]
        )
        run.metric(gold_name, "retry_rate", counts["retried"] / counts["notes"])
    print(f"{gold_name}: {json.dumps(counts)}")
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Extract risk factors from the next notes.")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--schema", default="readmission")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    add_ops_arguments(parser)
    args = parser.parse_args(argv)
    with track_run("llm_extract", args) as run:
        spark = SparkSession.builder.getOrCreate()
        build_llm(
            spark,
            f"{args.catalog}.{args.schema}",
            run,
            batch_size=args.batch_size,
            job_run_id=args.job_run_id,
        )


if __name__ == "__main__":
    main()
