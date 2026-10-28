# Discovery notes: Contoso Health

Date: 2026-10-21

Contoso Health is a fictional regional hospital network. Its 12 facilities are real public CMS
Certification Numbers from Ohio relabeled with invented names (see the README), so every Contoso
number in these notes is public CMS data or synthetic operational data, never a real hospital's
own. Stakeholders are described by role only.

## The client's question

> CMS penalizes us under the Hospital Readmissions Reduction Program. Which of our facilities
> and conditions drive the penalty, what would actually reduce it, and does the penalty itself
> change hospital behavior?

Restated so the data can answer it (`docs/analysis-plan.md`, section 1):

1. Which Contoso facilities and conditions are above the penalty cutoff, and which will be next
   cycle?
2. Does anything Contoso does (post-discharge follow-up calls are the candidate) lower the
   excess readmission ratio (ERR)? This needs an experiment, not CMS data; a pilot is designed
   for it (pending, PR #13).
3. Does being counted as penalized change a hospital's later ERR?

## Stakeholders

| Role | What they need from the engagement |
|---|---|
| CFO | The size and drivers of the penalty exposure; whether a program is worth funding. |
| CMO | Which conditions and sites to put clinical attention on; what a follow-up program would ask of clinicians. |
| Quality director | A scorecard they can trust each quarter, with the caveats stated; the five questions in `engagement/wireframe.md`. |
| Analytics lead | A pipeline they can run and monitor after the engagement ends; documented tables. |
| Compliance lead | Confirmation that no PHI is used and what a production version would need (`engagement/phi-note.md`). |

## Current state

- Contoso tracks its HRRP status from the annual CMS release. It has no model of next year's
  status and no comparison of its ratios with Ohio or national hospitals.
- Facility, discharge and follow-up call data sit in an operational Postgres, not in an analytics
  store. Follow-up call records start in September 2023.
- Clinical notes hold risk factors (comorbidities, prior admissions, discharge disposition,
  social risk, follow-up plans) that no report reads.

## Data available

| Source | What it gives | Where documented |
|---|---|---|
| CMS HRRP, FY2013 to FY2027 | ERR, discharges, per facility and condition; 266,159 rows in `silver_hrrp_history` | `docs/data-sources.md` |
| CMS Hospital General Information, Unplanned Hospital Visits | Hospital attributes (5,419 hospitals); 67,060 visit-measure rows | `docs/data-sources.md` |
| Census ACS 5-year county measures, 2011 to 2024 vintages | Income, poverty, uninsured, age 65+, disability by county | `docs/data-sources.md` |
| Contoso operational database (synthetic) | 12 facilities, 1,850 monthly discharge rows, 3,700 follow-up call rows | `docs/lakebase.md` |
| MTSamples transcriptions | 2,357 public clinical notes standing in for Contoso's notes | `docs/llm-extraction.md` |

## Constraints

- **Platform.** Databricks Free Edition: serverless compute only, one SQL warehouse, one Lakebase
  project (`docs/governance.md`, `docs/lakebase.md`). Decisions that follow from it are in
  ADR 0003 and ADR 0004.
- **No PHI.** Nothing in the engagement is patient data (`engagement/phi-note.md`).
- **Cutoff is not fully observable.** From FY2019 CMS compared each hospital with its
  dual-eligibility peer group median, which no file before FY2027 carries. Gold uses 1.0 as a
  stand-in and flags the rows (ADR 0006). Superseded 2026-10-28: the FY2019 to FY2025 files
  were found and loaded, and 1.0 stands in only where CMS published no median (ADR 0008).
- **No dollar amounts.** The payment adjustment factor and base payments are not loaded, so the
  work ranks where to look and does not size the penalty (`engagement/wireframe.md`).
- **Operational history is short.** Only FY2026 and FY2027 windows have Contoso operational
  data (`docs/gold.md`).
- **Azure.** Key Vault and Data Factory are created only after the client approves the spend and
  are torn down after evidence is captured (SPEC.md, Boundaries).

## Success measures

Agreed for the engagement, each checkable on the repository:

1. The pipeline runs Bronze to Gold from a clean schema in under 15 minutes (the clean run on
   2026-10-21 took 12 minutes 58 seconds, `docs/data-sources.md`).
2. The dashboard and Genie space answer the five questions in `engagement/wireframe.md`
   (`docs/genie.md`).
3. The penalty model beats the carry-forward rule on held-out years, with the weak year stated
   (`docs/model-card.md`).
4. The causal question is answered with hypotheses fixed beforehand, including a null result if
   that is what the data says (`docs/causal-findings.md`).
5. A follow-up pilot is specified with sample size, power and the analysis committed to before
   launch (pending, PR #13).

## Risks

| Risk | Effect | Handling |
|---|---|---|
| The 1.0 stand-in cutoff misclassifies rows for FY2019 to FY2026 | Above-cutoff counts in those years are approximate; 6.2 percent of FY2027 rows land on different sides of the two cutoffs | `cutoff_is_approximate` column and captions; the causal analysis uses exact years only |
| The model is weaker on the exact-cutoff year | AUC 0.874 on FY2026, 0.678 on FY2027 | Probabilities, not yes or no; caption on the predictions page (`docs/model-card.md`) |
| Contoso's source system may not set `updated_at` on update | Changed rows would be missed by the incremental load | Question for the client's DBA; ADR 0001 |
| LLM extraction labels are unreviewed | Reported metrics are provisional: 0 of 50 labels confirmed | Review pending; metrics tagged `labels_status=draft` (ADR 0002) |
| Free Edition limits | No account-level analyst group, so the grant is skipped | `docs/governance.md` |

## Open questions for the client

Listed with the decisions needed in `engagement/status-update.md`.
