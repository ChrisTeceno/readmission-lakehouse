# Dashboard wireframe and client questions

Status: agreed before build (2026-10-21)

Contoso Health is a fictional client. Its 12 facilities are real public CMS Certification
Numbers from Ohio relabeled with invented names (see the README), so every Contoso number on
these pages is public CMS data or synthetic operational data, never a real hospital's own.

This page is the design the AI/BI dashboard and the Genie space are built from. It was written
and committed before any dashboard file, so the dashboard answers questions Contoso asked
rather than questions the data happened to make easy.

## The five questions

Written as Contoso's quality and finance leads would ask them in discovery.

| # | Contoso asks | Answered on | Source tables |
|---|---|---|---|
| Q1 | Which of our facilities and conditions are above the penalty cutoff this year? | Contoso scorecard | `gold_contoso_scorecard`, `gold_penalty_mart` |
| Q2 | How does our readmission ratio trend compare with Ohio and national hospitals? | National penalty landscape | `gold_facility_condition_year`, `gold_penalty_mart` |
| Q3 | Which of our facility and condition pairs does the model expect above the cutoff next cycle? | Predictions | `gold_penalty_predictions`, `gold_contoso_scorecard` |
| Q4 | Where is follow-up call completion lowest, and is it where we are penalized? | Follow-up operations | `gold_contoso_scorecard` |
| Q5 | Is the data pipeline healthy: did the last run succeed, how long did it take, and which quality checks failed? | Pipeline health | `ops_run_log`, `ops_dq_metrics` |

What each answer has to say, so the build can be checked against it:

- **Q1.** "This year" is FY2027, the latest HRRP year loaded. Its cutoff is the published peer
  group median, so the answer is exact. The page lists every Contoso facility and condition with
  its ERR, cutoff and above or below, and counts the conditions above the cutoff per facility.
- **Q2.** A line per group (Contoso, Ohio, all hospitals) of the mean ERR by fiscal year, FY2013
  to FY2027, and the share of hospitals penalized each year. From FY2019 the cutoff is the
  published peer group median; the few rows CMS published no median for use 1.0
  (`cutoff_is_approximate`), and the page's caption names them (ADR 0008; until 2026-10-28
  every FY2019 to FY2026 row used 1.0, ADR 0006).
- **Q3.** The FY2028 probability per Contoso facility and condition from the `penalty_model`
  champion, sorted highest first, with last year's above-cutoff flag beside it. The page says
  these are probabilities, that the model is weaker on FY2027 (test AUC 0.868
  on FY2026, 0.679 on FY2027, `docs/model-card.md`), and that FY2028 is likelier to look like
  FY2027.
- **Q4.** Completed follow-up calls over discharges per facility and condition, lowest first,
  with calls within 72 hours beside it and the FY2027 above-cutoff flag. Contoso's records start
  in September 2023, so only FY2026 and FY2027 have operational data; the page says how many
  months each rate covers. The page does not claim the calls cause the ratio; that is the pilot's
  question.
- **Q5.** The latest run's status per task, task duration across runs, and every failed data
  quality check with its value and threshold.

Each question is also a Genie sample question (`docs/genie.md`).

## Pages

Low fidelity: boxes are widgets, the text inside says what each shows. Filters sit across the
top of a page and apply to every widget on it.

### 1. Contoso scorecard (Q1)

```
+--------------------------------------------------------------------------+
| Contoso Health scorecard, FY2027              [Fiscal year v] [Facility v]|
| Fictional client: real Ohio CCNs relabeled with invented names.           |
+------------------+------------------+------------------+-----------------+
| Facilities with  | Conditions above | Facility-condition| Mean ERR       |
| >= 1 condition   | cutoff           | pairs reported    | (Contoso)      |
| above cutoff     |                  |                   |                |
|      [ n ]       |     [ n ]        |     [ n ]         |   [ x.xx ]     |
+------------------+------------------+------------------+-----------------+
| Conditions above cutoff, by facility (bar, stacked by condition)          |
|  CH01 ####                                                                |
|  CH02 #####                                                               |
|  ...                                                                      |
+--------------------------------------------------------------------------+
| Facility | Condition | ERR | Cutoff | Approx. cutoff? | Above? | ERR change|
| ...table, above-cutoff rows first...                                      |
+--------------------------------------------------------------------------+
| Caption: 1.0 to FY2018, the published peer group median from FY2019;     |
| 1.0 stands in only where CMS published no median.                         |
+--------------------------------------------------------------------------+
```

### 2. National penalty landscape (Q2)

```
+--------------------------------------------------------------------------+
| National penalty landscape                         [Condition v]          |
+--------------------------------------------------------------------------+
| Mean ERR by fiscal year (line): Contoso / Ohio / all hospitals            |
|  1.02 |      ___/\___                                                     |
|  1.00 |-----/--------\------- cutoff 1.0                                  |
|  0.98 |____/          \___                                                |
|       FY13 ......................................... FY27                 |
+-------------------------------------+------------------------------------+
| Share of hospitals penalized by FY   | Hospitals penalized by state,     |
| (bar)                                | latest year (table, top states)   |
+-------------------------------------+------------------------------------+
| Caption: bars colored by cutoff rule (1.0 to FY2018, peer median after);  |
| rows still on a 1.0 stand-in are named.                                   |
+--------------------------------------------------------------------------+
```

### 3. Predictions (Q3)

```
+--------------------------------------------------------------------------+
| FY2028 penalty predictions                     [Facility v] [Condition v] |
| Probabilities from the LightGBM champion; see the model card.             |
+------------------+------------------+------------------------------------+
| Contoso pairs    | Contoso pairs    | Model version, scored at           |
| predicted above  | above in FY2027  |                                    |
|     [ n ]        |     [ n ]        |  [ penalty_model v4, 2026-10-21 ]  |
+------------------+------------------+------------------------------------+
| Facility | Condition | P(above cutoff FY2028) | Above FY2027? | Decile    |
| ...sorted by probability, highest first...                                |
+--------------------------------------------------------------------------+
| Predicted probability by condition, all hospitals (bar of mean P)         |
+--------------------------------------------------------------------------+
| Caption: test AUC 0.868 on FY2026, 0.679 on FY2027; FY2028 follows        |
| FY2027, so expect the lower figure.                                       |
+--------------------------------------------------------------------------+
```

### 4. Follow-up operations (Q4)

```
+--------------------------------------------------------------------------+
| Follow-up operations                      [Fiscal year v: 2026, 2027]     |
| Synthetic operational data, from September 2023.                          |
+--------------------------------------------------------------------------+
| Follow-up rate by facility (bar, lowest first), colored by above-cutoff   |
+--------------------------------------------------------------------------+
| Facility | Condition | Months | Discharges | Completed | Rate | 72h rate | |
| Above cutoff?  ...lowest rate first...                                    |
+--------------------------------------------------------------------------+
| Caption: rates are completed calls over all-payer discharges; a low rate  |
| beside a penalty is a place to look, not evidence the calls lower the ERR.|
+--------------------------------------------------------------------------+
```

### Added 2026-10-26: Penalty dollars and follow-up calls

Added after the readout, before Pipeline health, when Contoso asked what the penalty costs and
whether follow-up calls would pay for themselves (`docs/penalty-dollars.md`).

```
+--------------------------------------------------------------------------+
| Penalty dollars and follow-up calls: every dollar figure is an estimate   |
| Timeline: FY2026 paid, FY2027 being paid (fixed), FY2028 mostly baked in, |
| FY2029 on can still change                                                |
+------------------------------------+-------------------------------------+
| Estimated penalty, FY2026          | Estimated penalty, FY2027           |
+------------------------------------+-------------------------------------+
| Penalty by facility, FY2026 (bar)  | Penalty by facility, FY2027 (bar)   |
+------------------------------------+-------------------------------------+
| What changed FY2026 to FY2027 (table: facility, condition, both years)    |
+------------------------------------+-------------------------------------+
| Break-even drop, pass-through 0.5  | Break-even drop, pass-through 1.0   |
+------------------------------------+-------------------------------------+
| Scenarios (table: effect, cost level, pass-through, cost, penalty         |
| avoided, Contoso net, break-even, payer savings shown apart)              |
+--------------------------------------------------------------------------+
```

### 5. Pipeline health (Q5)

```
+--------------------------------------------------------------------------+
| Pipeline health: readmission_pipeline                                     |
+------------------+------------------+------------------+-----------------+
| Last run status  | Last run ended   | Failed tasks,    | Failed DQ checks|
|                  |                  | last run         | (all runs)      |
+------------------+------------------+------------------+-----------------+
| Latest attempt per task (table: task, status, started, minutes, rows,     |
| error)                                                                    |
+--------------------------------------------------------------------------+
| Task duration by run (line, one series per task)                          |
+--------------------------------------------------------------------------+
| Failed data quality checks (table: run, task, table, metric, value,       |
| threshold, measured at)                                                   |
+--------------------------------------------------------------------------+
```

## Decisions taken in the design review

- One dashboard, five pages, rather than one per audience: Contoso's quality lead and the data
  team share the same Gold tables, and the pipeline-health page tells the quality lead whether
  the other four are current.
- Q1 uses FY2027 by default because it is the one recent year with the exact cutoff. Earlier
  years stay selectable, with the approximate-cutoff flag shown as a column. Updated
  2026-10-28: every year from FY2019 now has its published cutoff (ADR 0008); FY2027 stays the
  default as the latest year, and the flag marks only the rows CMS published no median for.
- No dollar amounts. The Gold tables carry ERRs and above-cutoff flags, not the payment
  adjustment factor, so the dashboard does not estimate the penalty's size. Superseded
  2026-10-26: the penalty dollars page shows estimated dollars from the published payment
  adjustment factor and estimated base payments.
- The predictions page shows probabilities, not a yes or no, because the model card says the
  probabilities are overconfident at both ends on FY2027.
