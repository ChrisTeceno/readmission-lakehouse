# ADR 0008: Use CMS's published peer group median as the cutoff for FY2019 to FY2026

Status: accepted (2026-10-28). Supersedes ADR 0006.

## Context

ADR 0006 used 1.0 as the penalty cutoff for FY2019 to FY2026 because the peer group medians CMS
applied in those years were thought to be unavailable: only the FY2027 IPPS supplemental file
was loaded, and the Provider Data Catalog archive carries no peer group or median. Research for
a later run found that CMS publishes the HRRP supplemental data file for every fiscal year from
FY2019, each with the hospital's peer group and the median ERR of that peer group for each
condition (`docs/data-sources.md`).

Options considered:

1. **Keep the 1.0 stand-in** and the flag on every FY2019 to FY2026 row.
2. **Load every year's supplemental file** and use its published median as the cutoff.
3. **Load the files only for the dashboards** and leave the model and the causal analysis on
   1.0.

## Decision

Option 2. The fetch step lands the FY2019 to FY2025 supplemental files next to the FY2026 and
FY2027 ones, taking the Section 508 text copy inside each zip (tab-separated, CSV for FY2022
and FY2025) so the fetch needs no spreadsheet reader. Columns are matched by normalized name and
a file missing a required column is refused. `silver_hrrp_payment_measure` is the only parser of
the per-condition values; `silver_hrrp_history` keeps the PDC rows and joins `peer_group` and
`peer_median_err` from the payment tables. Gold sets `penalty_cutoff` to 1.0 for FY2013 to
FY2018 and to the published median from FY2019.

`cutoff_is_approximate` stays, and is now true only for rows CMS published no median for:

- **FY2023 pneumonia.** CMS left pneumonia out of the FY2023 payment determination (COVID), so
  the FY2023 file has no pneumonia median while the PDC still publishes the ERR: 3,785 rows,
  2,951 with an ERR, all 12 Contoso facilities among them.
- **Hospitals missing from a year's file.** 116 to 134 CCNs a year are in the PDC but not in that
  year's supplemental file, about 200 rows with an ERR a year.

These rows keep the 1.0 stand-in.

## Why

- **The published values reproduce CMS's payment.** Recomputing the FY2026 payment adjustment
  factor from the loaded terms matches the published factor to 4 decimals for 2,870 of 2,945
  hospitals (97.5 percent) and within 0.0001 for all of them; every year from FY2019 shows the
  same pattern. The FY2027 cutoffs are unchanged on all 17,472 rows.
- **The stand-in was wrong for 3.5 to 5.1 percent of rows a year.** Rows with an ERR on different
  sides of 1.0 and of their peer median: FY2019 708 (5.0 percent), FY2020 705 (5.1), FY2021 643
  (4.7), FY2022 614 (4.7), FY2023 348 (3.5), FY2024 492 (4.1), FY2025 504 (4.3), FY2026 461
  (4.0), FY2027 899 (6.2).
- **One rule everywhere.** Option 3 would leave the model and the causal analysis measuring a
  rule the dashboards no longer show.

## Consequences

- **Labels change.** The model is retrained on the published cutoffs. Version 7 (LightGBM) is
  the champion: test AUC 0.868 on FY2026 and 0.679 on FY2027, against 0.874 and 0.678 for
  version 6, whose FY2026 figure was measured against the 1.0 label. FY2026 is now an exact-cutoff
  test year, and the gap between FY2026 and FY2027 remains (`docs/model-card.md`).
- **The causal primary result is unchanged.** ADR 0005's pre-registered sample is FY2013 to
  FY2018, whose cutoff was always exact, and it is still the primary result. A secondary sample
  of every year with a published cutoff and an outcome (FY2013 to FY2023 and FY2025) was added
  after the pre-registration, once the medians were available: +0.0002 (95 percent interval
  -0.0023 to +0.0032, MDE 0.0039), a null like the primary (`docs/causal-findings.md`). It
  replaces the stand-in sensitivity runs.
- **Captions narrow.** Dashboards and Genie no longer call FY2019 to FY2026 approximate; they
  name the two kinds of rows that still use 1.0 (`engagement/wireframe.md`, `docs/genie.md`).
- **Contoso's above-cutoff counts move by 0 to 3 pairs a year** for FY2019 to FY2026, upward in
  every year that moved (`docs/gold.md`).
- **The job depends on seven more CMS files.** A file whose layout changes fails the fetch
  instead of loading a cutoff of 1.0.
