# Pilot design: 72-hour post-discharge follow-up calls at Contoso

Written 2026-10-21, before any pilot data exist (SPEC task 2.6, success criterion 12). Every
number below comes from `analysis/03_power.py` (fixed seed 20261021), which runs on Databricks
against the tables and locally from the committed extract in `analysis/data/`; both runs gave
identical output on 2026-10-21. The simulator is `src/readmission/analysis/power.py`, tested in
`tests/test_power.py`. A change to the design, the outcome or the analysis after randomization is
recorded under Deviations at the end of this file, with the date and the reason.

## For the COO

We propose testing a structured nurse phone call within 72 hours of discharge for heart attack,
heart failure, pneumonia, COPD, hip and knee replacement and bypass patients, by switching it on
two facilities at a time, one larger and one smaller, every three months, in an order drawn by
lot, until all 12 have it after 18 months; enrollment runs 21 months and the answer comes one
month later. Comparing each facility with itself before and after its switch, across about
17,900 discharges, the pilot has an 80 percent chance of detecting a drop in the 30-day
readmission rate from about 16.9 to 13.1 percent (3.8 points), and a 30 percent chance of
detecting a 2-point drop. That is a large effect for a phone call, so read the pilot this way:
if the calls work as well as the program's advocates hope, we will see it; if they work modestly,
the pilot will tell us the effect is smaller than about 4 points but not whether it is zero. We
commit now to the analysis and to the decision each result leads to: a clear reduction with no
rise in ED visits and staff hours within budget means the calls become standard everywhere; a
result that rules out a 2-point reduction means we stop the structured script and give the nurse
hours back; anything in between means we keep the calls only if they stay within budget, and we
do not count on them in HRRP planning.

## 1. Summary

| Item | Value |
|---|---|
| Intervention | structured follow-up call within 72 hours of discharge, HRRP conditions |
| Design | stepped wedge, 12 facilities, 6 steps of 2, 7 periods of 3 months |
| Randomization | order of the steps, stratified by size: each step takes one of the six larger and one of the six smaller facilities |
| Enrollment | 21 months, plus 1 month for the last discharges' 30-day outcome |
| Sample size | 12 facilities, 84 facility-periods, about 17,885 index discharges, half of the facility-periods on the intervention |
| Primary outcome | 30-day all-cause readmission rate after an HRRP-condition discharge, all payers |
| Primary analysis | discharge-weighted regression of facility-period readmission rates on the intervention with period and facility effects; two-sided permutation test over the stratified randomization, alpha 0.05 |
| Usual-care rate | 16.9 percent (CMS FY2026 observed rates for Contoso's CCNs, weighted by Contoso's mix) |
| Power | 80 percent at an absolute reduction of 3.82 points (23 percent relative); 30 percent at 2 points; 60 percent at 3 points |
| Type I error, simulated | 0.054 (2,000 trials, Monte Carlo standard error 0.005) |
| Planning ICC and CAC | 0.01 and 0.7 (section 6) |
| MDE across ICC 0.005 to 0.05 and CAC 0.5 to 0.9 | 2.98 to 8.08 points |

## 2. Inputs, and what is synthetic

- **Contoso's operations are synthetic.** Contoso Health is fictional. Its 12 facilities are real
  Ohio hospitals' CCNs relabeled CH01 to CH12, and its discharges and follow-up calls are seeded
  by `scripts/seed_contoso.py` (`docs/lakebase.md`). The pilot takes from them only the volumes
  and today's call rates, from the latest 12 months of `silver_contoso_discharges` and
  `silver_contoso_followup_calls` (October 2025 to September 2026): 852 HRRP-condition discharges
  a month across the network, from 11 a month at CH12 to 152 at CH03; 42.7 percent of discharges
  get a completed call within 72 hours today, 31.9 to 57.4 percent by facility.
  `tests/test_power.py` regenerates those 12 months from the seed and checks the extract against
  them.
- **Readmission rates are public CMS data.** Each facility's usual-care rate is its FY2026 CMS
  observed rate per condition (readmissions over discharges, Medicare fee-for-service, 07/2021 to
  06/2024, from `gold_facility_condition_year`), weighted by Contoso's condition mix: 15.2 to
  22.7 percent by facility, 16.9 percent for the network. The pilot's outcome is all-payer, and
  all-payer rates usually run a little below Medicare's; using Medicare's makes the binomial
  variance, and so the MDE, slightly larger.
- **Between-hospital variation** comes from all Ohio hospitals in the same table, FY2023 and
  FY2026 (section 6).

## 3. Design

### 3.1 Unit of randomization: the facility

Calls are made by facility staff who also do discharge teaching, and a structured script changes
how a unit talks to every patient, so patient-level randomization inside a facility would leak:
nurses who learn the script use it on control patients too, and a control patient who calls the
unit back reaches the same nurses. Randomizing facilities avoids that contamination. It costs
power: patient-level randomization of the same 17,885 discharges would detect 1.57 points (normal
approximation for two proportions, notebook section 7) against 3.82 for the chosen design. If
Contoso can run calls from one central team that never touches discharge, patient-level
randomization becomes worth reconsidering, and this document would be revised before launch.

### 3.2 Stepped wedge rather than a parallel cluster trial

| Consideration | Parallel (6 vs 6) | Stepped wedge |
|---|---|---|
| Contamination | none between facilities | none between facilities |
| Rollout | six sites trained at once | two sites a quarter; the call team and the script mature on early sites |
| Withholding | six facilities never get the program in the trial | every facility gets it; the order is the only thing randomized |
| Comparison | facilities against other facilities | each facility against itself, period effects removing secular trend and season |
| MDE, 21 months (planning ICC and CAC) | not reached below 10 points, with or without a baseline period | 3.82 points |

With 12 facilities a parallel trial is not viable. Facilities differ in readmission rate (15.2 to
22.7 percent at baseline) by more than the effects of interest, and with six per arm those
differences do not average out. Randomized within six size-matched pairs it also has only 64
possible allocations, and an allocation and its mirror image give estimates of the same size, so
the smallest attainable two-sided permutation p-value is 1/32; it can only reject at 0.05 when
the observed allocation is the most extreme of all 64. The stepped wedge compares each facility
with itself, so stable differences between facilities drop out and only their quarter-to-quarter
movement (ICC times 1 - CAC, section 6) adds noise.

The stepped wedge's own risks, and how the design handles them:

- **Secular trend and season.** Intervention time is concentrated late in the trial, so a
  network-wide trend would be confounded with the intervention without period effects. The
  analysis fits one effect per 3-month period (section 7).
- **Delay.** The last two facilities start 18 months in. That is acceptable for a program whose
  benefit is unproven.
- **Learning curve.** Early periods on the intervention may underperform. The primary analysis
  counts them (intent to treat); a sensitivity excludes each facility's first month.

### 3.3 Number of steps and duration

MDE at 80 percent power, planning ICC 0.01 and CAC 0.7 (notebook section 4):

| Design | 14 months | 21 months | 28 months |
|---|---|---|---|
| Stepped wedge, 4 steps of 3 | 4.75 | 4.36 | 4.13 |
| Stepped wedge, 6 steps of 2 (chosen) | 4.26 | 3.82 | 3.55 |
| Stepped wedge, 12 steps of 1 | 3.81 | 3.38 | 3.04 |
| Parallel with a baseline period | not reached | not reached | 9.51 |
| Parallel, no baseline | not reached | not reached | not reached |

(Percentage points; "not reached" means no effect up to 10 points has 80 percent power.)

Six steps at 3-month periods is the choice. Twelve single-facility steps would lower the MDE by
about 12 percent, but with one facility per step size cannot be balanced across the order by
stratification, and a new site every 7 weeks leaves the call team no time to settle each one.
Lengthening enrollment helps slowly, because the quarter-to-quarter movement of a facility's rate
does not shrink with more discharges: 28 months buys 0.27 points over 21 months. Fourteen months
costs 0.44.

### 3.4 Randomization

Sort the 12 facilities by monthly volume. The six larger and the six smaller each draw a random
order 1 to 6; step k switches on the k-th facility of each half at the start of period k + 1.
All 6! x 6! = 518,400 orders are equally likely. The draw is done once, with a recorded seed, by
someone outside the operations team, and facilities learn their start date 6 weeks ahead for
training.

## 4. Intervention and control

- **Intervention.** A nurse calls each patient discharged with a principal diagnosis in an HRRP
  condition (AMI, HF, pneumonia, COPD, elective hip or knee replacement, CABG) within 72 hours,
  using a structured script: symptoms and warning signs, medication reconciliation against the
  discharge list, follow-up appointment confirmed or booked, and a direct number to call back.
  Three attempts before the 72 hours are up.
- **Control.** Usual care: today's unstructured calls, which reach 42.7 percent of discharges
  within 72 hours (synthetic).
- **What the contrast is.** Control facilities already call, so the effect estimated is that of
  the structured program over usual calls, at the facility level, for all discharges whether or
  not the call connects. If the program lifts completion within 72 hours to 80 percent, the
  facility-level MDE of 3.82 points corresponds to about 10 points for each additional patient
  reached (3.82 / (0.80 - 0.427)), which is why the MDE should be read as a large effect.

## 5. Outcomes

**Primary.** 30-day all-cause unplanned readmission to any Contoso facility after an index
discharge for an HRRP condition, all payers, per facility and 3-month period, assigned to the
period of the index discharge.

**Secondary.** The same in the Medicare fee-for-service subgroup (the HRRP population); per
condition, descriptive; days alive and out of hospital at 30 days.

**Guardrails** (monitored monthly, reported with the primary):

| Guardrail | Measure | Breach |
|---|---|---|
| ED visits | 30-day treat-and-release ED visits per index discharge | a rise on the intervention, primary model, two-sided p at or below 0.05 |
| Call completion (fidelity) | completed within 72 hours over discharges, facilities on the intervention | under 60 percent for two consecutive months at a facility |
| Staff hours | nurse hours per completed call and per discharge | over budget by 25 percent for a quarter |

ED visits, staff hours and patient-level readmission flags are not in Contoso's Lakebase today
(`silver_contoso_*` holds monthly counts of discharges and calls only); the pilot needs them
added to the operational database before period 1 starts.

## 6. Power

### 6.1 Method

Simulation, because 12 clusters with unequal sizes and a permutation test have no closed form.
Each simulated trial (`readmission.analysis.power.simulate_power`):

1. Draws a stratified allocation of the 12 facilities to the 6 steps.
2. Gives each facility its usual-care rate (section 2) plus a stable facility effect with variance
   ICC x CAC x p(1 - p) and a facility-period effect with variance ICC x (1 - CAC) x p(1 - p),
   on the probability scale (the Hooper, Teerenstra, de Hoop and Eldridge 2016 model), then
   subtracts the effect in intervention cells.
3. Draws readmissions per facility-period as binomial counts on the expected discharges (monthly
   volume x 3).
4. Runs the primary analysis of section 7 and its permutation test against 999 re-randomizations
   drawn from the same stratified scheme.

Power is the share of 2,000 trials with p at or below 0.05, at absolute reductions from 0 to 10
points in steps of 0.25; the MDE interpolates where power crosses 80 percent. Every effect size
reuses the same allocations and facility effects (common random numbers), so the curve is smooth.

The simulator is tested against a case with a known answer: a parallel trial of 80 equal
clusters, where the difference in cluster means is normal with known variance and power is
0.70 analytically; the simulation agrees within 0.04 and is unbiased for the effect. The tests
also check the permutation test's size in a 12-facility stepped wedge, the regression against an
explicit dummy-variable fit, and the ICC and window-correlation estimators on simulated data.

### 6.2 ICC

The ICC is the between-facility variance of the true 30-day readmission rate over p(1 - p). It is
estimated among Ohio hospitals per condition with the one-way ANOVA estimator on CMS readmission
counts, with a bootstrap interval over hospitals (notebook section 3). FY2026:

| Condition | Hospitals | ICC | 95 percent bootstrap interval |
|---|---|---|---|
| AMI | 60 | 0.0025 | 0.0001 to 0.0048 |
| HF | 102 | 0.0031 | 0.0014 to 0.0051 |
| PN | 97 | 0.0017 | 0.0004 to 0.0030 |
| COPD | 78 | 0.0006 | -0.0022 to 0.0035 |
| HIP_KNEE | 5 | 0.0037 | -0.0025 to 0.0071 |
| CABG | 16 | 0.0133 | 0.0013 to 0.0216 |

Weighted by Contoso's condition mix the ICC is 0.0027. The highest bootstrap upper bound across
conditions and both years is 0.0216, for CABG in FY2026 with 16 hospitals; every other condition's
is under 0.0075 in both years. CMS suppresses counts
under 11, so small hospitals drop out, and the windows are three-year Medicare averages, which
vary less than a quarter's all-payer rate at one facility. The planning ICC is therefore 0.01,
almost four times the estimate, and the grid runs from 0.005 to 0.05.

### 6.3 CAC

The cluster autocorrelation is the share of the between-facility variance that is stable over
the trial; only the rest moves a facility's rate from one period to the next and adds noise to
a within-facility comparison. CMS data bound it from one side: the correlation of hospitals' true
rates between FY2023 and FY2026, two windows with no discharges in common, pooled over conditions
with at least 20 hospitals, is 0.74 (the covariance of observed rates over the between-hospital
standard deviations, so sampling noise does not attenuate it). That is drift over three years in
three-year averages; quarter-to-quarter shocks average out of CMS windows and cannot be seen. The
planning CAC is 0.7, just below it, and the grid is 0.5, 0.7 and 0.9.

### 6.4 How the MDE moves with the ICC

MDE in percentage points, 21 months (notebook section 6):

| Design | CAC | ICC 0.005 | 0.01 | 0.02 | 0.03 | 0.05 |
|---|---|---|---|---|---|---|
| Stepped wedge, 6 steps | 0.5 | 3.70 | 4.37 | 5.54 | 6.42 | 8.08 |
| Stepped wedge, 6 steps | 0.7 | 3.40 | **3.82** | 4.61 | 5.33 | 6.61 |
| Stepped wedge, 6 steps | 0.9 | 2.98 | 3.15 | 3.50 | 3.80 | 4.44 |
| Parallel, baseline period | 0.5 | 9.93 | not reached | not reached | not reached | not reached |
| Parallel, baseline period | 0.7 | 9.48 | not reached | not reached | not reached | not reached |
| Parallel, baseline period | 0.9 | 8.98 | 9.39 | not reached | not reached | not reached |
| Parallel, no baseline | any | 9.46 | not reached | not reached | not reached | not reached |

At the estimated ICC the design is near its binomial floor; with a CAC of 0.7 or above the MDE
stays under 5 points for any ICC up to 0.02. The risk is a low CAC combined with a high ICC, a
network whose facilities swing from quarter to quarter: then the pilot can only see effects of
5 to 8 points.

## 7. Pre-specified analysis

**Estimand.** The difference in 30-day readmission rate between intervention and usual care, on
the absolute scale, averaged over facilities and periods with discharge weights, intent to treat
by facility schedule (a facility counts as on the intervention from the first day of its
scheduled period, whatever its call completion).

**Primary model.** For facility i in period j, with y the readmission rate and n the index
discharges: y_ij = period_j + facility_i + theta x_ij, by weighted least squares with weights
n_ij. theta is the effect; negative means fewer readmissions. Period effects remove secular trend
and season, facility effects remove stable differences between facilities (Thompson, Davey,
Fielding, Hargreaves and Hayes 2018 analyse stepped wedges on cluster-period summaries the same
way).

**Test.** Two-sided permutation test of theta = 0: the same estimate under 9,999 orders drawn from
the stratified scheme of section 3.4, p = (1 + number at least as large in absolute value) /
10,000. The test is exact whatever the correlation structure, which is the reason for it with 12
facilities: model-based standard errors from 12 clusters are too small without a correction. One
confirmatory test, alpha 0.05, no multiplicity adjustment.

**Interval.** 95 percent confidence interval by inverting the permutation test: the set of shifts
d for which y_ij + d x_ij gives p above 0.05.

**Supportive analyses** (reported in full, never chosen among):

- Patient-level logistic GEE with an exchangeable working correlation within facility, period and
  condition as fixed effects, Mancl-DeRouen bias-corrected standard errors and a t reference
  with 10 degrees of freedom (12 facilities minus 2), giving an odds ratio.
- Linear mixed model on facility-period rates with facility and facility-period random
  intercepts, period fixed effects and Kenward-Roger degrees of freedom (R `lme4` and
  `pbkrtest`), the conventional stepped-wedge model, to show the permutation result does not
  depend on the choice.
- Condition-adjusted rates (indirect standardization to the condition-specific usual-care rates),
  in case the condition mix shifts at switch-over.
- Excluding each facility's first month on the intervention (learning curve).
- The Medicare fee-for-service subgroup, the HRRP population.

## 8. Monitoring and stopping rules

- **No stopping for efficacy or futility.** The last facilities have no intervention time until
  month 18, so an early efficacy look would rest on the facilities that started first and a
  fraction of the comparisons; the program is cheap to continue. The primary analysis runs once,
  after the last discharge's 30-day window closes.
- **Harm.** Each quarter the quality committee sees the primary model's estimate for readmissions
  and ED visits. The pilot stops if either shows harm with a permutation p below 0.001 (a
  Haybittle-Peto boundary, so the final test keeps its 0.05 level).
- **Fidelity.** A facility under 60 percent completion within 72 hours for two consecutive
  months gets a fix within a quarter (staffing, call scheduling). If the network-wide rate on the
  intervention stays under 60 percent for two quarters, the pilot stops: it would no longer test
  the program it set out to test, and the result would be reported as a failure of delivery.
- **Staff hours** over budget by 25 percent for a quarter go to the COO for a continue or stop
  decision; no automatic stop.

## 9. Decision for each result

theta is reported as a reduction (positive means fewer readmissions) with its 95 percent interval.
The smallest reduction worth the program is set at 2 points; Contoso finance confirms or changes
it before randomization (cost per discharge called over cost per readmission avoided), and a
change is a Deviation.

| Result | Reading | Decision |
|---|---|---|
| p at or below 0.05, reduction, guardrails clean | the calls reduce readmissions | keep the program in all 12 facilities as standard work; extend to non-HRRP discharges with the same monitoring |
| p at or below 0.05, reduction, a guardrail breached | effective at a cost | COO weighs the breach (ED visits up, or hours over budget) against the reduction; redesign before extending |
| p above 0.05, interval excludes reductions of 2 points or more | a reduction worth the cost is ruled out | stop the structured script, return the nurse hours; keep basic calls only if wanted for patient experience |
| p above 0.05, interval includes 0 and 2 points | inconclusive: "the data cannot distinguish no effect from a reduction of up to X" | keep only if staff hours are within budget, report it as unproven, do not count it in HRRP planning; a patient-level trial (section 3.1) is the next step if the question still matters |
| p at or below 0.05, increase | harm | stop |

A null or inconclusive result is reported with the same prominence a positive one would get.

## 10. Limitations

- Contoso's volumes and call rates are synthetic; a real deployment reruns `analysis/03_power.py`
  against real operational tables before randomizing, and the design may change with them.
- Readmissions to hospitals outside Contoso are not seen in Contoso's own records. If the program
  shifts where patients are readmitted, the estimate is biased; Medicare claims (secondary
  outcome) capture readmissions anywhere but arrive months later.
- Facility-period shocks are modelled as one variance per period whatever its length; real
  shocks may partly average out over 3 months, which would make the design slightly more
  powerful than stated.
- HRRP penalties use three-year windows announced two years later, so a real reduction reaches
  Contoso's ERR, and its penalty, two to four fiscal years after the pilot; the pilot measures
  readmissions directly and makes no penalty forecast.
- Expected discharges per period are fixed in the simulation; seasonal volume swings change
  power little because period effects absorb what all facilities share.

## Deviations

None yet.
