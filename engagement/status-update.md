# Status update: Contoso Health readmissions engagement

Date: 2026-10-21. Contoso Health is a fictional client.

## Summary

The data platform, penalty model, causal analysis, notes extraction and dashboard are built and
on main. Three things are open: the Azure evidence run, workspace screenshots of the dashboard,
and a clinical review of the extraction labels. The causal result is a null: crossing the cutoff does not
change a hospital's later ERR.

## Done

Merged to main (PR number, what it delivered):

| PR | Delivered |
|---|---|
| #1, #2 | Project scaffold with CI; CMS Provider Data client with retries and recorded fixtures |
| #3, #4 | Historical HRRP FY2013 to FY2027 (266,159 rows in Silver); Bronze ingestion and the pipeline job |
| #5 | Contoso Lakebase: synthetic seed and incremental load (ADR 0001) |
| #6, #7 | Silver layer with constraints, quarantine and governance; Census ACS county measures |
| #8 | Gold layer: scorecard, penalty mart, features, anomaly flags, run monitoring, daily schedule |
| #9 | Analysis plan written before outcomes were examined; EDA with twelve hypotheses |
| #10 | Penalty model with MLflow and champion alias; test AUC 0.874 on FY2026, 0.678 on FY2027 |
| #11 | Causal analysis: estimate +0.0004, interval -0.0027 to +0.0047, a null (`docs/causal-findings.md`) |
| #12 | Notes extraction with three models compared on 50 notes (ADR 0002) |
| #14 | Dashboard wireframe agreed first, then the dashboard, pipeline-health page and Genie space |

A clean run from empty tables takes 12 minutes 58 seconds against a 15-minute target.

## Next

- **Azure evidence run, in progress.** Key Vault holding the Census key and Data Factory
  triggering the job, with evidence captured and the resources torn down afterwards.
- **Dashboard screenshots, pending.** The pages exist and are tested; the images in
  `docs/evidence/` are local renders, and workspace screenshots need a signed-in session
  (`docs/genie.md`).
- **Label review, done by the lead.** The project lead checked all 50 extraction labels against
  the written rules and Chris decided the four judgment calls; one label changed and the model
  ranking held. No clinician has reviewed them.
- **Pilot design, in review (PR #13).** Stepped wedge across Contoso facilities; minimum
  detectable effect 3.82 percentage points at 80 percent power.
- Power BI project, engagement readout and executive summary follow.

## Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The model is weaker where the cutoff is exact | AUC 0.678 on FY2027, and FY2028 is likelier to look like FY2027 | Probabilities with a caption; ADR 0007 |
| FY2019 to FY2026 cutoffs are approximate | 6.2 percent of FY2027 rows land on a different side of the two cutoffs | Rows flagged; causal analysis uses exact years (ADR 0006) |
| The causal estimate has a retention caveat | Rows just above the cutoff are 3.5 points more likely to have a later ERR | Reported; bounds not yet run |
| The pilot's detectable effect is large | 3.82 points is about 23 percent relative; smaller effects would not be seen | Design is open for discussion (PR #13) |
| The extraction labels have no clinical review | One note moves some fields' precision or recall by 0.25 or more | Lead and Chris checked them; a clinician review is open |

## Decisions needed from Contoso

1. **Label review.** Name a clinical reviewer for the 50 extraction labels, or accept that the
   project lead and Chris checked them.
2. **Peer group medians.** Confirm whether Contoso can supply or point us to FY2019 to FY2026
   peer medians; without them those years stay approximate.
3. **Pilot.** Confirm that the follow-up program can be rolled out to facilities in stages, and
   which facilities cannot be randomized and why.
4. **Azure.** Approve the short-lived Key Vault and Data Factory resources, which carry a small
   cost.
5. **Source system.** Ask your DBA whether the operational database sets `updated_at` on update,
   not only on insert; the incremental load depends on it (ADR 0001).
