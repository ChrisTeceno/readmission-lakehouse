# User stories

Date: 2026-10-21

Contoso Health is a fictional client. Each story's acceptance criteria link to a file on main, or
are marked pending with the card that delivers them.

## US-01 Penalty position this year

As the **CFO**, I want to see every facility and condition above the penalty cutoff for the
latest fiscal year, so that I know where the exposure sits.

Acceptance:
- The Contoso scorecard page lists ERR, cutoff, approximate-cutoff flag and above-cutoff for each
  facility and condition, FY2027 by default: `src/dashboards/readmission.lvdash.json`.
- The answer matches a hand-written query: 25 facility-condition pairs above the cutoff across
  10 facilities (`docs/genie.md`, Q1).

## US-02 Trend against peers

As the **quality director**, I want Contoso's mean ERR by year beside Ohio and all hospitals, so
that I can say whether we are getting better or worse than others.

Acceptance:
- The national penalty landscape page shows the three lines for FY2013 to FY2027 with the
  cutoff caption: `src/dashboards/readmission.lvdash.json`, `docs/genie.md` (Q2).
- Rows on the 1.0 stand-in, where CMS published no peer median, are labelled as such:
  `docs/gold.md`, ADR 0008.

## US-03 Next cycle

As the **quality director**, I want the facility and condition pairs the model expects above the
cutoff in FY2028, so that I can plan before CMS publishes.

Acceptance:
- `gold_penalty_predictions` holds a probability, rank and decile per pair, scored by the
  `champion` alias: `docs/model-card.md`.
- The page states the model's weak year: test AUC 0.868 on FY2026 and 0.679 on FY2027
  (`docs/model-card.md`, `engagement/wireframe.md`).
- 29 Contoso pairs score 0.5 or more (`docs/model-card.md`).

## US-04 Follow-up completion

As the **CMO**, I want follow-up call completion by facility and condition beside the penalty
flag, so that I know where operations are thinnest.

Acceptance:
- The follow-up operations page shows rate, 72-hour rate and months covered, lowest first:
  `src/dashboards/readmission.lvdash.json`, `docs/genie.md` (Q4).
- The page says the rate beside a penalty is a place to look, not evidence the calls cause the
  ratio: `engagement/wireframe.md`.

## US-05 Does the penalty change behavior

As the **CFO**, I want to know whether being counted as penalized makes a hospital improve, so
that I do not fund a program on the assumption that it does.

Acceptance:
- The hypotheses and the wording of a null result are fixed before outcomes are examined:
  `docs/analysis-plan.md`.
- The estimate, interval, validity checks, sensitivity and the DiD check that disagrees in sign
  are all reported: `docs/causal-findings.md` (+0.0004, interval -0.0027 to +0.0047; the secondary sample
  through FY2025, added after the pre-registration, +0.0002, -0.0023 to +0.0032).
- The notebook and its outputs are in the repository: `analysis/02_causal.py`,
  `docs/causal/02_causal.html`.

## US-06 Would follow-up calls work

As the **CMO**, I want a designed pilot of post-discharge follow-up calls, so that Contoso can
test the program on its own facilities before scaling it.

Acceptance:
- Sample size, power, minimum detectable effect (3.82 percentage points at 80 percent power),
  primary metric, guardrails and analysis are stated before launch. Pending: pilot experiment
  design, PR #13 (`docs/pilot-design.md` on that branch).
- The operational baseline the design needs exists: `docs/lakebase.md`.

## US-07 Operational data in the lakehouse

As the **analytics lead**, I want Contoso's operational database loaded incrementally beside the
CMS data, so that reports do not need a manual export.

Acceptance:
- A second load after new rows picks up only the new rows (12, 50 and 100 rows appended, then
  0, 0, 0): `docs/lakebase.md`, incremental load test.
- The connector choice and its limits are recorded: `engagement/adr/0001-contoso-lakebase-connector.md`.
- Key Vault holds the Census key and Data Factory triggers the job. Pending: Azure Key Vault and
  ADF in Bicep (SPEC task 3.1), created only on the client's approval.

## US-08 Pipeline health

As the **analytics lead**, I want to see whether the last run succeeded and which quality checks
failed, so that I can trust the other pages.

Acceptance:
- The pipeline health page reads `ops_run_log` and `ops_dq_metrics`: `docs/data-sources.md`
  (run monitoring), `docs/genie.md` (Q5).
- A forced task failure writes a failed row and fails the job: `docs/data-sources.md`.
- A failed task emails the job owner: `resources/readmission_pipeline.yml`.

## US-09 Bad rows do not stop the run

As the **analytics lead**, I want malformed source rows set aside and counted, so that one bad
file does not block the quarterly refresh.

Acceptance:
- Rows that break a schema or constraint go to `silver_quarantine`; the run continues:
  `docs/governance.md`.
- Row-count, null-rate and county-match expectations fail the run before a table is overwritten:
  `docs/governance.md`.

## US-10 Risk factors from notes

As the **quality director**, I want readmission risk factors pulled from clinical notes with a
quote for each, so that I can see what the claims data cannot show.

Acceptance:
- Fields, labelling rules and the validate, retry once, reject path: `docs/llm-extraction.md`.
- Three models compared on 50 labelled notes (mean field F1 0.82, 0.70, 0.54): `engagement/adr/0002-llm-risk-factor-extraction.md`.
- The metrics are provisional until the labels are reviewed (0 of 50 confirmed). Pending: label
  review by the client's reviewer.

## US-11 Power BI on the same tables

As the **CFO**, I want the Gold tables in a Power BI file, so that finance can use the tool it
already has.

Acceptance:
- A `.pbip` project reads the Gold tables. Pending: Power BI project (SPEC task 3.2, second half;
  `powerbi/README.md` marks the directory).

## US-12 No patient data

As the **compliance lead**, I want to know that no PHI is used and what changes if it is, so that
I can approve a production version.

Acceptance:
- What data is used and what would change with real PHI: `engagement/phi-note.md`.
- Every Silver and Gold table is tagged `contains_phi = false`: `docs/governance.md`, `docs/gold.md`.
