# Governance

How the Silver tables in `workspace.readmission` are documented, tagged, constrained and
shared. Code: `src/readmission/silver/governance.py`, `src/readmission/quality/`, and the
`silver` task (`readmission.silver.build`) of the `readmission_pipeline` job.

## Silver tables

| Table | Grain | Built from |
|---|---|---|
| `silver_hrrp` | facility, measure (current HRRP fiscal year) | `bronze_hrrp` |
| `silver_hrrp_history` | facility, measure, fiscal year (FY2013 to FY2027) | `bronze_hrrp_history_pdc`, `bronze_hrrp_history_ipps` |
| `silver_hospital_info` | facility | `bronze_hospital_info` |
| `silver_unplanned_visits` | facility, measure id | `bronze_unplanned_visits` |
| `silver_clinical_notes` | note id | `bronze_clinical_notes` |
| `silver_acs_county` | county FIPS, ACS vintage (2011 to 2024) | `bronze_acs_county` |
| `silver_hospital_county` | facility, ACS vintage | `silver_hospital_info`, `bronze_acs_county` |
| `silver_quarantine` | one row per rejected Bronze row | all of the above |

Every run rebuilds each table from Bronze. Each table has an explicit `StructType` in
`src/readmission/silver/`; the table is created from it, so its NOT NULL columns and column
comments come from the code. Duplicate business keys keep the row from the latest `_batch_id`;
history keeps each fiscal year's latest batch.

## Comments and tags

Every Silver table carries a table comment (what it holds and its source dataset) and a comment
on every column (meaning and units: ratios, percents, days per 100 discharges). The job
re-applies them on every run, so the text in the schema is what the catalog shows.

Tags on every Silver table:

| Tag | Value |
|---|---|
| `layer` | `silver` |
| `contains_phi` | `false`: CMS data is facility level, and MTSamples notes are public de-identified samples |
| `source` | `cms`, `mtsamples`, `census`, or a comma-separated list for tables built from several |
| `dataset` | CMS dataset id where there is one (`9n3s-kdb3`, `xubh-q36u`, `632h-zaca`); `acs5-profile` for Census |

## Constraints, quarantine and expectations

Delta CHECK constraints (`quality/constraints.py`):

| Constraint | Expression | Tables |
|---|---|---|
| `ccn_format` | `facility_id RLIKE '^[0-9A-Z]{6}$'` | all CMS tables |
| `err_positive` | `excess_readmission_ratio IS NULL OR excess_readmission_ratio > 0` | `silver_hrrp`, `silver_hrrp_history` |
| `period_order` | `period_end >= period_start` | `silver_hrrp`, `silver_hrrp_history`, `silver_unplanned_visits`, `silver_acs_county` |
| `county_fips_format` | `county_fips IS NULL OR county_fips RLIKE '^[0-9]{5}$'` | `silver_acs_county`, `silver_hospital_county` |
| `acs_percent_range` | each percent column `IS NULL OR BETWEEN 0 AND 100` | `silver_acs_county` |

Delta rejects a row whose CHECK expression evaluates to null (the first ACS run on 2026-10-21
failed on the 2011 vintage's null uninsured percent), so a constraint on a nullable column
must say `IS NULL OR` explicitly. Changing a constraint's expression on an existing table needs
`ALTER TABLE ... DROP CONSTRAINT` first: the build adds only constraints missing by name.

The build evaluates the same expressions before writing. A row that breaks one, misses a key,
or holds a value that does not parse (anything other than a number, date or CMS suppression
code where one is expected) goes to `silver_quarantine` with its table, business key, Bronze row
as JSON, the rule it broke and its batch id. Quarantine never fails the run.

Three expectations fail the run (`quality/expectations.py`), before the table is overwritten,
so the previous version stays in place:

- Row count: more than a 20 percent drop from the previous build. No fiscal year of HRRP lost 5
  percent of its hospitals, so a larger drop means a truncated file or a format change.
- ERR null rate above 45 percent, on `silver_hrrp` and `silver_hrrp_history`. CMS leaves the
  ratio blank for hospitals under 25 cases: 36.1 percent of FY2026 rows, the most of any year.
- County match rate below 98 percent of hospitals in the latest ACS vintage, on
  `silver_hospital_county`. Every hospital matched on 2026-10-21, so a drop means new county
  spellings or a Census geography change that needs a rule in `silver/county_match.py`.

## Analyst access

The intended grant, applied to every Silver table:

```sql
GRANT SELECT ON TABLE workspace.readmission.<table> TO `analysts`;
```

It does not run on this workspace. Unity Catalog grants go to account-level principals, and
Databricks Free Edition has no account console in which to create an account group. A
workspace-local group can be created (`databricks groups create --display-name analysts`
succeeded on 2026-10-21), but Unity Catalog does not see it: the grant failed with
`PRINCIPAL_DOES_NOT_EXIST`, so the local group was deleted again. The job still issues the
grant on every run and logs it as skipped when the principal is missing, so the statement takes
effect unchanged on a workspace with an account-level `analysts` group. A real deployment would
also grant `USE CATALOG` on `workspace` and `USE SCHEMA` on `workspace.readmission` to the same
group.
