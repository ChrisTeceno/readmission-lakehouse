---
marp: true
theme: default
paginate: true
size: 16:9
title: Contoso Health readmissions readout
style: |
  section { font-size: 24px; }
  h1 { font-size: 40px; }
  table { font-size: 19px; }
  section.small { font-size: 18px; }
  section.small table { font-size: 15px; }
  section.sources { font-size: 11.5px; }
  section.sources h1 { font-size: 28px; margin-bottom: 4px; }
  section.sources li { margin: 0; }
---

# Contoso Health readmissions readout

What drives the HRRP penalty, what next cycle looks like, and what would reduce it

2026-10-21

Contoso Health is a fictional client. Its 12 facilities are real public Ohio CMS Certification
Numbers relabeled with invented names, and its operational data are synthetic. No patient data
(PHI) is used anywhere (`engagement/phi-note.md`).

Every number in this deck is listed with its source on the last slide.

---

# The question

> CMS penalizes us under the Hospital Readmissions Reduction Program. Which of our facilities
> and conditions drive the penalty, what would actually reduce it, and does the penalty itself
> change hospital behavior?

Restated so the data can answer it (`engagement/discovery.md`):

1. Which facilities and conditions are above the cutoff now, and which will be next cycle?
2. Does anything Contoso does lower the excess readmission ratio (ERR)? Candidate: follow-up
   calls after discharge. This needs an experiment, not CMS data.
3. Does being counted as penalized change a hospital's later ERR?

The ERR compares a hospital's readmissions with what CMS expects for its patients: 1.02 is
2 percent more than expected. A condition above the cutoff counts toward the payment cut.

---

# The data

| Source | What it gives |
|---|---|
| CMS HRRP, FY2013 to FY2027 | ERR and discharges per hospital and condition, 266,159 rows |
| CMS Hospital General Information | Attributes of 5,419 hospitals |
| Census ACS 5-year, 2011 to 2024 vintages | County income, poverty, insurance, age, disability |
| Contoso operational database (synthetic) | 12 facilities, 1,850 monthly discharge rows, 3,700 follow-up call rows |
| MTSamples | 2,357 public clinical notes standing in for Contoso's notes |

Two limits shape every answer:

- **The cutoff is exact only for FY2013 to FY2018 and FY2027.** For FY2019 to FY2026 CMS used
  peer group medians no file here carries; 1.0 stands in, and in FY2027, where both are known,
  6.2 percent of rows land on different sides of the two.
- **Dollars are estimates.** CMS publishes each hospital's payment adjustment factor but not the
  base payments it applies to; the base is estimated from CMS's own IPPS impact file
  (`docs/penalty-dollars.md`).

---

<!-- _class: small -->

# What drives Contoso's penalty

FY2026 ERR by facility and condition (bold: above 1.0, the stand-in cutoff for that year)

| Facility | AMI | HF | PN | COPD | Hip/knee | CABG |
|---|---|---|---|---|---|---|
| Alder | 0.961 | 0.952 | 0.897 | **1.008** | **1.074** | **1.043** |
| Birch | **1.131** | **1.077** | **1.090** | 0.996 | **1.161** | |
| Dogwood | **1.184** | **1.021** | **1.065** | 0.993 | **1.190** | 0.970 |
| Hawthorn | **1.068** | **1.044** | **1.025** | 0.999 | **1.630** | **1.061** |
| Oak | | **1.128** | **1.087** | **1.006** | **1.006** | |

The other seven facilities (Cedar, Elm, Fir, Juniper, Linden, Maple, Spruce) have 11 pairs above
1.0 between them, at most three each. 31 Contoso pairs were above 1.0 in FY2026.

- **Hip and knee replacement is the widest driver:** 8 of the 9 Contoso facilities that report it
  are above 1.0. Nationally hip/knee also has the widest spread of any condition.
- **Four facilities carry most of it:** Birch, Dogwood, Hawthorn and Oak are above 1.0 on four or
  more conditions each, 17 of the 31 pairs. Hawthorn's hip/knee ratio, 1.630, is the highest of
  any hospital in the country that year.

---

# Where Contoso stands now (FY2027, exact cutoff)

- **10 of 12 facilities** have at least one condition above the cutoff; **25 of 67** reporting
  facility-condition pairs are above it. Nationally, 90.7 percent of hospitals had at least one
  condition above the cutoff in FY2027.
- **Contoso's mean ERR is 0.999**: 0.0092 below Ohio's mean and 0.0029 below the national mean.
- Contoso's mean was above the national mean in FY2015, FY2016 and FY2023 to FY2026, and above
  Ohio's in FY2023 to FY2026.
- **Against other Ohio hospitals, Contoso's median hospital is 0.0055 lower** (FY2026, adjusted
  p 0.87). With 12 hospitals that is not distinguishable from no difference, which is not the
  same as evidence that Contoso is like its peers.

Reading: Contoso is a typical Ohio network. Its exposure comes from a few facilities and from
hip/knee, not from the whole network running high.

---

<!-- _class: small -->

# What the penalty costs, and which year can still change

The fiscal year names the year the penalty is paid, not the discharges it measures.
Every dollar figure is an estimate: (1 - CMS's payment adjustment factor) x base operating DRG
payments estimated from CMS's IPPS impact file and Table 1B.

| Fiscal year | Paid | Discharges measured | Where it stands | Estimated penalty |
|---|---|---|---|---|
| FY2026 | Oct 2025 to Sep 2026 | 07/2021 to 06/2024 | The bill paid; comparison year | about $2.0M |
| FY2027 | Oct 2026 to Sep 2027 | 07/2023 to 06/2025 | The bill being paid now; published Aug 2026, fixed | about $1.8M |
| FY2028 | Oct 2027 to Sep 2028 | 07/2024 to 06/2026 (assumed) | Mostly baked in; the model's forecast (next slide) | not estimated |
| FY2029 on | Oct 2028 on | 07/2025 on (assumed) | What Contoso can still change | see the pilot economics |

- **What changed, FY2026 to FY2027 (down about $195K):** AMI fell $447K, most of it Dogwood
  ($373K to $13K); Birch hip/knee fell $150K. CABG rose $530K: Alder ($57K to $364K) and Hawthorn
  ($52K to $238K); Oak heart failure and hip/knee rose $160K together.
- A program started in 2027 first moves the penalty around FY2029 to FY2031.

---

# Next cycle: what the model expects for FY2028

The penalty model gives, for every hospital and condition, the probability that the ERR lands
above the cutoff next year. It is a ranking tool; it does not predict the ERR or the dollars.

- **28 Contoso pairs** have a probability of 0.5 or more for FY2028, against 25 above the cutoff
  in FY2027.
- Highest: **Oak hip/knee (0.995), Oak heart failure (0.992), Hawthorn CABG (0.983).**
- What it leans on: last year's ERR dominates, then last year's above-cutoff flag, the mean
  prior ERR, the last ERR change and state. County socioeconomics add little.

Use: start each cycle's review with the pairs at the top of this list, and with Birch, Dogwood,
Hawthorn and Oak, which carry most of the current exposure.

---

# How good the model is, and where it is weak

| Held-out year | Carry last year's flag forward | Logistic regression | LightGBM (chosen) |
|---|---|---|---|
| FY2026 (approximate cutoff) | AUC 0.772 | 0.869 | **0.874** |
| FY2027 (exact cutoff) | AUC 0.633 | 0.675 | **0.678** |

- AUC is the chance the model ranks a pair that went above the cutoff over one that did not;
  0.5 is a coin toss. LightGBM's lead over logistic regression is small; the larger step is from
  the carry-forward rule to either model.
- **FY2027 is the honest test.** Every year from FY2019 to FY2026, training included, uses the
  1.0 stand-in cutoff, so the model learned a rule close to, but not, the one CMS applies.
  FY2028 follows FY2027 and is more likely to look like the 0.678 than the 0.874.
- **Overconfident on FY2027:** pairs given under 0.1 were above the cutoff 22 percent of the
  time, pairs given over 0.9 only 76 percent. Read the probabilities as an order, not as odds.
- A gate stops a new model from replacing the current one if its test AUC is below 0.80 or below
  logistic regression's.

---

# Does the penalty change hospital behavior? No.

Regression discontinuity at the 1.0 cutoff, FY2013 to FY2018 (the exact-cutoff years), outcome
the change in ERR to the next non-overlapping three-year window. Design fixed before outcomes
were examined (`docs/analysis-plan.md`).

| Measure | Result |
|---|---|
| Estimate (jump in ERR change at 1.0) | +0.0004 |
| 95% interval (robust, bias-corrected) | -0.0027 to +0.0047 |
| Smallest effect the data could detect (80% power) | 0.0053 |
| Bandwidth | 0.061 either side of 1.0; 0.5x to 2x all include zero |
| Placebo cutoffs at 0.90, 0.95, 1.05, 1.10 | no jump at any (adjusted p 0.61 to 0.75) |
| Sample | 69,163 hospital-condition-years, 3,268 hospitals |

**A precise null.** Hospitals pushed just over the line and publicly counted as penalized ended
the next window with the same ratio change, within a few tenths of a percent, as hospitals just
under it. Effects larger than 0.02 either way are ruled out; an effect a quarter that size would
have been seen.

---

# What the causal result does not show

- **Hospitals far above the line.** The design compares hospitals near 1.0, where the payment cut
  is small. A response to larger cuts is not ruled out.
- **A retention caveat.** A density test on every row rejects (p 0.009): fewer rows sit just
  above 1.0 than just below. In the analysis sample it does not (p 0.91), but rows just above
  1.0 are 3.5 points more likely to have a later ERR at all. That could bias the estimate by an
  unknown amount; bounds have not been run.
- **The difference-in-differences check disagrees in sign.** It finds a small improvement,
  -0.0030 (-0.0058 to -0.0002), where the RDD finds +0.0004. Intervals overlap, but by the
  pre-registered rule it does not support the RDD. Both are under a third of the 0.02 threshold.

**For Contoso:** the penalty label alone will not lower a facility's ratio. And a penalized
facility's ratio falling next cycle is not proof a program worked: ratios near and above 1.0
drift back toward 1.0 on their own. Programs need a comparison group, which is what the pilot
provides.

---

# What the notes add

Clinical notes hold risk factors CMS data does not: comorbidities, prior admissions, discharge
disposition, social risk, follow-up plans. A model hosted inside the Databricks workspace reads
each note and returns those fields, each with a quote from the note; nothing leaves the
workspace.

| | gpt-oss-120b (chosen) | llama-3.3-70b | llama-3.1-8b | gemma-4-26b (local) |
|---|---|---|---|---|
| Mean field F1 on 50 labelled notes | **0.81** | 0.70 | 0.54 | **0.85** |
| Rejected after one retry | 4 percent | 4 percent | 28 percent | 0 percent |

- **Label review:** the project lead drafted the labels and checked all 50 against the written
  rules; Chris Teceno, a clinician, reviewed all 50 and decided the four judgment calls; one
  label changed and the ranking held. With 4 primary-condition positives, one note moves a
  field's precision or recall by 0.25 or more.
- **Local option:** the fourth column is a model run on a laptop, so no note text leaves the
  machine. On these notes it scored no lower than the hosted model, which makes keeping notes
  inside Contoso's network a hosting question, not an accuracy one. It is not in the pipeline,
  because the workspace's compute cannot reach it.
- First production batch: 100 notes, 14 rejected; the fix is a prompt change, re-evaluated on the
  same 50 notes before use.
- What it enables: patient-level risk factors for targeting follow-up calls. Notes are MTSamples
  stand-ins; Contoso's own notes would be PHI and need the controls in the PHI note.

---

<!-- _class: small -->

# Recommendation: test follow-up calls in a stepped-wedge pilot

A structured nurse call within 72 hours of discharge for HRRP conditions, switched on two
facilities at a time (one larger, one smaller) every three months in an order drawn by lot.

| Item | Design |
|---|---|
| Facilities | 12, each compared with itself before and after its switch |
| Duration | 21 months of enrollment, answer one month later |
| Sample | about 17,885 discharges (synthetic Contoso volumes) |
| Usual-care readmission rate | 16.9 percent |
| Detectable at 80% power | a drop of 3.82 points (to about 13.1 percent) |
| Power for a 2-point drop | 30 percent |

- 3.82 points is a large effect for a phone call. If the calls work as hoped, the pilot sees it;
  if they work modestly, it will say the effect is under about 4 points, not whether it is zero.
- Decisions are fixed now: a clear drop with no rise in ED visits and staff hours within budget
  makes the calls standard; a result that rules out a 2-point drop stops the script.
- Design under review in PR #13 (`docs/pilot-design.md`).

---

<!-- _class: small -->

# Would follow-up calls pay for themselves?

Contoso's own ledger: FY2027 penalty avoided minus the program's cost. Estimates; costs are BLS
wages plus stated time, setup and management assumptions (central $371K a year, range $201K to
$724K).

| Effect on the 30-day rate | Basis | Penalty avoided | Contoso net, central cost |
|---|---|---|---|
| 0 points | Pooled RCTs of calls alone (Boggan 2025) | $0 | -$371K |
| 1.0 point | A judgment | $0.50M to $0.93M | +$132K to +$558K |
| 3.0 points | Multicomponent bundles (Leppin 2014), not a call alone | $1.20M to $1.64M | +$833K to +$1.27M |
| 3.82 points | The pilot's detectable effect; more than the evidence supports | $1.36M to $1.74M | +$987K to +$1.37M |

Ranges are 0.5 to 1.0 of the drop reaching each ERR.

- **Trials find no effect from a call alone. The program pays for itself at Contoso at about half
  a point** (0.36 to 0.71 points at the central cost), which the pilot cannot detect.
- Medicare and insurers would save about $14,000 per avoided readmission ($1.43M a year at 1
  point). That is their money, never added to Contoso's.
- Contoso's upside is capped by the penalty it pays, about $1.75M a year.

---

# Limits, stated plainly

- Contoso is fictional. Its CMS numbers are real public data for relabeled Ohio hospitals; its
  operations (volumes, follow-up calls) are synthetic and cover only FY2026 and FY2027.
- FY2019 to FY2026 cutoffs are approximate (1.0 for a peer group median). The model trained on
  them; the causal analysis uses exact years only.
- The model is a ranking with AUC 0.678 on the exact-cutoff year and is overconfident there.
- The causal null applies to hospitals near the cutoff, and carries the retention caveat.
- Penalty dollars are estimates: the base payments are modelled from CMS's impact file, not
  Contoso's paid claims. Program costs rest on stated assumptions, and the 1-point central
  effect is a judgment.

---

# Next steps and decisions needed

1. **Use the FY2028 ranking** in the quality review: Oak, Hawthorn, Birch and Dogwood first, and
   hip/knee across the network. The dashboard and Genie space answer the five agreed questions.
2. **Approve the pilot**: confirm staged rollout is possible, which facilities cannot be
   randomized, and the 2-point smallest reduction worth the program (finance).
3. **Peer group medians**: point us to FY2019 to FY2026 medians if Contoso has them; without
   them those years' labels stay approximate.
4. **Azure:** the Key Vault and Data Factory run is done
   ([evidence](../docs/evidence/azure.md)); the resources are torn down after the readout.
5. **Ask the DBA** whether the operational database sets `updated_at` on update; the incremental
   load depends on it.

---

<!-- _class: sources -->

# Sources

All files are on `main` unless marked; Gold tables and notebook runs are from 2026-10-21, the penalty dollar tables from 2026-10-26.

- `engagement/discovery.md` (Data available): 266,159 HRRP rows; 5,419 hospitals; ACS 2011 to 2024 vintages; 12 facilities, 1,850 discharge rows, 3,700 call rows; 2,357 notes.
- `docs/gold.md` (Penalty cutoff): exact years FY2013 to FY2018 and FY2027; 6.2 percent (899 of 14,575) of FY2027 rows on different sides of 1.0 and the peer median.
- `docs/eda/01_eda.html` (notebook export, section 5, Contoso condition table, CROSS_SECTION_YEAR 2026): every FY2026 facility ERR on slide 4, and counted from that table the 31 pairs above 1.0, 17 of them at Birch, Dogwood, Hawthorn and Oak, 11 at the other seven; Hawthorn hip/knee 1.630 (also the FY2026 hip/knee maximum in section 2); D11 difference in medians -0.0055, BH p 0.868. Summarised in `docs/eda/what-the-data-can-answer.md`: hip/knee 8 of 9, widest spread (3.4 times COPD's), 90.7 percent penalized in FY2027.
- `docs/genie.md` (Q1 and reworded check): FY2027 10 facilities, 25 of 67 pairs, mean ERR 0.999; FY2026 31 pairs. (Q2): FY2027 mean 0.9992, 0.0092 below Ohio, 0.0029 below national; years above national and Ohio. (Q3): 28 pairs at 0.5 or more, Oak (CH11) hip/knee 0.995 and HF 0.992, Hawthorn (CH07) CABG 0.983, model version 4. Facility ids to names: `src/readmission/ingest/contoso.py`.
- `docs/model-card.md` (Metrics, Calibration, What the model uses, Metric gate; MLflow experiment `/Shared/readmission-penalty`, run `17d90d5cf89543f7b0e286cfb930ff4d`, job run 303222482315557, model version 4): AUC 0.772, 0.869, 0.874 (FY2026) and 0.633, 0.675, 0.678 (FY2027); 22 and 76 percent calibration; 0.80 gate.
- `docs/causal-findings.md` (The answer; Validity checks; Sensitivity; Difference-in-differences; Limitations), from `docs/causal/02_causal.html`: +0.0004, -0.0027 to +0.0047, MDE 0.0053, bandwidth 0.061, 0.5x to 2x, threshold 0.02, 1.02 read as 2 percent more readmissions than expected, placebo cutoffs 0.90, 0.95, 1.05 and 1.10 with BH p 0.61 to 0.75, 69,163 rows, 3,268 hospitals, density p 0.009 and 0.91, retention 3.5 points, DiD -0.0030 (-0.0058 to -0.0002).
- `engagement/adr/0002-llm-risk-factor-extraction.md` (Results; First job run; MLflow runs rescored on the reviewed labels `e18026f5a77b444db0ecc9fb8163c8bf`, `321fafc2509d4fc09edc42476355f193`, `0fe0fa0c99ce40e4a4232a139c9ecd70`): F1 0.81, 0.70, 0.54; reject rates 4, 4, 28 percent; 4 primary-condition positives, 0.25 swing; 100 notes, 14 rejected. `docs/llm-extraction.md`: all 50 labels drafted and checked by the lead, reviewed by Chris Teceno, a clinician, who decided the four judgment calls; one label changed.
- `docs/pilot-design.md` (on branch `pf/pilot-experiment-design-post-discharge-follow-up`, PR #13, open; Summary and For the COO; `analysis/03_power.py`, seed 20261021): 72-hour call, 12 facilities, 6 steps of 2 every three months, 21 months plus one month, about 17,885 (17,900) discharges, 16.9 percent usual care, MDE 3.82 points to about 13.1 percent, 30 percent power at 2 points, smaller than about 4 points, 2-point decision threshold. The MDE of 3.82 is also on `main` in `engagement/status-update.md`.
- `docs/penalty-dollars.md` (Headline; Timeline; What changed; Economics; Break-even), from `gold_penalty_dollars`, `gold_penalty_by_condition`, `gold_penalty_sensitivity` and `gold_nurse_call_economics`: $1,959,057 (FY2026) and $1,764,148 (FY2027); FY2027 windows and publication; condition and facility changes; costs $200,739, $370,724, $724,342; penalty avoided $503,059 and $928,851 at 1 point, $1,203,846 and $1,642,249 at 3, $1,357,498 and $1,740,081 at 3.82; break-even 0.36 and 0.71; payer savings $1,428,000 at 1 point; $14,000 per readmission (Medicare $13,986 per Contoso discharge, DY2024); Boggan 2025, Leppin 2014.
- `docs/synthetic-data.md` (How it is generated; Realism check; Finding): how Contoso's synthetic volumes and calls were made, seed 20261021; all-payer factor 2.0 against a public estimate of 3.21, so synthetic volumes run about 38 percent low (pilot MDE 3.45 points at 3.21 instead of 3.82).
- `engagement/status-update.md` (Next; Decisions needed): pending Azure run and screenshots; the four decisions.
- `engagement/executive-summary.md` rounds the figures above: about 9 in 10 hospitals (90.7 percent); over half (17 of 31 pairs); 87 and 68 times in 100 (AUC 0.874 and 0.678); a quarter of the size that would matter (0.0053 against 0.02); about 18,000 discharges (17,885); about 17 to 13 percent (16.9 percent less 3.82 points).
