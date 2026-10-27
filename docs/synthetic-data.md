# How Contoso's synthetic data were made, and how realistic they are

Written 2026-10-27. The generator is `src/readmission/ingest/contoso.py`; the realism check is
`src/readmission/analysis/realism.py`, run by `scripts/check_synthetic_realism.py` and tested in
`tests/test_realism.py`. Benchmarks were checked on 2026-10-27 unless a line says otherwise.

## In short

Contoso Health is a made-up hospital network, so it has no real operational records. Its 12
facilities borrow the public identifiers (CMS Certification Numbers, CCNs) of 12 real Ohio
hospitals under invented names. Everything Medicare publishes about those 12 CCNs is real and
used as published: readmission rates, penalty ratios, payments. What Contoso would hold in its
own systems, how many patients it discharges each month and how many of them its nurses phone
after discharge, does not exist, so a small program writes it.

That program starts from each hospital's real Medicare discharge counts, doubles them to stand
for all payers, adds a winter rise for pneumonia, COPD and heart failure, adds random monthly
noise, and gives each facility its own call rates. It writes counts only: there are no patient
rows, so there is nothing that could be protected health information.

Compared with public figures, the winter rise for pneumonia and heart failure and the share of
patients reached by a call are in the range published studies report. One input is off: real
hospitals see roughly three all-payer discharges for these conditions for every Medicare
fee-for-service discharge, not two, so Contoso's volumes are about 38 percent too low. That
changes the pilot's sample size and detectable effect (section 7). It does not touch the
penalty figures, the model or the causal analysis, which rest on CMS data alone.

## 1. What is real and what is synthetic

| Data | Real or synthetic | Where it is used |
|---|---|---|
| The 12 CCNs, their state and their CMS figures (HRRP discharges, readmissions, excess readmission ratios, payments) | Real, public CMS data, unchanged | Scorecard, model, causal analysis, penalty dollars, the pilot's usual-care rate |
| Facility names (`Contoso Health Alder` and so on) and ids CH01 to CH12 | Invented | Everywhere a facility is named |
| Monthly discharges per facility and HRRP condition, all payers | Synthetic | Pilot volumes and power, follow-up dashboard, nurse-call cost |
| Follow-up calls per facility, month, condition and 72-hour flag | Synthetic | The pilot's usual-care call rate (42.7 percent), follow-up dashboard |
| Clinical notes | Public MTSamples transcriptions, not synthetic and not Contoso's (`docs/llm-extraction.md`) | LLM extraction |

No real hospital's data is altered: the generator reads the published CMS counts and writes
new rows; it never writes back to a CMS table. No Contoso number describes a real hospital.

## 2. Why synthetic

The project needs an operational database to show an incremental load from Postgres (Lakebase,
`docs/lakebase.md`, ADR 0001) and a pilot that rests on a client's own volumes. Real
operational data from a hospital would be protected health information or at least
confidential, and would need the agreements and controls listed in `engagement/phi-note.md`.
Synthetic counts tied to real public CCNs keep the joins to CMS data honest without any of that.

## 3. How it is generated

All parameters are constants at the top of `src/readmission/ingest/contoso.py`.

| Step | What it does | Parameter | Source or reason |
|---|---|---|---|
| 1. Roster | 12 Ohio CCNs relabeled CH01 to CH12 | `FACILITIES` | Real CCNs so Contoso joins to HRRP; one state so Census and peer comparisons are coherent |
| 2. Public volume | Each CCN's eligible discharges per HRRP condition | `Facility.hrrp_discharges` | CMS HRRP FY2026 file (dataset `9n3s-kdb3`, performance period 07/2021 to 06/2024, downloaded 2026-10-21). A condition CMS left blank (fewer than 25 cases, or no service line) gets no rows |
| 3. Monthly mean | Public count over the 36-month window, times the all-payer factor | `HRRP_WINDOW_MONTHS = 36`, `ALL_PAYER_FACTOR = 2.0` | HRRP counts Medicare fee-for-service only; Contoso's systems count every payer. The factor 2.0 was a round assumption; section 6 tests it |
| 4. Season | Mean times `1 + a cos(2 pi (month - 1) / 12)`, peaking in January | `WINTER_AMPLITUDE`: PN 0.25, COPD 0.2, HF 0.1; AMI, hip/knee and CABG 0 | Respiratory admissions and heart failure peak in winter; elective joints and CABG do not. Peak to trough is 1.67 for pneumonia, 1.5 for COPD and 1.22 for heart failure |
| 5. Noise | Count = mean plus normal noise with standard deviation sqrt(mean), rounded, floored at 0 | none | Poisson-like monthly variation |
| 6. Call rates | Per facility, fixed: attempt rate uniform 0.55 to 0.9, completion rate uniform 0.6 to 0.85, share of attempts within 72 hours uniform 0.6 to 0.85 | `_facility_rates` | Facilities differ the way a program's sites would; no published per-site distribution was used |
| 7. Calls per row | Attempts = discharges x attempt rate x uniform 0.9 to 1.1, capped at discharges; split into within and after 72 hours; completions = calls x rate x uniform 0.9 to 1.1, with the after-72-hours rate 0.7 times the early one | `followup_rows` | Later calls reach fewer patients |

The result for the 36 seeded months (September 2023 to August 2026): 12 facilities, 50
facility-condition pairs, 1,800 discharge rows holding 31,132 discharges (about 865 a month),
and 3,600 call rows. The live database has 1,850 and 3,700 rows because a later
`--append-month` added a month (`docs/lakebase.md`).

## 4. Seed and how to regenerate

`SEED = 20261021`. Each row key (facility, condition, month) gets its own `random.Random` seeded
from the seed and the key, so a month's rows are identical whether they came from the first
seed or a later `--append-month`, and adding a facility does not change anyone else's rows.

```
uv run python scripts/seed_contoso.py --profile readmission   # write 36 months to Lakebase
uv run python -c "from datetime import date; from readmission.ingest.contoso import discharge_rows; print(discharge_rows(date(2024, 1, 1)))"   # any month, locally
```

`tests/test_contoso_seed.py` checks determinism and that totals stay within three standard
deviations of the public count times the factor; `tests/test_power.py` regenerates the pilot's
12 months and checks the committed extract against them.

## 5. What the generator does not model

- **Readmissions.** None are generated. Every readmission rate in the project is CMS's own,
  Medicare fee-for-service, per CCN.
- **Patients.** No patient rows: no age, sex, payer per discharge, diagnosis codes, length of
  stay, discharge disposition or social factors.
- **Trend.** Volumes have no growth or decline over the 36 months, and no COVID-era dip.
- **Seasonality beyond three conditions.** AMI has none, though it rises a little in winter
  (section 6). Holiday and summer dips in elective hip/knee and CABG are absent.
- **Overdispersion.** Monthly noise is Poisson-sized; real monthly counts vary more (staffing,
  diversion, outbreaks). Not checked against a benchmark.
- **Relations between tables.** Call rates do not depend on volume, season or condition, and
  calls do not change anything downstream, since there are no readmissions to change.
- **Data quality problems.** No late, duplicated or corrected rows beyond what the incremental
  load test adds deliberately.

## 6. Realism check

```
uv run python scripts/check_synthetic_realism.py           # print the table
uv run python scripts/check_synthetic_realism.py --write   # refresh the table below
```

The synthetic values come from the generator, so the check needs no workspace and matches what
the seed wrote. `tests/test_realism.py` fails if the table below differs from the script's
output. Verdicts: **matches** (close on a like-for-like measure), **plausible** (inside the
published range, or close on a measure defined a little differently), **off** (outside what the
benchmark supports), **by construction, proves nothing** (the benchmark is the generator's own
input), **no benchmark**.

<!-- realism-check:begin -->
| Property | Benchmark | Source | Synthetic | Gap | Verdict |
|---|---|---|---|---|---|
| 36-month volume per facility and condition | CMS eligible discharges x 2.0, the generator's own input: 31,010 | CMS HRRP FY2026 (dataset 9n3s-kdb3) | 31,132 over 50 pairs | +0.4% in total; -5.2% to +18.0% per pair (noise) | by construction, proves nothing |
| All-payer discharges per Medicare FFS discharge, HRRP conditions | 3.21 (Contoso mix; 2.88 HF to 3.68 AMI and hip/knee); all conditions at these 12 CCNs 4.61 (2.70 to 5.82) | HCUP SB #277 (2018 payer shares); CMS Medicare Monthly Enrollment (Ohio 2021 to 2024); CMS cost reports and MedPAR 2023 | 2.00 | -38% against the condition estimate | off |
| Winter (Dec to Feb) over summer (Jun to Aug) admissions, PN | +51.3% | Butala 2018, HCUP SID NY and CA | +64.2% | +12.9 points | plausible |
| Winter (Dec to Feb) over summer (Jun to Aug) admissions, HF | +17.7% | Butala 2018, HCUP SID NY and CA | +21.8% | +4.1 points | matches |
| Winter (Dec to Feb) over summer (Jun to Aug) admissions, AMI | +6.3% | Butala 2018, HCUP SID NY and CA | -0.9% | -7.2 points | off |
| Winter over summer admissions, COPD; none for hip/knee and CABG | none checked |  | COPD +43.0%; hip/knee -19.4% and CABG -8.8%, noise around a flat mean |  | no benchmark |
| Discharges with a completed call within 72 hours | 48.5% to 65.1% completed (a structured program: two attempts within 72 hours, one academic medicine service) | Harrison 2014 | 43.0% | -5.6 points to -22.2 points | plausible |
| Discharges with a completed call at any time | 57.9% reached within 7 days (22 hospitals, all inpatients) | Lukanski 2023 | 51.5% | -6.4 points | plausible |
| Readmission rates | not generated: every rate in the analysis is CMS's own, per CCN | CMS HRRP | none |  | no benchmark |
| Age, sex, payer, length of stay, diagnosis codes | not generated: the database holds monthly counts only |  | none |  | no benchmark |
<!-- realism-check:end -->

How to read each row:

- **Volumes.** The generator is built to reproduce the CMS count times 2.0, and it does (0.4
  percent over in total; single pairs drift with noise, most at the smallest facilities). This
  shows the code does what it says, nothing about realism. The relative sizes of the 12
  facilities and six conditions are real, because they are CMS's.
- **All-payer factor.** Two independent estimates, both above 2.0.
  - *Condition level.* Medicare (fee-for-service and Medicare Advantage together) was the
    expected payer for 73.5 percent of heart failure stays, 69.2 percent of COPD, 63.1 percent of
    pneumonia, 57.5 percent of AMI and of osteoarthritis (the diagnosis behind most hip and knee
    replacements) and 58.8 percent of coronary atherosclerosis (behind most CABG), US, 2018. Over
    the HRRP window (07/2021 to 06/2024) 47.2 percent of Ohio's Medicare beneficiaries were in
    Original Medicare (51.0 percent in 2021 down to 44.2 in 2024). If Medicare Advantage members
    are hospitalized at the same rate as fee-for-service members, the all-payer to
    fee-for-service ratio is 1 / (share x 0.472): 2.88 for heart failure up to 3.68 for AMI and
    hip/knee, 3.21 weighted by Contoso's mix.
  - *Hospital level.* Across all conditions, these 12 hospitals' 2023 cost reports show 209,410
    discharges against 45,462 Medicare fee-for-service discharges in MedPAR for 2023, a ratio of
    4.61 (2.70 to 5.82 by hospital). Cost-report Title XVIII counts agree with MedPAR within 8
    percent at every CCN, which confirms Title XVIII is fee-for-service. All conditions include
    maternity, surgery and younger patients, so this ratio is an upper bound for the six HRRP
    conditions, but it is far from 2.0 at every hospital.
  - *Caveats.* Medicare Advantage members are hospitalized somewhat less than fee-for-service
    members, which pulls the condition estimate down; HRRP eligible discharges also exclude
    transfers, deaths, patients leaving against advice and those without 12 months of prior
    enrollment, which makes them smaller than all fee-for-service discharges and pushes the true
    factor up. The 2018 payer shares are national, not Ohio's. Even if every Medicare patient
    were fee-for-service, the ratio would be 1.36 to 1.74, so 2.0 sits between that impossible
    floor and every realistic estimate. Verdict: off, by about 38 percent low.
- **Seasonality.** Butala and colleagues counted 51.3 percent more pneumonia admissions in winter
  than summer, 17.7 percent more heart failure and 6.3 percent more AMI, all payers, New York and
  California 2005 to 2011. The generator's pneumonia peak is somewhat stronger (64.2 percent,
  about 59 before noise), heart failure is close, and AMI has no winter rise at all. The
  benchmark is from two other states and an earlier decade, and the paper's text and tables
  define seasons differently (January to March in the methods, December to February in the
  tables); with January to March the generator gives about 49 percent for pneumonia and 17 for
  heart failure, nearer still. A study of Israeli heart failure admissions found a January to
  August ratio of 2.00 (Aronson 2021), far above the generator's 1.22, so heart failure
  seasonality varies widely by place; the US benchmark is the relevant one. No COPD benchmark
  was verified. Hip/knee and CABG have no seasonal term, yet hip/knee shows 19.4 percent fewer
  discharges in winter: at about 24 a month across the network, noise alone moves the ratio by
  about 10 percent per standard deviation, so a pattern in a small series here is noise.
- **Follow-up calls.** In Harrison 2014, a structured program at one academic medicine service
  with two attempts within 72 hours, about 2,672 patients completed the call: 65 percent of the
  4,102 implied by the abstract's counts, 49 percent of the 5,507 eligible. Lukanski 2023, 22
  hospitals with calls targeted within 72 hours, reached 57.9 percent within 7 days. Contoso's
  usual care, which the pilot assumes is unstructured, completes a call within 72 hours for 43.0
  percent and at any time for 51.5 percent: a little below dedicated programs, which is what
  usual care should be. Plausible on both, with the caveat that neither study measures exactly
  what Contoso's rows count.
- **Not generated.** Readmission rates, age, length of stay and patient attributes have no
  synthetic value to test. The analysis uses CMS's rates directly.

## 7. Finding: the all-payer factor is low

The factor of 2.0 understates all-payer volumes by about 38 percent against the condition-level
estimate of 3.21. The generator is unchanged: results depend on these data, so a change waits
for a decision. What it moves, measured by rerunning `analysis/03_power.py` locally on the
committed extract with every volume and call count scaled by 3.21 / 2.0 (rates unchanged):

| Pilot figure | Factor 2.0 (published) | Factor 3.21 |
|---|---|---|
| HRRP-condition discharges a month | 852 | 1,367 |
| Index discharges over 21 months | 17,885 | 28,707 |
| MDE at 80 percent power | 3.82 points | 3.45 points |
| Power at a 2-point drop | 30 percent | 37 percent |
| Power at a 3-point drop | 60 percent | 70 percent |

The MDE moves less than the volume because most of the stepped wedge's noise is the
quarter-to-quarter movement of each facility's rate, which more discharges do not shrink
(`docs/pilot-design.md` section 3.3). The pilot's conclusions hold: 3.45 points is still a large
effect for a phone call. The nurse-call program's cost (`docs/penalty-dollars.md`) scales with
the 10,200 discharges a year it assumes, so the per-discharge cost would apply to about 16,400,
and the break-even effect would rise. The penalty dollars themselves are CMS figures and do not
move.

## 8. What the synthetic data cannot tell us

- **The pilot's power rests on synthetic volume and synthetic call rates.** The usual-care
  readmission rate (16.9 percent) and the between-hospital variation are CMS's; the 852
  discharges a month and the 42.7 percent 72-hour call rate are not. A real deployment reruns
  `analysis/03_power.py` on Contoso's own counts before randomizing (`docs/pilot-design.md`).
- **Follow-up operations figures** (the dashboard's call rates, the 72-hour share by facility)
  describe no real program. Differences between facilities are draws from uniform ranges, not
  findings.
- **Nurse-call cost** scales with synthetic volume (section 7).
- **Nothing links calls to readmissions** in Contoso's data, so no analysis here estimates a
  call's effect from Contoso's own records; the effect sizes in `docs/penalty-dollars.md` come
  from published trials.
- **Not affected:** the HRRP scorecard, penalty dollars, the penalty model, the causal analysis
  and the national landscape. They read real CMS data for real CCNs; only the names are
  invented.

## Sources

| Source | Link | Used for | Checked |
|---|---|---|---|
| CMS HRRP FY2026, hospital-level | Provider Data Catalog dataset `9n3s-kdb3` | The generator's input counts | 2026-10-21 |
| HCUP Statistical Brief #277, Most Frequent Principal Diagnoses for Inpatient Stays in U.S. Hospitals, 2018, Supplemental Table 1 | `https://hcup-us.ahrq.gov/reports/statbriefs/sb277-Top-Reasons-Hospital-Stays-2018.jsp` | Medicare share of stays per condition | 2026-10-27 |
| CMS Medicare Monthly Enrollment, Ohio, annual | `https://data.cms.gov/data-api/v1/dataset/cf1be15c-b102-4735-9bb1-1f5db074b76d/data` | Original Medicare share, 2021 to 2024 | 2026-10-27 |
| CMS Hospital Provider Cost Report, 2023 | `https://data.cms.gov/data-api/v1/dataset/f08d4c46-c0e4-4bcb-a54a-c62c87d59251/data` | All-payer and Title XVIII discharges per CCN | 2026-10-27 |
| CMS Medicare Inpatient Hospitals by Provider, 2023 | `https://data.cms.gov/data-api/v1/dataset/ad17abc4-0e93-4828-bba5-e8f39ddafbdb/data` | Medicare fee-for-service discharges per CCN | 2026-10-27 |
| Butala NM et al., Seasonality and Readmission after Heart Failure, Myocardial Infarction, and Pneumonia, Health Serv Res 2018;53(4):2185-2202 | `https://pmc.ncbi.nlm.nih.gov/articles/PMC6051982/` | Winter over summer admissions | 2026-10-27 |
| Aronson D, Environmental factors, winter respiratory infections and the seasonal variation in heart failure admissions, Sci Rep 2021;11:11292 | doi:10.1038/s41598-021-90790-7, PMID 34050240 | Heart failure seasonality outside the US | 2026-10-27 |
| Harrison JD et al., Assessing the impact of nurse post-discharge telephone calls on 30-day hospital readmission rates, J Gen Intern Med 2014;29(11):1519-25 | doi:10.1007/s11606-014-2954-2, PMID 25103122 | Completed calls within 72 hours | 2026-10-27 |
| Lukanski A et al., Implementing a Discharge Follow-up Phone Call Program Reduces Readmission Rates in an Integrated Health System, J Healthc Qual 2023;45(6):315-323 | doi:10.1097/JHQ.0000000000000400, PMID 37788411 | Patients reached within 7 days | 2026-10-27 |
