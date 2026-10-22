# LLM risk factor extraction

The `llm_extract` job task reads MTSamples notes from `silver_clinical_notes`, asks a
Databricks-hosted model (through `ai_query`) for the readmission risk factors below, validates
each answer against `src/readmission/llm/schema.json` and writes the valid ones to
`gold_note_risk_factors`. MTSamples is public transcription data, not patient records, so no PHI
is involved at any step.

## Fields and labelling rules

The same rules are the extraction prompt and the guide for the 50 evaluation labels in
`tests/fixtures/llm_labels.jsonl`. Every answer is about the encounter the note documents.

| Field | Values | Rule |
|---|---|---|
| `primary_condition` | `AMI`, `HF`, `PN`, `COPD`, `CABG`, `HIP_KNEE`, `other` | The HRRP condition that is the main reason for the encounter: acute myocardial infarction, heart failure, pneumonia, COPD exacerbation, a coronary artery bypass graft procedure, or an elective total hip or knee arthroplasty. Anything else, including an acute coronary syndrome not documented as an infarction, is `other`. |
| `comorbidities` | list of terms from a fixed vocabulary of 23 | Chronic conditions the note documents the patient as having (history, problem list, assessment), including the primary condition. Denied conditions, family history and possible or rule-out diagnoses are left out. Synonyms map to one term: ESRD to `chronic_kidney_disease`, dyslipidemia and hypercholesterolemia to `hyperlipidemia`, degenerative joint disease to `osteoarthritis`, reflux esophagitis to `gerd`, any malignancy (in remission or resected included) to `cancer`. |
| `prior_admissions_mentioned` | true or false | The note says the patient had a hospital admission before this encounter. Emergency department visits, prior surgeries and later admissions do not count. |
| `discharge_disposition` | `home`, `home_with_services`, `skilled_nursing_facility`, `rehab_facility`, `hospice`, `transferred`, `left_against_medical_advice`, `expired`, `not_stated` | Where the patient went at the end of this encounter, only when the note states it. "Discharged" with no destination, a transfer to the recovery room or ICU, and admissions are `not_stated`. |
| `social_risk_factors` | list of `lives_alone`, `transportation`, `medication_access` | Documented as present: the patient lives alone; lacks transportation to care; cannot afford or obtain medications. Smokers in the household, never driving without a stated barrier, and forgetting doses do not count. |
| `follow_up_planned` | true or false | The note plans or instructs a follow-up visit or contact with a clinician, timed or not. "Return as needed" or "if worse" alone is false. |

Each value carries an `evidence` span: a short quote copied from the note. It is required for a
positive value (a condition other than `other`, a disposition other than `not_stated`, a true
flag, every list item) and null otherwise.

## Evaluation labels

`tests/fixtures/llm_labels.jsonl` holds 50 notes drawn by `readmission.llm.sample` (seed
20261021, stratified by specialty with proportional allocation, so 20 of the 50 are Surgery).
The labels were drafted from the note text before any model was run. On 2026-10-21 the project
lead checked all 50 against the rules above; Chris Teceno, a clinician, then reviewed all 50
and decided the four judgment calls, after which `needs_review` was cleared on every row;
`labeler_note` still records the calls that were checked.
A label changes only to fix a labelling error, in its own commit, recorded below.

Label changes (the other 49 labels were checked and kept, including cancer in notes 2854 and 12
and tobacco not counted as `substance_use` in note 1262, which Chris decided):

- 2026-10-21, note 3360, `comorbidities`: added `valvular_disease` (evidence "Moderate tricuspid
  regurgitation."). Moderate tricuspid regurgitation on echo counts as valve disease; decided by
  Chris during the label review.

## Evaluation and model choice

`scripts/evaluate_llm_extraction.py` runs each candidate endpoint over the 50 notes with the
job's prompt, schema and checks, and logs one MLflow run per model to
`/Users/chris.teceno@gmail.com/readmission-llm-extraction`. The comparison and the choice of
`databricks-gpt-oss-120b` are in `engagement/adr/0002-llm-risk-factor-extraction.md`. All metrics
were first measured against the drafts and then rescored on the reviewed labels from the saved
predictions (`--rescore-run`, no model called again); the rescored MLflow runs are tagged
`labels_status=reviewed`. The ranking did not change: mean field F1 is 0.81 for gpt-oss-120b
(0.82 on the drafts), 0.70 and 0.54.

A fourth candidate runs locally: `--local` sends the same prompt, JSON schema, checks and retry to
Gemma 4 26B A4B (8-bit) served by omlx on the Mac, with latency and tokens per second logged and
the run tagged `backend=local`. Only the note read touches the SQL warehouse. It scores 0.85 mean
field F1 on the reviewed labels (MLflow run `9248f5286c1f4c6ea33f86020243a289`), 0.04 above
gpt-oss-120b on 50 notes. A local or on-prem model keeps note text inside the client's network, the
argument in [`engagement/phi-note.md`](../engagement/phi-note.md), and this run shows no F1 cost
for it. It is not part of the Databricks job, because serverless cannot reach the Mac, and the
job stays on gpt-oss-120b.

## The job task

`llm_extract` runs after `silver`, beside `gold`. Each run takes the next `BATCH_SIZE` (100)
notes with no row in `gold_note_risk_factors` and no reject under the current endpoint and
prompt, lowest note id first, so the 2,357 notes are covered in 24 runs. Valid answers append to
`gold_note_risk_factors` with the endpoint, model name, prompt version and hash, and attempt
count; notes that fail twice append to `silver_llm_rejects` with the reason and the raw reply.
A prompt or model change leaves extracted notes as they are; rejected notes are tried again
under the new prompt. `ops_dq_metrics` records each run's `first_attempt_valid_rate` and
`retry_rate`.

```
uv run --group ml python scripts/evaluate_llm_extraction.py --without-response-format
uv run --group ml python scripts/evaluate_llm_extraction.py --models --local --concurrency 2
databricks bundle run readmission_pipeline -t dev --only llm_extract
```
