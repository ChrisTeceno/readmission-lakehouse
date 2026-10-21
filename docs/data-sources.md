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

`--work-dir` (default `data/fetch/`, gitignored) caches downloaded zips. Landing in the volume
was checked on 2026-10-21 with all seven IPPS years (batch `20261021T190000Z`).

## Pipeline job

`databricks bundle run readmission_pipeline -t dev` runs four serverless tasks from the project
wheel (`resources/readmission_pipeline.yml`): `fetch` runs the fetch CLI for every source, then
`bronze` (`readmission.bronze.load`) appends what is new in landing to the Bronze tables and
copies the MTSamples notes. A second run with no source changes appends nothing. `contoso`
(`readmission.bronze.contoso`) runs beside them: it reads Contoso's Lakebase database directly,
not the landing volume (see the Contoso section below). `silver` (`readmission.silver.build`)
runs after `bronze` and `contoso` and rebuilds the Silver tables (see `docs/governance.md`).

Silver row counts from the 2026-10-21 run:

| Table | Rows | Quarantined |
|---|---|---|
| `silver_hrrp` | 18,330 | 0 |
| `silver_hrrp_history` | 266,159 | 0 |
| `silver_hospital_info` | 5,419 | 0 |
| `silver_unplanned_visits` | 67,060 | 0 |
| `silver_clinical_notes` | 2,357 | 1 (note 97 has no transcription text) |
| `silver_contoso_discharges`, `silver_contoso_followup_calls` | built by `contoso` | 0 |

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

### CMS.gov IPPS supplemental files, FY2013 to FY2018 and FY2027 (verified 2026-10-21)

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
| 2027 | `https://www.cms.gov/files/zip/fy-2027-hospital-readmissions-reduction-program-supplemental-data-file.zip` | `FY2027_HRRP_Supplemental_File - FR FY 2027 Tab.txt` | 2,912 | 07/01/2023 to 06/30/2025 |

Periods come from the variable description file in each zip. Do not use the CMS.gov FY2023 and
FY2026 links: they serve the wrong year's file.

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
- Peer groups: only FY2027 carries `Peer group assignment` (dual-eligibility quintile 1 to 5)
  and `Peer group median ERR for <M>`. FY2013 to FY2018 predate peer grouping (it began in
  FY2019). FY2027 also has dual proportion, neutrality modifier, penalty indicator and DRG
  payment ratio columns, kept in Bronze only.
- FY2027 covers two years of discharges, not three, per its variable description.

### Silver normalization

`silver_hrrp_history` columns: `facility_id`, `measure` (`AMI`, `HF`, `PN`, `COPD`, `HIP_KNEE`,
`CABG`), `fiscal_year`, `discharges`, `readmissions` (null for IPPS years),
`excess_readmission_ratio`, `predicted_rate` and `expected_rate` (null for IPPS years),
`peer_group` and `peer_median_err` (FY2027 only), `period_start`, `period_end`, `source` (`pdc`
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
