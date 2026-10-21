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
  to FY2027, and the share of hospitals penalized each year. FY2019 to FY2026 use the 1.0
  stand-in cutoff (`cutoff_is_approximate`), so any above-cutoff count for those years carries
  that caption on the page.
- **Q3.** The FY2028 probability per Contoso facility and condition from the `penalty_model`
  champion, sorted highest first, with last year's above-cutoff flag beside it. The page says
  these are probabilities, that the model is weakest on the exact-cutoff year (test AUC 0.874
  on FY2026, 0.678 on FY2027, `docs/model-card.md`), and that FY2028 is likelier to look like
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
| Caption: FY2019 to FY2026 cutoffs are the 1.0 stand-in for the peer group |
| median; FY2027 uses the published median.                                 |
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
| Caption: FY2019 to FY2026 shares use the 1.0 stand-in cutoff              |
| (cutoff_is_approximate = true).                                           |
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
| Caption: test AUC 0.874 on FY2026 (approximate cutoff), 0.678 on FY2027   |
| (exact cutoff); FY2028 follows FY2027, so expect the lower figure.        |
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
  years stay selectable, with the approximate-cutoff flag shown as a column.
- No dollar amounts. The Gold tables carry ERRs and above-cutoff flags, not the payment
  adjustment factor, so the dashboard does not estimate the penalty's size.
- The predictions page shows probabilities, not a yes or no, because the model card says the
  probabilities are overconfident at both ends on FY2027.
