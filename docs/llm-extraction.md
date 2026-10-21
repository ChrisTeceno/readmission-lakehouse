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

Label changes: none yet.
