# Genie space and dashboard: the five client questions

How the AI/BI dashboard and the Genie space answer the five questions Contoso agreed to in
`engagement/wireframe.md`, how each was tested, what Genie got wrong and the instruction that
fixed it. Contoso Health is a fictional client; its facilities are real public Ohio CCNs
relabeled with invented names.

## What is deployed

Both are bundle resources and deploy with `databricks bundle deploy -t dev`:

| Resource | Bundle file | Definition |
|---|---|---|
| Dashboard `Contoso Health readmissions` | `resources/dashboard.yml` | `src/dashboards/readmission.lvdash.json` |
| Genie space `Contoso Health readmissions` | `resources/genie.yml` | `src/genie/readmission.geniespace.json` |

Both run on the workspace's one SQL warehouse, the 2X-Small `Serverless Starter Warehouse`,
which `databricks.yml` looks up by name (`var.warehouse_id`); no warehouse was created. The
dashboard's datasets name tables without a catalog or schema, and `dataset_catalog` and
`dataset_schema` point them at `workspace.readmission`. Deploy publishes the dashboard.

Genie spaces are a bundle resource (`genie_spaces`) on Databricks CLI v1.21.0 with the direct
deployment engine this bundle already uses, so no script was needed. The space body follows the
Genie API's `serialized_space` version 2: tables, one text instruction, column synonyms, sample
questions and example SQL, every collection sorted by id or name and every id 32 lowercase hex
characters (the API rejects anything else). `tests/test_dashboards.py` checks those rules, that
every dashboard widget reads a dataset and columns that exist, and that the cutoff,
fictional-client and model captions are on their pages.

Space id on the dev workspace: `01f1cd785d031b07b67f5e804592b151`. Dashboard id:
`01f1cd782bf1143185867b26598d81c0`.

### Genie's tables

`gold_contoso_scorecard`, `gold_facility_condition_year`, `gold_penalty_mart`,
`gold_penalty_predictions`, `ops_run_log` and `ops_dq_metrics`. Genie reads each table's and
column's Unity Catalog comment (set by the `gold` task, see `docs/gold.md` and
`docs/governance.md`), so the space adds only synonyms ("ERR", "readmission ratio",
"follow-up completion", "next cycle") and one text instruction. The instruction says Contoso is
fictional, that "this year" is the latest fiscal year (FY2027) and "next cycle" is FY2028, that
the tables carry no dollar amounts, and that any answer counting rows above the cutoff for FY2019
or later must include `cutoff_is_approximate` and name any rows on the 1.0 stand-in (FY2023
pneumonia, and hospitals missing from a year's supplemental file; ADR 0008). It also carries the
model's caveat (test AUC 0.868 on FY2026, 0.679 on FY2027). Until 2026-10-28 it said every
FY2019 to FY2026 cutoff was approximate (ADR 0006); the test rounds below ran under that
wording.

## How Genie was tested

Each question went to the deployed space through the Conversation API, one new conversation per
question, and the generated SQL and its result were read back:

```
databricks genie start-conversation <space_id> "<question>" -o json
databricks genie get-message-attachment-query-result <space_id> <conversation_id> <message_id> <attachment_id> -o json
```

Round 1 ran the five questions on a space with the table synonyms and a first text instruction
but no example SQL. Each answer was checked against a query written by hand on the same tables.
Round 2 added the fixes below and reran the five questions plus a reworded version of each
question that had failed, so a fix that only matched the exact sample wording would show.
Rounds 3 and 4 fixed the two problems round 2 still had. All runs: 2026-10-21, on Gold from the
job runs of that day.

The figures below are those runs' answers. Since the published peer medians were loaded and
model version 7 became champion (2026-10-28, ADR 0008), two of them moved: FY2026 has 33 Contoso
pairs above the cutoff (was 31, all on the 1.0 stand-in then, none now), and FY2028 has 29 pairs
at 0.5 or more (was 28). FY2027 figures are unchanged.

## The five questions

### Q1. Which of our facilities and conditions are above the penalty cutoff this year?

- **Dashboard:** Contoso scorecard. Fiscal year filter, default FY2027. Counters: 10 facilities
  with a condition above the cutoff, 25 facility-condition pairs above it, 67 pairs with an
  ERR, mean ERR 0.999. A bar per facility stacked by condition, and the full table with ERR,
  cutoff, `cutoff_is_approximate` and the ERR change.
- **Round 1, wrong.** Genie filtered on `contoso_months > 0`, the operational-data filter, and
  returned 14 pairs instead of 25. The 11 it dropped are above-cutoff conditions with no Contoso
  call records, such as CH02 CABG (ERR 1.0971) and CH03 HIP_KNEE (1.2235).
- **Fix.** Instruction: "Filter on contoso_months > 0 only for follow-up call questions: penalty
  questions use every row, including conditions with no Contoso operational data." Plus an
  example SQL for the question.
- **Round 2 answer, right.** 25 pairs, each with ERR, cutoff and `cutoff_is_approximate`
  (false for every FY2027 row), and the summary noted FY2027's cutoff is exact:

  ```sql
  SELECT contoso_facility_id, contoso_facility_name, measure, fiscal_year,
    excess_readmission_ratio, penalty_cutoff, cutoff_is_approximate
  FROM workspace.readmission.gold_contoso_scorecard
  WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM workspace.readmission.gold_contoso_scorecard)
    AND above_cutoff
  ORDER BY contoso_facility_id, excess_readmission_ratio DESC
  ```

- **Reworded check.** "List every Contoso hospital and condition over the HRRP cutoff in
  FY2026." Genie wrote its own SQL (`fiscal_year = 2026 AND above_cutoff`), returned 31 pairs
  with `cutoff_is_approximate = true` on each, and said in the answer that FY2026's cutoffs are
  approximate. That is the approximate-cutoff instruction working on a year the example does not
  cover.

### Q2. How does our readmission ratio trend compare with Ohio and national hospitals?

- **Dashboard:** National penalty landscape. A line per group (Contoso, Ohio, all hospitals) of
  mean ERR by fiscal year with a condition filter; the share of hospitals with a condition above
  the cutoff per year, colored by cutoff rule (1.0 to FY2018, peer group median after); hospitals
  penalized by state in FY2027. Caption on the cutoff rule and the rows still on 1.0.
- **Round 1, SQL right, summary wrong.** Genie's three-way `UNION ALL` of yearly means was
  correct, but its summary said Contoso was below both peer groups "except for FY2025 and FY2026".
  Contoso's mean was also above both in FY2023 and FY2024 (1.0071 and 1.0051 against national
  1.0021 and 1.0018).
- **Fix, two steps.** An example SQL that puts the three means side by side with
  `contoso_minus_ohio` and `contoso_minus_national`, and an instruction to name every year above
  each group. Round 3's summary then listed the years but merged them: it put FY2015 and FY2016
  "above both", when Contoso was above the national mean but below Ohio's in those years. Round
  4's instruction: "give two separate lists, the years where contoso_minus_ohio is positive and
  the years where contoso_minus_national is positive, read from those columns; never merge them
  into 'above both'."
- **Round 4 answer, right.** Above Ohio in FY2023 to FY2026; above national in FY2015, FY2016
  and FY2023 to FY2026; FY2027 0.9992, 0.0092 below Ohio and 0.0029 below national.
- **Reworded check.** "In which years was Contoso's average ERR worse than the national average?"
  returned FY2015, FY2016 and FY2023 to FY2026, which matches the hand query.

### Q3. Which of our facility and condition pairs does the model expect above the cutoff next cycle?

- **Dashboard:** Predictions. Counters: 28 Contoso pairs with probability 0.5 or more for
  FY2028, 25 above the cutoff in FY2027, `penalty_model` version 4. The Contoso pairs by
  probability with last year's flag, decile and national rank; mean probability by condition for
  all hospitals. Caption: these are probabilities, test AUC 0.874 on FY2026 against 0.678 on
  FY2027 (version 4; 0.868 and 0.679 for version 7, the champion from 2026-10-28), overconfident
  at both ends on FY2027, so expect FY2028 to look like the lower figure.
- **Round 1, wrong.** Genie joined `gold_penalty_predictions` straight to
  `gold_contoso_scorecard`, which has one row per fiscal year, so every prediction repeated once
  per year (15 times for most pairs). Its answer named one pair (CH11 HIP_KNEE, 0.995) where there are 28.
- **Fix.** Instruction: join through `SELECT DISTINCT contoso_facility_id,
  contoso_facility_name, facility_id FROM gold_contoso_scorecard`, never the table itself. Plus
  an example SQL.
- **Round 2 answer, right.** 28 pairs sorted by probability, led by CH11 HIP_KNEE (0.995), CH11
  HF (0.992) and CH07 CABG (0.983), and the summary carried the FY2027 AUC caveat.
- **Reworded check.** "What are the 5 Contoso pairs most likely to be penalized in FY2028?"
  used the distinct join with `LIMIT 5`, got the same top five, and repeated the AUC caveat.

### Q4. Where is follow-up call completion lowest, and is it where we are penalized?

- **Dashboard:** Follow-up operations. Fiscal year filter (FY2026 or FY2027, default FY2027). The
  follow-up rate per facility, lowest first, colored by whether the facility has any condition
  above the cutoff; the facility and condition table with months covered, discharges, calls,
  the follow-up and 72-hour rates and the above-cutoff flag. Caption: synthetic data from
  September 2023; a low rate beside a penalty is a place to look, not evidence the calls change
  the ERR.
- **Round 1, wrong.** Genie returned only the single lowest row (CH09 COPD, 0.327) and joined
  `gold_penalty_mart.penalized`, which is per hospital, then said CH09 was "penalized for this
  condition". CH09 COPD is below the cutoff; CH09 is penalized for HIP_KNEE.
- **Fix.** Instruction: "Penalized for a condition" means `above_cutoff` on that facility,
  condition and year; `gold_penalty_mart.penalized` says nothing about a single condition. Plus an
  example SQL returning the ten lowest rows with `above_cutoff`.
- **Round 2 answer, right.** The ten lowest facility-condition rates, with the conclusion that
  CH09 COPD is not above the cutoff and that, of the ten lowest, only CH07 HF is.
- **Reworded check, wrong in round 2.** "Which Contoso facility has the lowest share of discharges
  with a completed follow-up call?" took the lowest condition row as the facility's rate (0.327).
  Round 3 added: a facility's rate is `SUM(calls_completed) / SUM(contoso_discharges)`, never the
  minimum or mean of its condition rates. Round 3 answer: CH09 at 0.371, which matches the
  dashboard's facility bar.
- **Dashboard bug found the same way.** The first version of the facility bar dropped rows with
  no call records before deciding whether a facility had a condition above the cutoff, so CH09
  showed as not penalized. The dataset now counts every condition, keeps only facilities with
  call data, and the caption says so.

### Q5. Is the data pipeline healthy: did the last run succeed, how long did it take, and which quality checks failed?

- **Dashboard:** Pipeline health. Counters: failed task attempts in the last run, hours since it
  ended, failed data quality checks across all runs. The last run, the latest attempt per task
  with duration, rows and error, task duration by run, and every failed check with value and
  threshold.
- **Round 1, misleading.** Genie took the last `job_run_id`, which was a run of only the
  `llm_extract` task, reported it succeeded in 3.58 minutes, and said no checks failed, because it
  looked for failed checks in that run only. It missed the one failed check on record (the gold
  task's `anomaly_rate` of 0.0503 against 0.05, run 1119069845046775) and said nothing about the
  other seven tasks.
- **Fix.** Instruction: a run can cover only some tasks, so report the last run and the latest
  attempt of each task, and list failed checks in the last run and in earlier runs with their run
  id, value and threshold. Plus an example SQL.
- **Round 2 answer, right.** Latest attempt of all eight tasks succeeded, 0.4 to 6.6 minutes;
  no failed check in the latest runs; one earlier failure, `anomaly_rate` 0.0503 against 0.05.
- **Reworded check.** "Did any task of the readmission pipeline fail recently?" listed the six
  failed attempts of 2026-10-21 (silver, gold and train, two attempts each), all followed by
  successful runs.

## Known limits

- Example SQL now exists for every sample question, so the five sample questions hit it
  directly; the reworded checks are what show the instructions generalize.
- Genie's summaries are written by a model. The tables were right in every round 2 answer; the
  wrong statements were in the summaries (Q2, Q4). Read the table under the summary.
- The counters "Hours since the last run ended" and "Failed task attempts, last run" read a run
  that may cover a subset of tasks; the latest-attempt table beside them covers every task.

## Screenshots

Workspace screenshots of the five dashboard pages and the answer to question 1 are in
`docs/evidence/dashboard.md`. The answer's "Condition Breakdown" sentence miscounts (HIP_KNEE 10,
HF 3, AMI 3 sums to 24; its own facility list gives 9, 4 and 4, which sums to 25), another case of
a wrong summary over a right table.
