# ADR 0002: Extract note risk factors with gpt-oss-120b through `ai_query`

Status: accepted (2026-10-21). The metrics below are measured against the 50 reviewed labels: the
project lead drafted them and checked all 50 against the written rules; Chris Teceno, a
clinician, reviewed all 50 and decided the four judgment calls. The first figures, against the unreviewed drafts, are under
"Label review" below.

## Context

Contoso wants readmission risk factors that CMS data does not carry: comorbidities, prior
admissions, discharge disposition, social risk and follow-up plans. They sit in free-text notes.
The lakehouse stands in for Contoso's notes with MTSamples, public transcription samples with no
PHI (`silver_clinical_notes`, 2,357 notes). The extraction has to keep the notes inside the
workspace, return structured values with a quote for each, and be measured before the job relies
on it. Field definitions and labelling rules are in `docs/llm-extraction.md`.

Free Edition serves Foundation Model API endpoints on pay-per-token. Chris chose to compare three
of them (decision on the task card, 2026-10-21), all READY on the workspace that day:
`databricks-gpt-oss-120b`, `databricks-meta-llama-3-3-70b-instruct` and
`databricks-meta-llama-3-1-8b-instruct`. No external API is used, so no note leaves the
workspace.

A fourth candidate was added the same day at Chris's request: Gemma 4 26B A4B (8-bit, from
`mlx-community/gemma-4-26b-a4b-it-8bit`) served on the Mac by omlx. It is the local option for a
client whose notes must stay inside its own network (`engagement/phi-note.md`). It is not part of
the Databricks job, because serverless cannot reach the Mac.

## How the models were compared

- **Same call as the job.** Each model got the same prompt (`PROMPT_VERSION` v1), temperature 0,
  4,096 max tokens and the JSON schema as `ai_query`'s `responseFormat`, one note per statement
  on the SQL warehouse (`scripts/evaluate_llm_extraction.py`).
- **Same checks as the job.** An answer is valid when it parses, matches `schema.json` after term
  normalization, and every evidence span is a quote from the note (whitespace runs and case are
  ignored, nothing else). An invalid first answer is asked once more with the reason; a second
  failure is a reject, which scores as predicting nothing.
- **Labels.** 50 notes, seed 20261021, stratified by specialty with proportional allocation. The
  labels were drafted from the note text (AI-assisted) and committed before any model ran. The
  three models ran against those drafts; the lead then checked all 50 against the rules in
  `docs/llm-extraction.md` and Chris decided the four judgment calls, which changed one label
  (note 3360 gains `valvular_disease`). The runs were not repeated: the saved predictions were
  rescored against the reviewed labels.
- **Scoring.** Scalar fields as slot filling (a positive is a condition other than `other`, a
  disposition other than `not_stated`, or a true flag); list fields per term after
  normalization (lowercase, spaces to underscores, synonyms to the vocabulary term),
  micro-averaged. Details in `src/readmission/llm/metrics.py`.
- **Local candidate.** The same prompt, schema, checks and retry, sent as OpenAI-compatible chat
  completions to omlx on 127.0.0.1:8000 with `response_format` set to the same JSON schema, two
  requests in flight. Note text was read from `silver_clinical_notes` through the SQL warehouse;
  inference stayed on the Mac. It ran once, directly against the reviewed labels.
- **MLflow.** One run per model in `/Users/chris.teceno@gmail.com/readmission-llm-extraction`,
  with each note's answer as a `predictions/` artifact. The first runs were tagged
  `labels_status=draft` and `metrics_provisional=true`; the rescored runs are tagged
  `labels_status=reviewed` and `rescored_from_run`, without `metrics_provisional`.

## Results

Label support (positives across the 50 notes): primary condition 4 (3 HF, 1 CABG),
comorbidities 55 terms, prior admissions 3, discharge disposition 8, social risk factors 2,
follow-up planned 13.

| Metric | gpt-oss-120b | llama-3.3-70b | llama-3.1-8b | gemma-4-26b-a4b (local) |
|---|---|---|---|---|
| Mean field F1 | **0.81** | 0.70 | 0.54 | **0.85** |
| Schema-valid rate, first answer | 1.00 | 1.00 | 0.96 | 1.00 |
| Valid (schema and quotes), first answer | 0.88 | 0.96 | 0.64 | 0.96 |
| Retry rate | 0.12 | 0.04 | 0.36 | 0.04 |
| Reject rate after retry | 0.04 | 0.04 | 0.28 | 0.00 |
| Schema-valid without `responseFormat` | 1.00 | 0.92 | 0.52 | not run |
| Latency per note, median / p90 (s) | 9.6 / 18.2 | 4.3 / 6.6 | 2.3 / 3.2 | 9.3 / 12.7 |
| primary_condition P / R | 0.60 / 0.75 | 1.00 / 0.50 | 1.00 / 0.50 | 1.00 / 1.00 |
| comorbidities P / R | 1.00 / 0.76 | 0.85 / 0.75 | 0.49 / 0.44 | 1.00 / 0.87 |
| prior_admissions_mentioned P / R | 0.60 / 1.00 | 0.25 / 0.67 | 0.33 / 0.33 | 0.75 / 1.00 |
| discharge_disposition P / R | 0.73 / 1.00 | 0.78 / 0.88 | 0.71 / 0.62 | 0.57 / 1.00 |
| social_risk_factors P / R | 0.67 / 1.00 | 1.00 / 0.50 | 0.33 / 0.50 | 0.50 / 1.00 |
| follow_up_planned P / R | 1.00 / 0.92 | 0.76 / 1.00 | 0.75 / 0.69 | 0.81 / 1.00 |

MLflow runs (rescored on the reviewed labels): gpt-oss-120b `e18026f5a77b444db0ecc9fb8163c8bf`,
llama-3.3-70b `321fafc2509d4fc09edc42476355f193`, llama-3.1-8b
`0fe0fa0c99ce40e4a4232a139c9ecd70`; gemma-4-26b-a4b (scored directly on the reviewed labels,
tagged `backend=local`) `9248f5286c1f4c6ea33f86020243a289`. The Databricks latencies are the
statement's wall time from a laptop with four statements in flight, warehouse overhead
included, so they compare the models rather than predict the job's throughput. The local
latency is one chat completion on the Mac with two in flight, about 20 output tokens a second
per request.

`responseFormat` with a JSON schema works through `ai_query` for all three models. It keeps
answers on the schema (the only schema failures were two llama-3.1-8b replies that ran to the
token limit and were cut off mid-string), but it cannot say that evidence must be quoted from
the note. Every first-answer failure of the two larger models was an evidence problem: a span
paraphrased, joined from two places with "...", or longer than 300 characters. llama-3.1-8b
added evidence-less list items and long spans. The validate, retry once, reject path is what
catches those, and it stays for every model.

## Decision

`databricks-gpt-oss-120b`, named in `readmission.llm.build.MODEL_ENDPOINT`. It has the highest
mean field F1 (0.81 against 0.70), and the margin comes from the fields a readmission model needs
most: comorbidities with no false positive and prior admissions with full recall. Schema-valid
rates tie at 1.00, so the tie-break rule did not come into play. It is the slowest of the three
(median 9.6 s a note), which does not matter for a batch job of 100 notes a run.

## Local model

On the 50 reviewed labels gemma-4-26b-a4b scores 0.85 mean field F1 against 0.81 for the chosen
model, with no reject after retry (two retries, both fixed), so keeping note text on the Mac cost
no accuracy here. It reads more than gpt-oss: recall is 1.00 on every field but comorbidities
(0.87), and precision is lower on discharge disposition (6 false positives), follow-up planned
(3) and social risk factors (2). The difference of 0.04 rests on the same small samples as the
rest of the table, so it shows the local model is competitive, not that it is better.

What it means for Contoso: a local or on-prem model keeps note text inside the client's network
and out of any vendor's endpoint, which is the argument in `engagement/phi-note.md`, and this run
shows no F1 cost for it. It is not a swap for the job. Serverless compute cannot reach a Mac, so
`llm_extract` stays on `databricks-gpt-oss-120b`; a client that wants a model it hosts would serve
one to the workspace (a private endpoint or a self-managed serving cluster) and rerun this
comparison on its own notes. The model was chosen on US origin (Google) and on a 2026-09-01
measurement that the 8-bit build holds a JSON schema where the 4-bit build changes shape on
long inputs. The 8-bit build was run once, with `response_format` sent, so whether omlx enforces
the schema or the model keeps to it by itself is not measured.

## Label review

The lead checked all 50 drafted labels against the rules in `docs/llm-extraction.md`, and Chris
decided the four judgment calls in chat: note 3360 gains `valvular_disease` (moderate tricuspid
regurgitation on echo counts as valve disease), and the labels for cancer in notes 2854 and 12 and
for tobacco in note 1262 stay as drafted. That one added term is the only change. Against the
drafts the run scored gpt-oss-120b at 0.82 mean field F1 (comorbidities recall 0.78 on 54 terms)
and llama-3.3-70b at comorbidities recall 0.76; against the reviewed labels those are 0.81 (0.76 on
55 terms) and 0.75. Nothing else moved, and the ranking and the choice hold.

## First job run

The `llm_extract` task's first run (2026-10-21, job run 648611251725236, note ids 0 to 100) took
about 4 minutes 20 seconds end to end for 100 notes: 71 valid on the first answer, 15 more on
the retry, 14 rejected. That reject rate (14 percent) is well above the evaluation's 4 percent,
because the lowest note ids are mostly Urology operative notes (83 of them). In 10 of the 14
rejects the failing span is evidence the model gave for `primary_condition: other`, where null
was allowed, and in 7 of the 14 the only difference from the note is MTSamples' comma after a
section heading (`PREOPERATIVE DIAGNOSIS:, Left inguinal hernia.` quoted as `PREOPERATIVE
DIAGNOSIS: Left inguinal hernia.`). The next prompt version should ask for null evidence on
negative values, or the quote check should ignore that heading comma; either change is
re-evaluated on the 50 notes under a new `PROMPT_VERSION` before the job uses it, and the
rejected notes are retried automatically under the new prompt.

## Consequences

- **Small samples on three fields.** With 4 primary-condition positives, 3 prior admissions and 2
  social risk factors, one note moves those fields' precision or recall by 0.25 or more. The
  comparison is decided by comorbidities (55 terms) and follow-up (13), where the sample is
  larger. The lead drafted the labels and Chris Teceno, a clinician, reviewed all 50; the one change is
  recorded in `docs/llm-extraction.md`.
- **Proportional sampling** makes the 50 notes look like the population the job processes (20
  are Surgery operative notes), so most notes are negative for most fields. A sample enriched
  for discharge summaries would measure recall on rare fields better; that is a second sample,
  not a replacement.
- **Disagreements worth a reviewer's look** (gpt-oss): it calls a note's main problem HF when
  heart failure is a background condition (an admission for syncope, a pacemaker for
  tachy-brady), counts a later admission as a prior one, and reads a previous emergency visit's
  "discharged home" as this encounter's disposition.
- **No version pin.** Pay-per-token endpoints report the foundation model
  (`system.ai.gpt-oss-120b`) but no version, so `gold_note_risk_factors` stores the endpoint,
  model name, prompt version, prompt hash and extraction time with each row. A silent model
  update would show up as a change in the job's `first_attempt_valid_rate` in `ops_dq_metrics`.
- **No PHI.** MTSamples is public transcription data. With real notes the same design holds,
  since `ai_query` keeps the text inside the workspace (a local model keeps it on the machine), but the Gold table would carry PHI in
  its evidence spans, needing PHI tags, restricted grants and a BAA covering the endpoint.
