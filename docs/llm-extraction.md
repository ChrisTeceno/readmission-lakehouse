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
The labels were drafted from the note text before any model was run and are all marked
`needs_review: true`; `labeler_note` records the judgment calls a reviewer should check first.
A label changes only to fix a labelling error, in its own commit, recorded below.

Label changes: none. The three-model run (2026-10-21) was reviewed for labelling errors among the
disagreements and none was found; the disagreements are listed in ADR 0002.

## Evaluation and model choice

`scripts/evaluate_llm_extraction.py` runs each candidate endpoint over the 50 notes with the
job's prompt, schema and checks, and logs one MLflow run per model to
`/Users/chris.teceno@gmail.com/readmission-llm-extraction`. The comparison and the choice of
`databricks-gpt-oss-120b` are in `engagement/adr/0002-llm-risk-factor-extraction.md`. All metrics
are provisional until the labels are confirmed (0 of 50 so far).

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
uv run --with mlflow-skinny python scripts/evaluate_llm_extraction.py --without-response-format
databricks bundle run readmission_pipeline -t dev --only llm_extract
```
