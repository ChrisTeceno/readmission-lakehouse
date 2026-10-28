# Gold layer

What the Gold tables in `workspace.readmission` hold and the rules they are built on. The
`gold` task of the `readmission_pipeline` job (`readmission.gold.build`) rebuilds them from
Silver on every run. Code: `src/readmission/gold/`.

| Table | Grain | Built from |
|---|---|---|
| `gold_facility_condition_year` | facility, measure, fiscal year (FY2013 to FY2027) | `silver_hrrp_history`, `silver_hospital_info` |
| `gold_penalty_mart` | facility, fiscal year | `gold_facility_condition_year`, `silver_contoso_facilities` |
| `gold_contoso_scorecard` | Contoso facility, measure, fiscal year | `gold_facility_condition_year`, `silver_contoso_*` |
| `gold_features` | facility, measure, target fiscal year | `gold_facility_condition_year`, `silver_hospital_info`, `silver_hospital_county`, `silver_acs_county` |
| `gold_penalty_predictions` | facility, measure, next fiscal year (FY2028) | `gold_features` and the `penalty_model` champion; written by the `score` task, see `docs/model-card.md` |
| `gold_penalty_dollars` | Contoso facility, fiscal year (FY2026, FY2027) | `silver_hrrp_payment`, `silver_hrrp_payment_measure`, `silver_ipps_impact`, `silver_contoso_facilities` |
| `gold_penalty_by_condition` | Contoso facility, measure, fiscal year (FY2026, FY2027) | `gold_penalty_dollars`, `silver_hrrp_payment_measure` |
| `gold_penalty_sensitivity` | Contoso facility, readmission drop, ERR pass-through (FY2027) | `gold_penalty_dollars`, `silver_hrrp_payment_measure`, `silver_hrrp_payment` |
| `gold_nurse_call_economics` | effect scenario, cost level, ERR pass-through | `gold_penalty_sensitivity` |

Row counts from the 2026-10-21 run: `gold_facility_condition_year` 266,159 (one per
`silver_hrrp_history` row, checked by the task), `gold_penalty_mart` 49,008,
`gold_contoso_scorecard` 984, `gold_features` 283,631 (266,159 targets with labels plus
17,472 FY2028 scoring rows).

## Penalty cutoff

A condition counts toward a hospital's HRRP payment reduction when its excess readmission ratio
(ERR) is above the cutoff for that fiscal year.

| Fiscal years | Cutoff | `cutoff_is_approximate` |
|---|---|---|
| FY2013 to FY2018 | 1.0 | false: this was the rule |
| FY2019 to FY2026 | 1.0 | true: CMS used the median ERR of the hospital's dual-eligibility peer group, which `silver_hrrp_history` does not carry (FY2026's is in `silver_hrrp_payment_measure` for the dollar work only; ADR 0006) |
| FY2027 | `peer_median_err` from the IPPS supplemental file | false (true for a row with no ERR, where CMS published no median) |

For FY2019 to FY2026, 1.0 is a stand-in. Peer group medians sit close to 1.0 (the FY2027
medians run from 0.9837 to 1.0143, median 0.9955), so most rows land on the same side of
either cutoff, but a hospital with an ERR near 1.0 can flip: in FY2027, where both are known,
899 of the 14,575 rows with an ERR (6.2 percent) are on different sides of 1.0 and of their
peer median. Analysis that depends on the exact cutoff (the regression
discontinuity in task 2.3) should use FY2013 to FY2018 and FY2027, or filter on
`cutoff_is_approximate = false`.

## Non-overlapping windows

Each fiscal year's ERR covers a three-year window of discharges, and consecutive years share two
of those years, so FY t against FY t-1 mostly compares the same patients. Gold compares each
fiscal year with its prior non-overlapping window: the latest fiscal year whose performance
period ends before this one starts (`readmission.gold.windows.prior_window_year`).

| FY | Prior window | Why |
|---|---|---|
| 2013 to 2015 | none | the window before it predates HRRP data |
| 2016 to 2024, 2026 | t-3 | three-year windows; FY2022's ends 12/2019 and FY2024's starts 07/2019, so both still map to t-3 |
| 2025 | 2022 | FY2025 starts 07/2020, after FY2022's COVID-shortened window ends |
| 2027 | 2025 | FY2027's two-year window starts 07/2023, the day after FY2025's ends |

`window_months` is 36, except 30 for FY2022 (07/2017 to 12/2019) and FY2024 (January to June
2020 excluded) and 24 for FY2027. Discharge comparisons use discharges per month, so a shorter
window does not look like a drop.

## Anomaly flags

`anomaly_flag` is true when either rule fires; `anomaly_reason` names the rules and values.

- `err_change`: the ERR change from the prior window has a modified z-score above 3.5 in
  absolute value among hospitals with the same measure and fiscal year (Iglewicz and Hoaglin:
  0.6745 times the distance from the median, over the median absolute deviation). Median and
  MAD are used rather than mean and standard deviation so the outliers do not widen the spread
  that judges them.
- `discharge_jump`: the change in discharges per month from the prior window is more than
  double, or under half, the median change for the same measure and fiscal year, with at least
  25 discharges in both windows (CMS's minimum for an ERR). The median matters because whole
  years shift: the median hospital's discharges per month fell to about 0.65 times its prior
  window's in FY2023 to FY2025 (COVID) and rose to 2.3 times in FY2027. A jump against that is
  more often a merger, a CCN change or a coding change than a change in patients.

On the 2026-10-21 data 3,127 rows (1.2 percent) are flagged, 2,628 of them for a discharge jump.
The gold task records the share in `ops_dq_metrics` (`anomaly_rate`, threshold 5 percent). A higher share is logged as a failed metric but does not fail the run.

## Model features and the leakage rule

`gold_features` has one row per facility, measure and target fiscal year T, for every fact row,
plus scoring rows for the next cycle (FY2028) with null labels. The features for T use only
what CMS had published before it released FY T's results:

- HRRP history from fiscal years strictly before T (lags 1 to 3 of ERR, above-cutoff and
  discharges; lag 1 of readmissions, predicted and expected rates, ERR change and anomaly flag;
  counts and the mean and maximum ERR over all earlier years). The join condition is
  `fiscal_year < target_fiscal_year`, so a year T row cannot reach a feature.
- Hospital attributes that rarely change: state, hospital type, ownership, emergency services.
  Hospital General Information is only the current snapshot here, so the star rating and the
  readmission measure counts are left out: they are built from recent claims and would carry
  later years into early targets.
- Census ACS county measures (`acs_population`, `acs_median_household_income`,
  `acs_pct_below_poverty`, `acs_pct_uninsured`, `acs_pct_age_65_plus`, `acs_pct_disability`)
  from vintage T-3, joined through the hospital's county in that vintage. CMS announces FY T's
  results with the IPPS final rule in August of T-1, and ACS publishes vintage V in December of
  V+1, so T-3 is the latest vintage out before T's results (FY2016, announced August 2015,
  takes the 2013 vintage of December 2014). This is deliberately older than the vintage
  `docs/data-sources.md` maps to each fiscal year, which describes T's performance period but
  came out after T's penalties. Targets past the latest loaded vintage take it (FY2028 uses
  2024); FY2013 would need the 2010 vintage, which is not loaded, so its ACS columns are null.
- Only `label_above_cutoff`, `label_err` and `label_cutoff_is_approximate` come from year T.

`tests/test_gold.py::test_features_never_use_the_target_year_or_later` changes every fact row of
year T and later, and every ACS vintage after T-3, and fails if any feature column for target
T moves.

## Contoso scorecard

One row per Contoso facility, condition and fiscal year with a CMS row. The CMS columns come
from the facts through the facility's CCN; the operational columns add up Contoso's synthetic
monthly discharges and follow-up calls over the months of that fiscal year's performance
window. Contoso's records start in September 2023, so only FY2026 (10 months) and FY2027 (22
months) have operational data; `contoso_months` says how many months each row covers.
`followup_rate` and `followup_within_72h_rate` divide completed calls by Contoso's all-payer
discharges.

## Penalty dollars and nurse-call economics

Every dollar column is an estimate (`is_estimate` is always true); sources, formulas and every
assumption are in `docs/penalty-dollars.md`. Only Contoso's ids and names appear: the CMS files'
hospital names never reach Gold.

- `gold_penalty_dollars`: `est_penalty` = (1 - `payment_adjustment_factor`) x
  `est_base_operating_drg_payments`, where the base is `transfer_adjusted_cases` x
  `transfer_adjusted_cmi` x (`labor_amount` x `wage_index` + `nonlabor_amount`), with Table 1A or
  1B standardized amounts per fiscal year (constants in `readmission.gold.penalty_dollars`).
  `payment_adjustment_factor` is CMS's published value; `computed_payment_adjustment_factor` is
  the same factor recomputed from the per-condition terms (it matches FY2027 to 4 decimals).
  Totals: $1,959,057 for FY2026 and $1,764,148 for FY2027.
- `gold_penalty_by_condition`: each condition's `share_of_penalty` is its term (DRG payment
  ratio x ERR above the peer median, 25 or more discharges) over the facility's sum of terms;
  `est_penalty` = share x the facility's estimated penalty, so the rows sum to
  `gold_penalty_dollars`.
- `gold_penalty_sensitivity`: the FY2027 penalty recomputed with every ERR multiplied by
  1 - `err_pass_through` x `relative_drop`, for drops of 0 to 4 points by 0.01 (relative to 16.9
  percent) and pass-throughs of 0.5 and 1.0; peer medians, the neutrality modifier, DRG payment
  ratios and base payments stay at FY2027 values. 9,624 rows.
- `gold_nurse_call_economics`: 24 rows, four effect scenarios (0, 1.0, 3.0 and 3.44 points) x
  three cost levels x two pass-throughs. `hospital_net` (penalty avoided minus
  `annual_program_cost`) is Contoso's ledger; `payer_savings` and `resource_cost_avoided` are
  other parties' and are never added to it. `break_even_drop_points` is the smallest drop on the
  0.01-point grid whose penalty avoided covers the cost. Cost and effect assumptions are named
  constants with their sources in `readmission.gold.nurse_call_economics`.

## Governance

Every Gold table carries a table comment and a comment on every column (from its `StructType`),
re-applied on each run, and the tags `layer = gold`, `contains_phi = false`, `source` (`cms` or
`cms,contoso`) and `dataset` where the table comes from CMS datasets. The analyst grant is
issued as for Silver (see `docs/governance.md`).
