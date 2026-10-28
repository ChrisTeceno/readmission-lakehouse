# Data sources

Every source this project reads, where it comes from, and what to watch for. Dates are when the
source was last checked by a real download.

## Network and landing

Serverless compute reaches data.cms.gov and www.cms.gov (probe 2026-10-21, recorded in
SPEC.md), so fetch runs as the first task of the Databricks job. Fetch downloads with retries on
429, 5xx and timeouts and lands raw files unchanged in the managed volume
`workspace.readmission.landing` (bundle resource `resources/landing.yml`) through the Files API.
Layout: `/Volumes/workspace/readmission/landing/<source>/[<partition>/]<batch_id>/<file>`, with a
`_manifest.json` per batch listing each file's source URL, dataset id, snapshot or modified
date, inner file name, sha256 and row count. The manifest is written last, so a batch with a
manifest is complete. Load reads only the landing volume and skips a batch whose `_batch_id`
is already in Bronze or whose files all match a loaded sha256 (`batches_to_load`).

```
uv run python -m readmission.ingest.fetch hrrp_history_pdc                     # to the volume
uv run python -m readmission.ingest.fetch hrrp_history_ipps --local data/landing
```

On 2026-10-26 data.cms.gov answered the job's serverless fetch with HTTP 403 (`Access Denied`)
on the datastore metadata call, so `hrrp_history_ipps` and `ipps_impact` (both on www.cms.gov)
were landed from a laptop with
`uv run python -m readmission.ingest.fetch hrrp_history_ipps ipps_impact --profile readmission`
and the job run with `--only bronze,silver,gold`. The fetch/load split is what makes that
possible.

`--work-dir` (default `data/fetch/`, gitignored) caches downloaded zips. Landing in the volume
was checked on 2026-10-21 with all seven IPPS years (batch `20261021T190000Z`).

## Pipeline job

`databricks bundle run readmission_pipeline -t dev` runs five serverless tasks from the project
wheel (`resources/readmission_pipeline.yml`): `fetch` runs the fetch CLI for every source, then
`bronze` (`readmission.bronze.load`) appends what is new in landing to the Bronze tables and
copies the MTSamples notes. A second run with no source changes appends nothing. `contoso`
(`readmission.bronze.contoso`) runs beside them: it reads Contoso's Lakebase database directly,
not the landing volume (see the Contoso section below). `silver` (`readmission.silver.build`)
runs after `bronze` and `contoso` and rebuilds the Silver tables (see `docs/governance.md`).
`gold` (`readmission.gold.build`) runs last and rebuilds the Gold tables (see `docs/gold.md`).

The job runs daily at 06:00 UTC; the dev target's `trigger_pause_status: PAUSED` preset keeps
the schedule paused there. `fetch` retries twice, two minutes apart, after the HTTP client's own
exponential backoff gives up (Jobs only retry at a fixed interval). A failed run emails the job
owner (`${workspace.current_user.userName}`).

### Run monitoring

Every task wraps its work in `readmission.ops.track_run`, which appends when the task ends:

| Table | One row per | Columns |
|---|---|---|
| `ops_run_log` | task run (a retried task has one per attempt) | `job_run_id`, `task`, `started_at`, `ended_at`, `status` (`succeeded` or `failed`), `rows_written`, `error` |
| `ops_dq_metrics` | metric | `job_run_id`, `task`, `table_name`, `metric`, `value`, `threshold`, `passed`, `measured_at` |

Metrics: `rows_written` per table from every task (per `landing/<source>` for fetch); Silver's
`row_count` (threshold: 80 percent of the previous build), `err_null_rate` (45 percent) and
`quarantined_rows`; Gold's `row_count_vs_silver` (must equal `silver_hrrp_history`) and
`anomaly_rate` (5 percent, recorded but never failing). The job passes
`--job-run-id={{job.run_id}}`; run from a laptop without it, a task logs nothing.

Forced failure, to test the alert path: the job parameter `fail_task` names a task that raises
`ForcedFailure` as soon as its run starts, which logs a failed `ops_run_log` row and fails the
job.

```
databricks bundle run readmission_pipeline -t dev --params fail_task=gold
```

Clean run, 2026-10-21 (job run 338286842208810): every Bronze, Silver and Gold table and
`ops_watermarks` were dropped first; the schema, the landing volume, Lakebase and the two ops
tables were kept, so fetch found every source already landed and downloaded nothing new. The
run took 12 minutes 58 seconds from `bundle run` to success:

| Task | Minutes | Rows written |
|---|---|---|
| `fetch` | 0.5 | 175,563 (landed rows counted per source) |
| `contoso` | 0.8 | 11,124 |
| `bronze` | 1.3 | 316,246 |
| `silver` | 6.6 | 480,282 |
| `gold` | 2.2 | 599,782 |

All 50 `ops_dq_metrics` rows of that run passed. Forced failure: job run 796922301916095 with
`fail_task=gold` (gold task only) wrote two `failed` rows for `gold` with error
`ForcedFailure: gold: forced failure (--fail-task=gold)`; serverless retried the task once on
its own, so there is one row per attempt.

Silver row counts from the 2026-10-21 run:

| Table | Rows | Quarantined |
|---|---|---|
| `silver_hrrp` | 18,330 | 0 |
| `silver_hrrp_history` | 266,159 | 0 |
| `silver_hospital_info` | 5,419 | 0 |
| `silver_unplanned_visits` | 67,060 | 0 |
| `silver_clinical_notes` | 2,357 | 1 (note 97 has no transcription text) |
| `silver_contoso_discharges`, `silver_contoso_followup_calls` | built by `contoso` | 0 |
| `silver_acs_county` | 45,091 | 0 |
| `silver_hospital_county` | 75,866 (5,419 hospitals by 14 vintages) | 0 |

Silver treats `Not Applicable` as a suppression code too: Unplanned Hospital Visits writes it for
patient counts on rate-only measures.

## Current CMS datasets (verified 2026-10-21)

| Source | Dataset | Bronze table | Landed file | Rows | CMS `modified` |
|---|---|---|---|---|---|
| `cms_hrrp` | `9n3s-kdb3` | `bronze_hrrp` | `FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv` | 18,330 | 2026-01-26 |
| `cms_hospital_info` | `xubh-q36u` | `bronze_hospital_info` | `Hospital_General_Information.csv` | 5,419 | 2026-07-22 |
| `cms_unplanned_visits` | `632h-zaca` | `bronze_unplanned_visits` | `Unplanned_Hospital_Visits-Hospital.csv` | 67,060 | 2026-09-10 |

Fetch reads the dataset metadata (`/metastore/schemas/dataset/items/<id>`) and lands the CSV its
`downloadURL` names, unchanged, under `<source>/<batch_id>/`. The CSV is used rather than paging
the datastore: one request takes 1 to 3 seconds where the datastore takes 40 to 55 seconds per
1,500-row page, and the landed bytes are exactly what CMS publishes. If the latest manifest for
the source has the same `modified`, fetch lands nothing. Code: `ingest/cms_current.py`,
`bronze/cms.py`.

Bronze keeps every column as a string as received, named by snake_casing the CSV header. The
datastore API names two columns differently (`citytown` and `countyparish` where Bronze has
`city_town` and `county_parish`). Metadata columns: `_ingested_at`, `_source` (download URL),
`_batch_id`, `_sha256`, `_source_modified` (CMS `modified`).

## MTSamples clinical notes

`bronze_clinical_notes` copies the Delta table `/Volumes/workspace/default/raw-data/mtsamples_clean`
(columns `id`, `description`, `medical_specialty`, `sample_name`, `transcription`, `keywords`;
partitioned by `medical_specialty`, whose values keep a leading space). There is no fetch. The
load reads the latest Delta version, never writes to the source, and stores that version in
`_source_modified`; a run whose version is already in Bronze copies nothing. Code:
`bronze/clinical_notes.py`.

## Contoso operational database (Lakebase, synthetic)

Contoso Health is fictional, and so is everything in its database. The 12 facilities are real
Ohio hospitals' public CCNs relabeled with invented names (`Contoso Health Alder` and so on), so
Contoso's tables join to the CMS tables; no real hospital's data is altered. Monthly discharges
by facility and HRRP condition are scaled from each CCN's FY2026 HRRP discharge counts (Medicare
fee-for-service, 36-month window) by an all-payer factor of 2, with winter peaks for pneumonia,
COPD and heart failure; a condition CMS reports no count for gets no rows. Follow-up call counts
are drawn per facility, month and condition, split by whether the call came within 72 hours of
discharge. Values are deterministic (fixed seed, one generator per row key).

| Postgres table (`contoso` schema) | Key | Bronze table | Silver table | Rows (2026-10-21) |
|---|---|---|---|---|
| `facilities` | `facility_id` | `bronze_contoso_facilities` | `silver_contoso_facilities` | 12 |
| `discharges` | facility, `discharge_month`, `condition` | `bronze_contoso_discharges` | `silver_contoso_discharges` | 1,850 |
| `followup_calls` | facility, month, condition, `within_72h` | `bronze_contoso_followup_calls` | `silver_contoso_followup_calls` | 3,700 |

Every table carries `updated_at`. The load reads only rows with `updated_at` after the watermark
in `ops_watermarks` (one row per source table) over JDBC, appends them to Bronze with
`_ingested_at`, `_source` (`lakebase:contoso.<table>`) and `_batch_id`, then rebuilds Silver,
keeping the latest version of each key. Setup, credentials and the incremental test:
`docs/lakebase.md`; connector choice: `engagement/adr/0001-contoso-lakebase-connector.md`.
Code: `ingest/contoso.py`, `bronze/contoso.py`, `silver/contoso.py`, `scripts/seed_contoso.py`.

## Census ACS county socioeconomics (verified 2026-10-21)

Landing source `acs_county`, partitioned by ACS 5-year vintage
(`acs_county/<vintage>/<batch_id>/`), Bronze table `bronze_acs_county`, Silver tables
`silver_acs_county` and `silver_hospital_county`. Code: `ingest/acs_county.py`,
`bronze/acs_county.py`, `silver/acs_county.py`, `silver/county_match.py`, `secrets.py`.

Per vintage, fetch lands two Census API responses unchanged:

- `county_<vintage>.json`: `https://api.census.gov/data/<vintage>/acs/acs5/profile?get=NAME,<codes>&for=county:*`,
  the six measures for every county plus its name, which is also the geography list hospitals
  are matched against.
- `ct_county_subdivision_<vintage>.json`: `https://api.census.gov/data/<vintage>/acs/acs5?get=NAME&for=county%20subdivision:*&in=state:09`,
  Connecticut's towns and the county or planning region each sits in.

A published vintage never changes, so fetch skips a vintage that already has a manifest; after
the first run the source makes no API calls until a new vintage is added to
`ingest/acs_county.py` (2025 is due in December 2026).

**API key.** api.census.gov stopped answering keyless data queries: since at least 2026-10-21
every one is redirected to `/data/missing_key.html` (only `variables.json` metadata still works
without a key). The key is read through `readmission.secrets.get_secret`: from Key Vault
(`census-api-key`) while the Azure resources are deployed, otherwise from the Databricks secret
scope `readmission`, key `census_api_key` (see `docs/azure.md`). With no key, fetch logs that it is calling keyless and fails with an error naming
the scope and key once Census redirects. The key is never written to a manifest, a landed file,
`_source` or an error message: manifests record the query without it. Census also redirects a
keyed query to `missing_key.html` now and then (once during recording on 2026-10-21); fetch
retries that.

**Variables** (data profile tables, which give percentages directly). Codes move between
vintages when Census reorders a table, so they are recorded per vintage and checked against each
vintage's `variables.json` by `tests/test_acs_county.py -m live`:

| Measure | 2011 to 2016 | 2017 to 2018 | 2019 to 2024 |
|---|---|---|---|
| `population` | `DP05_0001E` | `DP05_0001E` | `DP05_0001E` |
| `pct_age_65_plus` | `DP05_0021PE` | `DP05_0024PE` | `DP05_0024PE` |
| `median_household_income` | `DP03_0062E` | `DP03_0062E` | `DP03_0062E` |
| `pct_below_poverty` (all people) | `DP03_0128PE` | `DP03_0128PE` | `DP03_0128PE` |
| `pct_uninsured` (civilian noninstitutionalized) | `DP03_0099PE` | `DP03_0099PE` | `DP03_0099PE` |
| `pct_disability` (civilian noninstitutionalized) | `DP02_0071PE` | `DP02_0071PE` | `DP02_0072PE` |
| `pct_disability`, Puerto Rico | `DP02PR_0071PE` | `DP02PR_0071PE` | `DP02PR_0072PE` |

Caveats:
- The 2011 vintage answers `-888888888` (not applicable) for uninsured and disability in every
  county: the 5-year health insurance and new disability estimates start with 2012. Silver
  leaves them null for 2011.
- Puerto Rico's 78 municipios have no `DP02_` disability value; it is in `DP02PR_`, which is null
  everywhere else. Silver takes whichever is present.
- Census writes annotation codes inside the estimate as large negatives (`-666666666` too few
  sample cases, `-888888888`, `-999999999`, `-222222222`). None of the measures can be negative,
  so Silver maps every negative to null. Median household income is suppressed this way for one
  or two counties in most vintages (De Baca County, NM in 2024).
- Island areas (Guam, US Virgin Islands, Northern Mariana Islands, American Samoa) are not in
  the ACS: the API answers 204 for them.
- Income is in dollars of each vintage's own year, not adjusted to a common year.

**Vintage for each fiscal year.** A fiscal year uses the vintage whose survey midpoint
(July 2 of vintage minus 2) is nearest the midpoint of its HRRP performance period, capped at
the latest published vintage (`silver.acs_county.ACS_VINTAGE_BY_FISCAL_YEAR`):

| FY | Performance period | ACS vintage | Survey years |
|---|---|---|---|
| 2013 | 07/2008 to 06/2011 | 2011 | 2007 to 2011 |
| 2014 | 07/2009 to 06/2012 | 2012 | 2008 to 2012 |
| 2015 | 07/2010 to 06/2013 | 2013 | 2009 to 2013 |
| 2016 | 07/2011 to 06/2014 | 2014 | 2010 to 2014 |
| 2017 | 07/2012 to 06/2015 | 2015 | 2011 to 2015 |
| 2018 | 07/2013 to 06/2016 | 2016 | 2012 to 2016 |
| 2019 | 07/2014 to 06/2017 | 2017 | 2013 to 2017 |
| 2020 | 07/2015 to 06/2018 | 2018 | 2014 to 2018 |
| 2021 | 07/2016 to 06/2019 | 2019 | 2015 to 2019 |
| 2022 | 07/2017 to 12/2019 | 2020 | 2016 to 2020 |
| 2023 | 07/2018 to 06/2021 | 2021 | 2017 to 2021 |
| 2024 | 07/2019 to 06/2022 | 2022 | 2018 to 2022 |
| 2025 | 07/2020 to 06/2023 | 2023 | 2019 to 2023 |
| 2026 | 07/2021 to 06/2024 | 2024 | 2020 to 2024 |
| 2027 | 07/2023 to 06/2025 | 2024 (2026 once published) | 2020 to 2024 |

A vintage is published about a year and a half after its survey period ends, so the vintage
matched to a fiscal year's performance period was not available when CMS announced that year's
penalties. That is right for describing a period; a model that must predict before publication
should lag it.

**Hospital to county.** `silver_hospital_county` matches every hospital in
`silver_hospital_info` against each vintage's county names, within its state
(`silver/county_match.py`): exact on a normalized key (case, accents, punctuation and spacing
ignored, Saint as St., a leading `E.` as East, "the" dropped, the county type dropped, an
independent city keeps "city"), then a short alias table for CMS typos and the 2015 renames
(Shannon to Oglala Lakota SD, Wade Hampton to Kusilvak AK), then the town for Connecticut
planning regions and for Valdez-Cordova AK (split into Chugach and Copper River in 2019), then a
fixed FIPS table for island areas, then a fuzzy match accepted only when one county is clearly
closest.

Result of the 2026-10-21 run: all 5,419 hospitals matched in all 14 vintages (target: above 98
percent; the build fails below it). Methods in the 2024 vintage: 5,358 exact, 36 Connecticut
town, 14 alias (10 `THE DISTRICT` for DC, `NORTH SLOPE BOROUH`, `NORTHWEST ARTIC BOROUGH`, 2
`JEFFRSON DAVIS` LA), 6 island area, 2 Valdez-Cordova town, 3 fuzzy (2 `YELLOW MEDCINE` MN,
`SCOTT BLUFF` NE). Island-area hospitals get a FIPS code but no `silver_acs_county` row.

Row counts from the 2026-10-21 run: `bronze_acs_county` 47,516 (45,091 county rows: 3,220 to
3,222 per vintage, including 78 Puerto Rico municipios; 2,425 Connecticut town rows),
`silver_acs_county` 45,091, `silver_hospital_county` 75,866.

## Historical HRRP, FY2013 to FY2027

Landing sources `hrrp_history_pdc` and `hrrp_history_ipps`, partitioned by fiscal year
(`<source>/<fiscal_year>/<batch_id>/`); only the extracted HRRP file and its manifest land, never
the zip. Bronze tables `bronze_hrrp_history_pdc` and
`bronze_hrrp_history_ipps`, unified in `silver_hrrp_history` (one row per facility, measure and
fiscal year). Code: `src/readmission/ingest/hrrp_history.py`, `bronze/hrrp_history.py`,
`silver/hrrp_history.py`.

### PDC archive, FY2019 to FY2026 (verified 2026-10-21)

Index: `https://data.cms.gov/provider-data/api/1/archive/aggregate/theme/hospitals/relative`.
The response is `{"data": [...], "meta": ...}`; each entry has `name`, `url` (site-relative,
prefix `https://data.cms.gov`), `date`, `type` (`theme` for dated snapshots, `annual_theme` for
yearly bundles, which are skipped) and `size`. The `size` field is stale for some entries, so
downloads are checked against `Content-Length` and the zip structure instead.

Snapshot selection: the fetch opens each snapshot newest first through HTTP Range requests
(only the zip directory and the HRRP file, well under 1 MB each), reads the HRRP file's
`Start Date`, and maps it to a fiscal year (FY N starts July 1 of N-5). Per fiscal year it keeps
the latest snapshot that carries it. Within a fiscal year every snapshot holds identical rows.

| FY | Snapshot | Inner file | Rows | Performance period |
|---|---|---|---|---|
| 2019 | 2019-10-31 | `HOSPITAL_QUARTERLY_QUALITYMEASURE_RRP_HOSPITAL.csv` | 19,674 | 07/01/2014 to 06/30/2017 |
| 2020 | 2020-10-16 | `9n3s-kdb3.csv` | 19,344 | 07/01/2015 to 06/30/2018 |
| 2021 | 2021-10-27 | `FY_2021_Hospital_Readmissions_Reduction_Program_Hospital.csv` | 19,224 | 07/01/2016 to 06/30/2019 |
| 2022 | 2022-10-06 | `hospitals_10_2022/FY_2022_Hospital_Readmissions_Reduction_Program_Hospital.csv` | 19,020 | 07/01/2017 to 12/01/2019 (COVID exclusion) |
| 2023 | 2023-11-06 | `hospitals_11_2023/FY_2023_Hospital_Readmissions_Reduction_Program_Hospital.csv` | 18,990 | 07/01/2018 to 06/30/2021 |
| 2024 | 2024-10-30 | `FY_2024_Hospital_Readmissions_Reduction_Program_Hospital.csv` | 18,774 | 07/01/2019 to 06/30/2022, excluding 01/2020 to 06/2020 |
| 2025 | 2025-11-26 | `FY_2025_Hospital_Readmissions_Reduction_Program_Hospital.csv` | 18,510 | 07/01/2020 to 06/30/2023 |
| 2026 | 2026-08-13 | `hospitals_2026-08-13/9n3s-kdb3_2026-01-26_FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv` | 18,330 | 07/01/2021 to 06/30/2024 |

Zip URL pattern:
`https://data.cms.gov/provider-data/sites/default/files/dataset-archives/theme/hospitals/hospitals_<YYYY-MM-DD>.zip`.

Caveats:
- FY2023: SPEC.md lists the 2023-10-06 snapshot; the selection takes 2023-11-06, which is later
  and whose HRRP file is byte-identical.
- 2020-07-04 and 2026-09-30 snapshots have no HRRP file; 2026-09-30 is a partial theme (3 MB).
- The 2026-08-13 zip was built on macOS and also holds `__MACOSX/._<name>` resource forks that
  match the file pattern; they are skipped.
- Snapshots before 2019-10-31 use `Provider ID` and `Hospital Name` and quote every value; later
  ones use `Facility ID` and `Facility Name`.
- Measure names are `READM_30_<M>_HRRP` in FY2019 and `READM-30-<M>-HRRP` from FY2020.
- Suppression: FY2019 writes `Not Available`; later years write `N/A`, and
  `Too Few to Report` for small readmission counts. `Number of Discharges` is suppressed more
  often than the ratio.
- The file reports FY2024's window as 07/01/2019 to 06/30/2022; CMS excluded January to June
  2020 discharges inside it.
- Parallel downloads from data.cms.gov sometimes end early with HTTP 200 and a truncated body;
  the fetch retries until length and zip structure check out.
- No PDC file carries the dual-eligibility peer group or the peer median ERR.

### CMS.gov IPPS supplemental files, FY2013 to FY2027 (verified 2026-10-21; FY2026 2026-10-26; FY2019 to FY2025 2026-10-27)

Archive page:
`https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/archived-supplemental-data-files`.
www.cms.gov needs a browser User-Agent. Each zip holds several files (spreadsheets, DRG tables,
claim trims, superseded drafts); the fetch lands only the hospital-level readmissions text file,
taking the latest correction where CMS published one.

| FY | Zip URL | Inner file | Hospitals | Performance period |
|---|---|---|---|---|
| 2013 | `https://www.cms.gov/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads/fy_2013_fr_readmissions_file.zip` | `Readmissions PUF-FY 2013 IPPS Correction-March 2013.txt` | 3,500 | 07/01/2008 to 06/30/2011 |
| 2014 | `https://www.cms.gov/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads/readmissions-supplemental-data-puf.zip` | `FY 2014 Readmissions Supplemental Data Corection - Sept 2013.txt` | 3,483 | 07/01/2009 to 06/30/2012 |
| 2015 | `https://www.cms.gov/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads/fy2015-fr-readmit-supp-data-file.zip` | `Final FY15-CN Oct 2014.txt` | 3,476 | 07/01/2010 to 06/30/2013 |
| 2016 | `https://www.cms.gov/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads/fy2016-cms-1632-fr-readmissions.zip` | `FY 2016 IPPS Final Rule Readmissions PUF_readmadj revised 08-04-15.txt` | 3,464 | 07/01/2011 to 06/30/2014 |
| 2017 | `https://www.cms.gov/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads/fy2017-cms-1655-fr-hospital-readmissions.zip` | `FY 2017 IPPS Final Rule Readmissions PUF (FY17 data).txt` | 3,448 | 07/01/2012 to 06/30/2015 |
| 2018 | `https://www.cms.gov/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads/fy2018-fr-hospital-readmissions.zip` | `FY 2018 IPPS Final Rule Readmissions PUF (FY18 data).txt` | 3,414 | 07/01/2013 to 06/30/2016 |
| 2019 | `https://www.cms.gov/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads/fy2019-cms-1694-fr-hospital-readmissions.zip` | `Section 508 - FR FY 19 - tab - *.txt` | 3,173 | 07/01/2014 to 06/30/2017 |
| 2020 | `https://www.cms.gov/files/zip/fy-2020-ipps-final-rule-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `*Supplemental_File tab2.txt` | 3,130 | 07/01/2015 to 06/30/2018 |
| 2021 | `https://www.cms.gov/files/zip/fy-2021-ipps-final-rule-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `*_FR FY 2021.txt` | 3,080 | 07/01/2016 to 06/30/2019 |
| 2022 | `https://www.cms.gov/files/zip/fy-2022-ipps-final-rule-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `508_Compliant_Version_of_FR FY 2022.csv` | 3,047 | 07/01/2017 to 12/01/2019 |
| 2023 | `https://www.cms.gov/files/zip/fy-2023-ipps-final-rule-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `*FR FY 2023.txt` | 3,044 | 07/01/2018 to 06/30/2021, January to June 2020 excluded |
| 2024 | `https://www.cms.gov/files/zip/fy-2024-ipps-final-rule-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `*Variations FR FY 2024 tab .txt` | 3,037 | 07/01/2019 to 06/30/2022, January to June 2020 excluded |
| 2025 | `https://www.cms.gov/files/zip/fy-2025-ipps-final-rule-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `508-Version-FR-FY-2025.csv` | 2,980 | 07/01/2020 to 06/30/2023 |
| 2026 | `https://www.cms.gov/files/zip/fy2026-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `FR FY 2026.txt` | 2,945 | 07/01/2021 to 06/30/2024 |
| 2027 | `https://www.cms.gov/files/zip/fy-2027-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `FY2027_HRRP_Supplemental_File - FR FY 2027 Tab.txt` | 2,912 | 07/01/2023 to 06/30/2025 |

Periods come from the variable description file in each zip. The archived page is wrong for two
years: its FY2026 entry links the FY2025 zip and its FY2023 entry has no zip link. The FY2026 URL
above is the one on the FY2026 final rule home page
(`https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/fy-2026-ipps-final-rule-home-page`),
and its file reports the FY2026 period. FY2019 to FY2026 are on the PDC archive too: `silver_hrrp_history`
keeps PDC's rows for those years and takes only `peer_group` and `peer_median_err` from the
supplemental files, through `silver_hrrp_payment` and `silver_hrrp_payment_measure` (ADR 0008).
From FY2019 each zip holds an xlsx workbook and a text copy for accessibility ("Section 508");
the fetch lands the text copy, which is comma-separated for FY2022 and FY2025 and tab-separated
otherwise. Every value in the text copies matched the workbooks to the four decimals CMS
prints (checked 2026-10-27). Columns are matched by name in lower case with single spaces, since
capitalization changes by year, and a file missing a required column is refused.

Caveats:
- Tab-separated, cp1252, CRLF. One to four title rows above the header (FY2013 has four) and
  note rows or `End of worksheet` below the data.
- The CCN column is `PROV` (FY2013 to FY2015), `Provider` (FY2016 to FY2018) or `Hospital CCN`
  (FY2027). Every CCN in these files is already 6 digits; Silver pads anyway.
- Wide layout: `Number of <condition> Cases` and `Excess Readmission Ratio for <condition>` (or
  `<condition> Excess Readmission Ratio`) until FY2018; `Number of eligible discharges for <M>`
  and `ERR for <M>` in FY2027. Column order changes between years, and the FY2017 header misspells
  pneumonia as `Pneumoniuia`.
- No readmission counts and no predicted or expected rates in any year.
- Measures: AMI, HF and pneumonia in FY2013 and FY2014; COPD and hip/knee from FY2015; CABG from
  FY2017.
- FY2013 to FY2017 write an ERR of `0` when a hospital had fewer than 25 cases; Silver maps it
  to null. FY2018 and FY2027 write `.` for missing values and report ERRs for some hospitals
  under 25 cases.
- FY2027 quotes discharge counts over 999 with a thousands separator (`"1,110"`).
- Peer groups: FY2019 to FY2027 carry `Peer group assignment` (dual-eligibility quintile 1 to
  5; `Peer Group Assignment` in FY2019 and FY2020) and `Peer group median ERR for <M>`. FY2013
  to FY2018 predate peer grouping (it began in FY2019). The FY2023 file has no pneumonia
  columns: CMS left pneumonia out of the FY2023 payment program. FY2019 to FY2027 also have dual proportion, neutrality modifier, penalty indicator
  and DRG payment ratio columns; `silver_hrrp_payment` and `silver_hrrp_payment_measure` carry
  them, with the payment adjustment factor for every IPPS year.
- The payment adjustment factor header changes by year: `FY 2013  Readmission Payment
  Adjustment Factor`, `Corrected FY 2015  Readmissions Adjustment Factor`, `Payment adjustment
  factor` (FY2026 on). FY2022 on also write `Payment reduction percentage` with a `%` sign.
- The FY2026 zip also holds `FR FY 2026 Case Info.txt`; the pattern matches the hospital file
  only.
- FY2027 covers two years of discharges, not three, per its variable description.

### IPPS final rule impact files, FY2026 and FY2027 (verified 2026-10-26)

Source `ipps_impact`, table `bronze_ipps_impact` (`record` maps, like the IPPS readmissions files),
fetched by `readmission.ingest.ipps_impact`. One row per IPPS hospital with the inputs CMS models
the year's payments from; Gold uses them to estimate base operating DRG payments
(`docs/penalty-dollars.md`).

| FY | Zip URL | Inner file | Hospitals | Cases from |
|---|---|---|---|---|
| 2026 | `https://www.cms.gov/files/zip/fy2026-ipps-fr-impact-file.zip` | `FY 2026 IPPS Final Rule Impact File.txt` | 3,103 | MedPAR FY2024 |
| 2027 | `https://www.cms.gov/files/zip/fy2027-ipps-fr-impact-file.zip` | `FY 2027 IPPS Impact File - Correction Notice.txt` | 3,075 | MedPAR FY2025 |

Caveats:
- Tab-separated, one title row, header on row 2 starting `Provider Number`; CRLF.
- The FY2027 zip holds both the final rule and the correction notice file; the correction notice
  is later and goes with the correction notice Table 1B amounts, so it is the one landed.
- Columns are named after the year: `FY 2027 Wage Index`, and `CASETA44` / `TACMIV44` for the
  FY2027 grouper (V44), beside `CASETA43` / `TACMIV43` for the prior year's grouper. Silver picks
  the year's own grouper (fiscal year minus 1983).
- The file carries real hospital names in `Name`. Silver drops them; Gold and the dashboard show
  only Contoso's names.
- `Proxy Readmission Adjustment Factor` is the prior year's payment adjustment factor, not the
  year's.

### Silver normalization

`silver_hrrp_history` columns: `facility_id`, `measure` (`AMI`, `HF`, `PN`, `COPD`, `HIP_KNEE`,
`CABG`), `fiscal_year`, `discharges`, `readmissions` (null for IPPS years),
`excess_readmission_ratio`, `predicted_rate` and `expected_rate` (null for IPPS years),
`peer_group` and `peer_median_err` (FY2019 on, joined from the payment tables), `period_start`, `period_end`, `source` (`pdc`
or `ipps`). CCNs are left-padded to 6 characters; `N/A`, `Not Available`, `Too Few to Report`,
`.` and blank become null; thousands separators are stripped. Periods come from a constant per
fiscal year, not from the files.

Row counts from the 2026-10-21 run:

| FY | AMI | HF | PN | COPD | HIP_KNEE | CABG | Total | With ERR |
|---|---|---|---|---|---|---|---|---|
| 2013 | 3,500 | 3,500 | 3,500 | | | | 10,500 | 8,495 |
| 2014 | 3,483 | 3,483 | 3,483 | | | | 10,449 | 8,445 |
| 2015 | 3,476 | 3,476 | 3,476 | 3,476 | 3,476 | | 17,380 | 13,965 |
| 2016 | 3,464 | 3,464 | 3,464 | 3,464 | 3,464 | | 17,320 | 13,709 |
| 2017 | 3,448 | 3,448 | 3,448 | 3,448 | 3,448 | 3,448 | 20,688 | 14,315 |
| 2018 | 3,414 | 3,414 | 3,414 | 3,414 | 3,414 | 3,414 | 20,484 | 16,613 |
| 2019 | 3,279 | 3,279 | 3,279 | 3,279 | 3,279 | 3,279 | 19,674 | 14,280 |
| 2020 | 3,224 | 3,224 | 3,224 | 3,224 | 3,224 | 3,224 | 19,344 | 14,071 |
| 2021 | 3,204 | 3,204 | 3,204 | 3,204 | 3,204 | 3,204 | 19,224 | 13,885 |
| 2022 | 3,170 | 3,170 | 3,170 | 3,170 | 3,170 | 3,170 | 19,020 | 13,379 |
| 2023 | 3,165 | 3,165 | 3,165 | 3,165 | 3,165 | 3,165 | 18,990 | 12,919 |
| 2024 | 3,129 | 3,129 | 3,129 | 3,129 | 3,129 | 3,129 | 18,774 | 12,077 |
| 2025 | 3,085 | 3,085 | 3,085 | 3,085 | 3,085 | 3,085 | 18,510 | 11,927 |
| 2026 | 3,055 | 3,055 | 3,055 | 3,055 | 3,055 | 3,055 | 18,330 | 11,720 |
| 2027 | 2,912 | 2,912 | 2,912 | 2,912 | 2,912 | 2,912 | 17,472 | 14,575 |

266,159 rows in total, no duplicate (facility_id, measure, fiscal_year) keys.
