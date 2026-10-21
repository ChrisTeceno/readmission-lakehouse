# Architecture decision records

Decisions taken in the Contoso Health engagement (a fictional client), one page each: Context,
Decision, Consequences. An accepted ADR is changed only by a new ADR that supersedes it.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-contoso-lakebase-connector.md) | Load Contoso's Lakebase database over JDBC, incremental on `updated_at`, with OAuth tokens and no stored password | accepted 2026-10-21 |
| [0002](0002-llm-risk-factor-extraction.md) | Extract note risk factors with a Databricks-hosted model (`gpt-oss-120b`) through `ai_query`, chosen by mean field F1 on 50 notes; metrics provisional until labels are reviewed | accepted 2026-10-21 |
| [0003](0003-medallion-on-free-edition.md) | Medallion on Free Edition: Bronze append-only, Silver and Gold rebuilt each run, one serverless job | accepted 2026-10-21 |
| [0004](0004-fetch-load-split-with-landing-volume.md) | Fetch and load as separate steps around a landing volume with per-batch manifests | accepted 2026-10-21 |
| [0005](0005-rdd-on-non-overlapping-windows.md) | Regression discontinuity at the cutoff on non-overlapping windows, exact-cutoff years only, DiD as a check | accepted 2026-10-21 |
| [0006](0006-approximate-cutoff-stand-in.md) | Use 1.0 as the cutoff for FY2019 to FY2026 and flag those rows `cutoff_is_approximate` | accepted 2026-10-21 |
| [0007](0007-penalty-model-time-split-and-gate.md) | Penalty model trained on past years, champion by validation PR-AUC, test AUC gate | accepted 2026-10-21 |

## Where decisions without an ADR live

- The five dashboard questions and the one-dashboard, no-dollar-amounts choices:
  `engagement/wireframe.md`, section "Decisions taken in the design review".
- Pre-registration of the analysis: `docs/analysis-plan.md`.

## Pending

The pilot design (PR #13, open) will record its own choices (stepped wedge, facility as the unit
of randomization) in `docs/pilot-design.md`; an ADR is added when it merges if the client
confirms the design.

## Format

Each ADR starts with a `Status` line and a date, then Context (including the options considered),
Decision, Why where the reasoning is not obvious, and Consequences. Numbers are quoted from the
document they come from, which is linked in the text.
