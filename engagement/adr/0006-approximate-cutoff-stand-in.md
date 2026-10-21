# ADR 0006: Use 1.0 as the penalty cutoff for FY2019 to FY2026 and flag every such row

Status: accepted (2026-10-21)

## Context

A condition counts toward a hospital's HRRP payment reduction when its ERR is above the cutoff.
The cutoff was 1.0 for FY2013 to FY2018. From FY2019 CMS compared each hospital with the median
ERR of its dual-eligibility peer group. The peer group assignments and medians are published in
the IPPS supplemental file, and only the FY2027 file loaded here carries them (`docs/gold.md`,
`docs/data-sources.md`). The Provider Data Catalog archive for FY2019 to FY2026 has no peer group
or peer median.

Options considered:

1. **Drop FY2019 to FY2026** from every analysis.
2. **Use 1.0 for those years** and mark the rows as approximate.
3. **Reconstruct peer medians** from hospital dual-eligibility proportions.

## Decision

Option 2. Gold sets `penalty_cutoff` to 1.0 for FY2013 to FY2026 and to `peer_median_err` for
FY2027, and carries `cutoff_is_approximate`: false for FY2013 to FY2018, true for FY2019 to
FY2026, false for FY2027 (true for an FY2027 row with no ERR, where CMS published no median).

## Why

- **The error is measurable and small.** The FY2027 peer medians run from 0.9837 to 1.0143
  (median 0.9955). In FY2027, where both cutoffs are known, 899 of 14,575 rows with an ERR
  (6.2 percent) fall on different sides of 1.0 and of their peer median (`docs/gold.md`).
- **Dropping the years would remove most of the history** the model needs: FY2019 to FY2026 is
  eight of the fourteen training and evaluation years.
- **Reconstruction needs data that is not in the project.** It would also replace a documented
  approximation with an undocumented one.

## Consequences

- **Above-cutoff counts for FY2019 to FY2026 carry a caption** on every dashboard page that
  shows them, and Genie is instructed to include the flag and say the cutoffs are approximate
  (`engagement/wireframe.md`, `docs/genie.md`).
- **The causal analysis uses exact years only** as its primary sample (ADR 0005).
- **The model learns "ERR above 1.0"**, which is close to but not the rule CMS applied. Test AUC
  is 0.874 on FY2026 (approximate label) and 0.678 on FY2027 (exact cutoff), every candidate
  including the carry-forward rule loses 0.1 to 0.2 AUC on FY2027, and FY2028 is likelier to
  look like FY2027 (`docs/model-card.md`).
- **Revisit when peer medians are available** for earlier years: relabel, rerun the evaluation
  and retire the flag. The client can speed this up by confirming a source (see
  `engagement/status-update.md`).
