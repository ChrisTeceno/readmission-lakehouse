# What the data can and cannot answer

For the readout. Source: `analysis/01_eda` run on serverless on 2026-10-21 over the Gold tables
of that day's pipeline run (export: `docs/eda/01_eda.html`). The hypotheses and their thresholds
were fixed beforehand in `docs/analysis-plan.md`; all twelve are reported below, including the
ones that came out null.

## What the EDA found

| ID | Question | Result (Benjamini-Hochberg across 12, q = 0.05) |
|---|---|---|
| D1 | Do ERRs differ across conditions? | No: every condition centers on 1.0 (epsilon squared 0.0006). Spreads differ: hip/knee's 10th to 90th percentile range is 3.4 times COPD's. |
| D2 | Did the share of AMI, HF and pneumonia rows above 1.0 trend over FY2013 to FY2018? | No: -0.1 points a year, adjusted p 0.49. About half sit above 1.0 every year (48.7 percent). |
| D3 | Is ERR associated with volume? | Yes, meaningful: Spearman rho -0.16 with log discharges (FY2026). |
| D4 | Does ERR spread differ by volume? | Yes, meaningful: the largest third's interquartile range is 1.5 times the smallest third's, as CMS's shrinkage of low-volume hospitals toward 1.0 predicts. |
| D5 | Does hospital mean ERR differ by ownership? | Yes, meaningful (epsilon squared 0.027): for-profit hospitals' median is 0.016 above non-profits'. |
| D6 to D10 | Is hospital mean ERR associated with county income, poverty, uninsured, age 65+ or disability shares? | None clears the 0.10 threshold. Poverty (rho 0.098) and uninsured (0.055) are statistically detectable but small; income, age and disability are not distinguishable from zero. |
| D11 | Do Contoso's 12 hospitals differ from other Ohio hospitals? | Not distinguishable at this sample size: median 0.006 below, adjusted p 0.87. This is not evidence that they are alike. |
| D12 | Is the anomaly flag rate different in COVID-affected years? | Detectable but under the 1-point threshold: 0.8 percent of FY2022 to FY2025 rows are flagged against 1.6 percent in other years. |

Other things the EDA surfaced:

- The share of hospitals penalized jumped from 72.9 percent (FY2014) to 83.6 percent (FY2015)
  when CMS added COPD and hip/knee: more conditions means more chances to be above 1.0. In
  FY2027, the first year with real peer-group cutoffs here, it is 90.7 percent.
- Discharge jumps make up most anomaly flags (2,828 of 3,127 rows). Six of the 15 most extreme
  ERR moves belong to one facility (CCN 170146), which should be checked before it enters the
  model or the RDD sample.
- In FY2026, hip/knee puts the most Contoso facilities above 1.0 (8 of the 9 that report it).

## The data can answer

- Which conditions and facilities sit above the penalty cutoff, every year from FY2013 to
  FY2027, for every hospital CMS reports (about 2,800 to 3,500 a year).
- How a hospital's ERR compares with its condition's national distribution and with its Ohio
  peers, including Contoso's 12 facilities.
- Whether ERR is associated with volume, ownership and county socioeconomics, as descriptive,
  county-level associations.
- With the regression discontinuity in task 2.3, whether a condition's ERR three years later
  changed because it crossed the 1.0 cutoff in FY2013 to FY2018, for hospitals near the cutoff.

## The data cannot answer

- Whether readmissions fell nationally. CMS standardizes the ERR to 1.0 every year, so it ranks
  hospitals against each other and cannot show a level shift; about half of rows sit above 1.0
  in every year by construction.
- The exact penalty status in FY2019 to FY2026. CMS used dual-eligibility peer group medians that
  no file here carries; 1.0 stands in and disagrees with the real cutoff for about 6 percent of
  rows (measured in FY2027, where both are known).
- Penalty dollars. The payment reduction also depends on each hospital's base DRG payments,
  which this project does not load.
- Why a hospital's ERR is high. There are no patient-level data and no staffing or process
  measures, and the ACS links are county averages.
- Anything about Contoso's operations from CMS data. The 12 CCNs are real hospitals relabeled,
  and Contoso's operational records are synthetic and cover only the FY2026 and FY2027 windows.
- Hospital attributes in past years. Ownership and type are today's snapshot.
- The effect of the penalty away from the cutoff, or on hospitals far above it.
