# Penalty dollars and nurse-call economics

What Contoso's HRRP penalty costs in FY2026 and FY2027, and what a 72-hour nurse follow-up call
program would cost and could save, as ranges. Built by `readmission.gold.penalty_dollars` and
`readmission.gold.nurse_call_economics`; sources checked 2026-10-26 unless a line says otherwise.

Contoso Health is fictional. Its 12 facilities are real public Ohio CMS Certification Numbers
(CCNs) under invented names, so every penalty input below is a real public CMS figure shown under
a fictional name. The CMS files carry the real hospital names; no Gold table, dashboard or
document here shows them, only Contoso's ids, names and the CCNs.

**Every dollar figure here is an estimate.** CMS publishes each hospital's payment adjustment
factor but not the base payments it is applied to, so the base is estimated from CMS's own
modelling file.

## Headline

- **Estimated penalty: about $1.96M in FY2026 and $1.76M in FY2027** across the 12 facilities, on
  about $525M and $534M of estimated base operating DRG payments.
- **Program cost: about $525K a year** (range $295K to $1.01M), first year, setup included, for
  about 16,400 HRRP-condition discharges a year (10,200 and $371K before Contoso's synthetic
  volumes were corrected on 2026-10-28, `docs/synthetic-data.md` section 7).
- **Trials find no effect from a follow-up call alone.** The program pays for itself on Contoso's
  own ledger if it lowers the 30-day readmission rate by about half a point to a point (0.53 to
  1.05 points at the central cost, depending on how much of the drop reaches the ERR). At the
  1-point central judgment it about breaks even: -$22K if half the drop reaches the ERR, +$404K
  if all of it does. The pilot's minimum detectable effect is 3.44 points, so the pilot cannot confirm break-even; it can only rule out
  large effects.
- Payer savings are larger than the penalty avoided but are Medicare's and insurers' money, shown
  separately and never added to Contoso's.

## Timeline: which year's discharges each bill is for

An HRRP fiscal year names the year the penalty is paid, not the year it measures. FY2027 runs
from October 2026 to September 2027 and is charged on discharges from July 2023 to June 2025,
published in August 2026, so it is complete and fixed. Nothing Contoso does now changes it.

| Fiscal year | Penalty paid | Discharges measured | Published | Where it stands | Estimated penalty |
|---|---|---|---|---|---|
| FY2026 | Oct 2025 to Sep 2026 | 07/2021 to 06/2024 | Aug 2025 | The bill paid; the comparison year | about $2.0M |
| FY2027 | Oct 2026 to Sep 2027 | 07/2023 to 06/2025 | Aug 2026 | The bill being paid now; exact cutoff | about $1.8M |
| FY2028 | Oct 2027 to Sep 2028 | 07/2024 to 06/2026 (assumed) | Aug 2027 (expected) | Mostly baked in: its discharges are in the past; the penalty model forecasts it | not estimated |
| FY2029 on | Oct 2028 on | 07/2025 to 06/2027 and later (assumed) | Aug 2028 on | What Contoso can still change | see the economics below |

FY2028 and later windows assume CMS keeps FY2027's two-year window and moves it a year at a time;
CMS sets each one in that year's final rule.

```
Discharge year (July to June)  21-22 22-23 23-24 24-25 25-26 26-27 27-28 28-29
FY2026 (paid Oct 2025)           ###   ###   ###
FY2027 (paid Oct 2026)                       ###   ###
FY2028 (paid Oct 2027)                             ###   ###
FY2029 (paid Oct 2028)                                   ###   ###
FY2030 (paid Oct 2029)                                         ###   ###
FY2031 (paid Oct 2030)                                               ###   ###
Program starts 2027                                             >>>>>>>>>>>>>>>>
```

A program started in 2027 first touches the end of FY2029's window and is fully inside FY2031's,
so it first moves the penalty around FY2029 to FY2031.

### What changed from FY2026 to FY2027 (lessons learned)

The total fell by about $195K, from $1,959,057 to $1,764,148 (`gold_penalty_by_condition`, each
facility's penalty split by its conditions' terms in the PAF formula). The two windows share
July 2023 to June 2024, so part of each change is the older years dropping out.

| Condition | FY2026 | FY2027 | Change |
|---|---|---|---|
| CABG | $108,686 | $639,136 | +$530,450 |
| HF | $354,600 | $390,107 | +$35,508 |
| COPD | $24,507 | $33,837 | +$9,330 |
| Hip/knee | $483,310 | $343,169 | -$140,141 |
| Pneumonia | $462,520 | $279,941 | -$182,579 |
| AMI | $525,434 | $77,958 | -$447,476 |

Largest moves by facility and condition:

| Facility, condition | FY2026 | FY2027 | Change | ERR FY2026 to FY2027 (peer median) |
|---|---|---|---|---|
| CH04 Dogwood, AMI | $372,787 | $12,933 | -$359,854 | 1.184 to 1.003 (0.997) |
| CH01 Alder, CABG | $57,182 | $364,302 | +$307,120 | 1.043 to 1.186 (1.006) |
| CH07 Hawthorn, CABG | $51,504 | $237,819 | +$186,315 | 1.061 to 1.302 (0.990) |
| CH02 Birch, hip/knee | $157,563 | $7,238 | -$150,325 | 1.161 to 1.008 (0.999) |
| CH04 Dogwood, pneumonia | $136,860 | $0 | -$136,860 | 1.065 to 0.976 (0.996) |
| CH05 Elm, pneumonia | $8,359 | $138,118 | +$129,758 | 0.998 to 1.054 (0.996) |
| CH11 Oak, HF | $74,657 | $170,752 | +$96,095 | 1.128 to 1.249 (0.995) |
| CH03 Cedar, hip/knee | $20,190 | $89,793 | +$69,603 | 1.105 to 1.224 (0.999) |
| CH07 Hawthorn, HF | $49,062 | $116,974 | +$67,912 | 1.044 to 1.101 (0.995) |
| CH11 Oak, hip/knee | $2,972 | $67,439 | +$64,467 | 1.006 to 1.250 (0.994) |

Dogwood's AMI and pneumonia and Birch's hip/knee are where FY2027 improved; CABG at Alder and
Hawthorn, and heart failure and hip/knee at Oak, are where it got worse. Those are the pairs to
watch in FY2028's forecast and to start the follow-up pilot with.

### What Contoso can still change (FY2029 on)

- **Start the nurse-call pilot now.** Its results arrive before the windows a program would move.
- **Focus the review on the facilities and conditions above,** with hip/knee across the network
  (the FY2028 forecast leads with Oak and Hawthorn).
- **Break-even is about half a point to a point** of the 30-day readmission rate at the central
  program cost (below), smaller than anything the pilot can detect.

## Sources

| Source | URL | Used for | Checked |
|---|---|---|---|
| FY2026 HRRP supplemental file | `https://www.cms.gov/files/zip/fy2026-hospital-readmissions-reduction-program-supplemental-data-file.zip`, inner file `FR FY 2026.txt`; linked from the FY2026 final rule home page, `https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/fy-2026-ipps-final-rule-home-page` | FY2026 payment adjustment factor, peer medians, DRG payment ratios, neutrality modifier | 2026-10-26 |
| FY2027 HRRP supplemental file | `https://www.cms.gov/files/zip/fy-2027-hospital-readmissions-reduction-program-supplemental-data-file.zip`, inner file `FY2027_HRRP_Supplemental_File - FR FY 2027 Tab.txt` | The same for FY2027 | 2026-10-26 |
| FY2026 IPPS final rule impact file | `https://www.cms.gov/files/zip/fy2026-ipps-fr-impact-file.zip`, inner file `FY 2026 IPPS Final Rule Impact File.txt` | Wage index, provider type, transfer-adjusted cases (`CASETA43`) and case mix index (`TACMIV43`), MedPAR FY2024 | 2026-10-26 |
| FY2027 IPPS impact file, correction notice | `https://www.cms.gov/files/zip/fy2027-ipps-fr-impact-file.zip`, inner file `FY 2027 IPPS Impact File - Correction Notice.txt` (the zip also holds the superseded final rule file) | The same for FY2027 (`CASETA44`, `TACMIV44`), MedPAR FY2025 | 2026-10-26 |
| FY2026 Tables 1A to 1E | `https://www.cms.gov/files/zip/fy2026-ipps-fr-table-1a-1e.zip` (`CMS-1833-F Tables 1A - 1E.txt`) | Standardized amounts: Table 1B labor $4,186.62, nonlabor $2,565.99; Table 1A $4,456.72, $2,295.89 | 2026-10-26 |
| FY2027 Tables 1A to 1E | `https://www.cms.gov/files/zip/fy2027-ipps-fr-table-1a-1e.zip` (`CMS-1849-CN Tables 1A - 1E.txt`, the correction notice) | Table 1B labor $4,245.76, nonlabor $2,602.24; Table 1A $4,519.68, $2,328.32 | 2026-10-26 |
| 42 CFR 412.152 | `https://www.law.cornell.edu/cfr/text/42/412.152` | Definition of base operating DRG payments | 2026-10-23 |
| 42 CFR 412.154 | `https://www.law.cornell.edu/cfr/text/42/412.154` | The payment adjustment factor formula, the 0.97 floor, the 25-discharge rule | 2026-10-23 |
| Medicare Inpatient Hospitals by Provider, DY2024 | `https://data.cms.gov/data-api/v1/dataset/ee6fb1a5-39b9-46b3-a980-a7284551a732/data` | Sanity check, and $13,986 Medicare payment per Contoso discharge | 2026-10-23 |
| BLS OEWS May 2025, Ohio, SOC 29-1141 | `https://api.bls.gov/publicAPI/v2/timeseries/data/`, series `OEUS390000000000029114108` (median), `OEUS390000000000029114103` (mean) | RN wage $39.67 and $42.18 an hour | 2026-10-23 |
| BLS ECEC, civilian hospital workers, Q2 2026 | series `CMU1036220000000P` (benefits share), `CMU1036220000000D` | Benefits 34.9 percent of compensation | 2026-10-23 |
| Harrison JD et al., J Gen Intern Med 2014;29(11):1519-1525 | doi:10.1007/s11606-014-2954-2 | Nurse time per patient (0.5 FTE for 4,115 patients over 18 months) | 2026-10-23 |
| Boggan JC et al., Ann Intern Med 2025 | doi:10.7326/ANNALS-24-01140, PMID 39805113 | Pooled effect of post-discharge calls: risk difference 0.00 (95% CI -0.02 to 0.02), 7 RCTs, 7,075 patients | 2026-10-23 |
| Leppin AL et al., JAMA Intern Med 2014;174:1095-1107 | doi:10.1001/jamainternmed.2014.1608 | Multicomponent peri-discharge interventions, RR 0.82 (0.73 to 0.91) | 2026-10-23 |
| HCUP Statistical Brief #307 (Jiang, Barrett), April 2024 | `https://hcup-us.ahrq.gov/reports/statbriefs/sb307-readmissions-2020.jsp` | Hospital cost per Medicare readmission, $18,100 (2020) | 2026-10-23 |

Other studies read and not used as an effect size: Mistiaen and Poot, Cochrane CD004510
(inconclusive); Tang 2014, doi:10.1007/s11606-014-2942-6 (no difference); Jayakody 2016,
doi:10.1186/s12913-016-1650-9; Hansen 2011, Ann Intern Med (no single intervention alone reduced
readmissions).

### The FY2026 link

The archived supplemental data files page,
`https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/archived-supplemental-data-files`,
links its FY2026 entry to the FY2025 zip, and its FY2023 entry has no zip link. The FY2026 final
rule home page links the correct FY2026 file (data period 07/01/2021 to 06/30/2024; Contoso's
discharge counts match the PDC FY2026 values, for example CH01 AMI 214). The fetch uses that link.

### Files that exist and are not loaded

The FY2019 to FY2025 supplemental files are public and carry peer group medians, dual
proportions, neutrality modifiers and DRG payment ratios:

| FY | URL (prefix `https://www.cms.gov`) |
|---|---|
| 2019 | `/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads/fy2019-cms-1694-fr-hospital-readmissions.zip` |
| 2020 to 2025 | `/files/zip/fy-<year>-ipps-final-rule-hospital-readmissions-reduction-program-supplemental-data-file.zip` |

FY2022 and FY2025 ship only as xlsx. Loading them would make the FY2019 to FY2025 cutoffs exact
and retire the 1.0 stand-in of ADR 0006 (`engagement/adr/0006-approximate-cutoff-stand-in.md`).
That touches the model labels, the causal sample and the dashboard captions, so it is a separate
task; this one adds only the FY2026 file, and `silver_hrrp_history` keeps its FY2026 rows from the
PDC archive with the stand-in.

## Method

### The payment adjustment factor (PAF)

42 CFR 412.154, FY2019 on:

```
ratio = neutrality modifier x sum over conditions of
        DRG payment ratio x max(0, ERR - peer group median ERR)
        (only conditions with 25 or more eligible discharges)
PAF   = max(0.97, 1 - ratio)
```

The project uses CMS's published PAF for the dollar estimate and recomputes it from the
supplemental file's terms for the sensitivity. The recomputed FY2027 PAF matches the published one
to 4 decimals for all 12 Contoso CCNs. FY2026 matches within 0.0001; CH07 computes to 0.98796
against a published 0.9879, because CMS rounds its inputs before publishing.

| Facility | CCN | FY2026 PAF | FY2027 PAF |
|---|---|---|---|
| CH01 Alder | 360085 | 0.9990 | 0.9963 |
| CH02 Birch | 360155 | 0.9864 | 0.9909 |
| CH03 Cedar | 360068 | 0.9998 | 0.9991 |
| CH04 Dogwood | 360051 | 0.9941 | 0.9994 |
| CH05 Elm | 360027 | 0.9996 | 0.9967 |
| CH06 Fir | 360039 | 0.9998 | 1.0000 |
| CH07 Hawthorn | 360072 | 0.9879 | 0.9839 |
| CH08 Juniper | 360040 | 0.9978 | 0.9992 |
| CH09 Linden | 360054 | 1.0000 | 1.0000 |
| CH10 Maple | 360218 | 0.9939 | 1.0000 |
| CH11 Oak | 360125 | 0.9885 | 0.9794 |
| CH12 Spruce | 360029 | 0.9862 | 0.9999 |

The impact file's `Proxy Readmission Adjustment Factor` is the prior year's PAF (the FY2027
impact file shows 0.9862 for 360029, its FY2026 value), so it is not used.

### Base operating DRG payments (estimate)

42 CFR 412.152 defines them as the wage-adjusted DRG operating payment plus new technology
add-ons, excluding IME, DSH, outliers and low-volume payments, before the value-based purchasing
adjustment. For a sole community hospital (CH06, CH08 and CH11, provider types 16 and 17) the
federal rate is the base, not the hospital-specific rate. The estimate:

```
base    = transfer-adjusted cases x transfer-adjusted case mix index
          x (labor amount x wage index + nonlabor amount)
penalty = (1 - published PAF) x base
```

- Cases and case mix index are the fiscal year's own grouper columns in its impact file (V43 for
  FY2026, V44 for FY2027), from MedPAR two years earlier, which is how CMS models the year.
- Table 1B applies when the wage index is at or below 1, Table 1A above it. All 12 Contoso
  facilities are at or below 1. The amounts assume quality data submitted and a meaningful EHR
  user, which holds for all 12 (`Proxy Quality Reduction` and `Proxy EHR Reduction` are 0).
- New technology add-ons are left out; they are small and not in the impact file.
- Worked example, CH07 FY2027: 2,302.94 x 1.7776 x (4,245.76 x 0.9236 + 2,602.24) = $26.71M;
  penalty (1 - 0.9839) x $26.71M = $429,963.

Sanity check: the estimated base is 75 to 97 percent of each facility's DY2024 total Medicare
inpatient payment, the gap tracking each hospital's IME and DSH (largest for CH01, the teaching
hospital). Contoso DY2024: 43,388 discharges, $606.8M Medicare payment.

A closer figure would come from HCRIS cost reports (Worksheet E Part A,
`https://downloads.cms.gov/FILES/HCRIS/HOSP10FY2024.ZIP`), which report by cost-report period
rather than fiscal year; not done here.

### Results

| Facility | Est. base FY2026 | Est. penalty FY2026 | Est. base FY2027 | Est. penalty FY2027 |
|---|---|---|---|---|
| CH01 Alder | $114.45M | $114,449 | $118.99M | $440,265 |
| CH02 Birch | $37.92M | $515,685 | $36.10M | $328,501 |
| CH03 Cedar | $100.95M | $20,190 | $99.77M | $89,793 |
| CH04 Dogwood | $110.16M | $649,928 | $116.03M | $69,617 |
| CH05 Elm | $46.99M | $18,794 | $48.89M | $161,322 |
| CH06 Fir | $39.51M | $7,902 | $41.95M | $0 |
| CH07 Hawthorn | $25.35M | $306,683 | $26.71M | $429,963 |
| CH08 Juniper | $8.52M | $18,733 | $6.69M | $5,354 |
| CH09 Linden | $8.48M | $0 | $7.43M | $0 |
| CH10 Maple | $15.85M | $96,706 | $15.20M | $0 |
| CH11 Oak | $12.10M | $139,136 | $11.59M | $238,852 |
| CH12 Spruce | $5.13M | $70,851 | $4.82M | $482 |
| **Total** | **$525.4M** | **$1,959,057** | **$534.2M** | **$1,764,148** |

FY2027 uses the correction notice impact file. The research report's $1,761,170 came from the
final rule file; the correction raised the wage index from 0.8812 to 0.8888 at CH02, CH03, CH05,
CH09 and CH12 and from 0.8961 to 0.9011 at CH08, which adds about $3K. In prose: about $2.0M in FY2026 and $1.8M in FY2027.

## Sensitivity: what a lower readmission rate does to the penalty

The PAF is piecewise linear in each ERR, zero below the peer median and capped at 3 percent, so
it is recomputed per facility and condition, never scaled. A facility already at a PAF of 1.0
(CH06, CH09 and CH10 in FY2027) gains nothing.

A drop of d points in the 30-day readmission rate, from Contoso's 16.9 percent usual-care rate
(`docs/pilot-design.md`, section 2), is a relative drop r = d / 16.9. Each ERR is multiplied by
1 - f x r, where f is the pass-through: CMS shrinks each hospital's predicted rate toward the
mean, so less than the whole relative drop reaches the ERR, more so at low volume, and CMS
publishes no closed form. f = 0.5 and f = 1.0 are the bounds. Peer medians, the neutrality
modifier, DRG payment ratios and base payments stay at FY2027 values. The total at no drop is
$1,753,757 (the unrounded recomputed PAF, against $1,764,148 with the published one).

| Drop, points | Penalty, f = 0.5 | Avoided | Penalty, f = 1.0 | Avoided |
|---|---|---|---|---|
| 0 | $1,753,757 | $0 | $1,753,757 | $0 |
| 0.5 | $1,473,059 | $280,698 | $1,250,698 | $503,059 |
| 1.0 | $1,250,698 | $503,059 | $824,906 | $928,851 |
| 2.0 | $824,906 | $928,851 | $367,814 | $1,385,943 |
| 3.0 | $549,911 | $1,203,846 | $111,509 | $1,642,249 |
| 3.44 | $459,259 | $1,294,498 | $43,724 | $1,710,033 |

Caveats:
- The pilot outcome is all-payer; HRRP counts Medicare fee-for-service only.
- HRRP windows are two to three years and announced one to three years after they close, so a
  program starting in 2027 first moves the penalty around FY2029 to FY2031, and one program year
  counts in more than one fiscal year's window. The table is a steady-state annual figure.
- Peers improving would lower the medians and shrink the gain.

## Program cost (assumptions)

| Assumption | Low | Central | High | Source |
|---|---|---|---|---|
| Discharges a year (HRRP conditions, all payers) | 16,400 | 16,400 | 16,400 | 1,366 a month, `docs/pilot-design.md` section 2 (synthetic, all-payer factor 3.2) |
| RN wage, Ohio | $39.67/h (median) | $42.18/h (mean) | $42.18/h | BLS OEWS |
| Benefits | 34.9% of compensation (x 1.536) | same | same, plus 20% overhead (assumption) | BLS ECEC |
| Loaded cost | $60.94/h | $64.79/h | $77.75/h | derived |
| Nurse minutes per discharge, attempts and charting included | 15 (assumption) | 23 (Harrison 2014) | 35 (assumption) | |
| Setup, first year | $20K | $50K | $100K | **assumption**, no published figure |
| Program management | 0.2 FTE | 0.5 FTE | 1.0 FTE | **assumption**, 2,080 hours an FTE |
| **Annual cost** | **$295,192** | **$524,714** | **$1,005,542** | |

No source gives a fully loaded cost per nurse call; the range is built from wage data and time
assumptions. Usual care already calls 42.7 percent of discharges within 72 hours, so the cost over
today's is lower than the full cost shown; pricing today's calls needs a decision this work does
not make.

## Effect sizes

| Scenario | Drop, points | Basis |
|---|---|---|
| none | 0 | Pooled RCTs of post-discharge calls alone: risk difference 0.00 (Boggan 2025) |
| central | 1.0 | **A judgment** between the null estimate and its upper confidence bound |
| high | 3.0 | Leppin 2014 RR 0.82 on 16.9 percent (3.04), a multicomponent bundle, not a call alone |
| pilot_mde | 3.44 | The pilot's minimum detectable effect; **larger than the evidence supports** |

## Who saves what

The ledgers are kept apart and never added:

- **Contoso (the headline):** the penalty avoided minus the program cost. Under DRG payment an
  avoided readmission also removes the payment Contoso would have received for it, and whether
  that nets to a gain depends on Contoso's margin on those stays and whether the bed is refilled;
  no public figure gives that, so avoided readmission costs are not counted as Contoso savings.
- **Payers (Medicare and insurers):** about $14,000 per avoided readmission (Medicare paid $13,986
  per Contoso discharge in DY2024), applied to all avoided readmissions.
- **Society:** $18,100 per readmission avoided (HCUP 2020 hospital cost), a resource-cost view
  only (about $21,000 in 2025 prices, BLS PPI hospitals `PCU622---622---`).

## Economics (FY2027 base, central cost $524,714)

| Scenario | Drop | Readmissions avoided | Penalty avoided, f = 0.5 | Contoso net, f = 0.5 | Penalty avoided, f = 1.0 | Contoso net, f = 1.0 | Payer savings |
|---|---|---|---|---|---|---|---|
| none | 0 | 0 | $0 | -$524,714 | $0 | -$524,714 | $0 |
| central | 1.0 | 164 | $503,059 | -$21,655 | $928,851 | $404,138 | $2,296,000 |
| high | 3.0 | 492 | $1,203,846 | $679,132 | $1,642,249 | $1,117,535 | $6,888,000 |
| pilot_mde | 3.44 | 564 | $1,294,498 | $769,784 | $1,710,033 | $1,185,319 | $7,898,240 |

Contoso's upside is capped by the penalty it pays, about $1.75M a year; the payer's is not.

### Break-even

The smallest drop, on a 0.01-point grid, at which the penalty avoided covers the program cost:

| Program cost | f = 0.5 | f = 1.0 |
|---|---|---|
| Low $295,192 | 0.54 | 0.27 |
| Central $524,714 | 1.05 | 0.53 |
| High $1,005,542 | 2.22 | 1.11 |

All but one sit inside the pooled RCT confidence interval (up to 2 points); the high cost at
half pass-through needs 2.22. All are well below the pilot's 3.44-point MDE. The plain reading:
trials find no effect from a call alone. At the central cost the program needs half a point to a
point to pay for itself at Contoso, and at the 1-point central judgment it about breaks even
(-$21,655 at half pass-through, +$404,138 at full), so it no longer clearly pays for itself at
that effect. The pilot cannot detect a drop that small. With 10,200 discharges a year (before the
2026-10-28 volume correction) break-even was 0.36 to 0.71 points and the 1-point net was +$132K to
+$558K; the program costs more now because there are more discharges to call, while the penalty
it can avoid is unchanged.

## Tables

| Table | Grain | Built from |
|---|---|---|
| `silver_hrrp_payment` | facility, fiscal year (FY2013 to FY2018, FY2026, FY2027) | `bronze_hrrp_history_ipps` |
| `silver_hrrp_payment_measure` | facility, condition, fiscal year (FY2026, FY2027) | `bronze_hrrp_history_ipps` |
| `silver_ipps_impact` | facility, fiscal year (FY2026, FY2027) | `bronze_ipps_impact` |
| `gold_penalty_dollars` | Contoso facility, fiscal year | the three Silver tables, `silver_contoso_facilities` |
| `gold_penalty_sensitivity` | Contoso facility, drop (0 to 4 points by 0.01), pass-through | `gold_penalty_dollars`, Silver payment tables |
| `gold_nurse_call_economics` | effect scenario, cost level, pass-through | `gold_penalty_sensitivity` |

Column definitions are in `docs/gold.md` and on each table in Unity Catalog. The dashboard page
"Penalty dollars and follow-up calls" shows the facility penalties for both years, the scenario
table and the break-even drops.
