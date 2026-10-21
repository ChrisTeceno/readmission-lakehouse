# Power BI project over the Gold tables

A Power BI Project (PBIP) with the report in the PBIR format and the semantic model in TMDL,
written by hand to Microsoft's documented layout. It reads the Gold tables in
`workspace.readmission` through the Databricks connector. Contoso Health is a fictional
client: its 12 facilities are relabeled public CMS hospitals (real CCNs).

```
powerbi/
├── ContosoReadmissions.pbip                   shortcut that opens the report
├── .gitignore                                 Desktop's default: localSettings.json, cache.abf
├── ContosoReadmissions.Report/
│   ├── .platform                              Fabric Git integration identity (type Report)
│   ├── definition.pbir                        byPath reference to the semantic model
│   └── definition/                            PBIR
│       ├── version.json, report.json
│       └── pages/pages.json, <page>/page.json, <page>/visuals/<visual>/visual.json
└── ContosoReadmissions.SemanticModel/
    ├── .platform                              Fabric Git integration identity (type SemanticModel)
    ├── .pbi/editorSettings.json
    ├── definition.pbism
    └── definition/                            TMDL
        ├── database.tmdl, model.tmdl, expressions.tmdl, relationships.tmdl
        └── tables/<table>.tmdl
```

## Semantic model

| Table | Source | Grain |
|---|---|---|
| `gold_facility_condition_year` | Gold | facility, condition, fiscal year |
| `gold_penalty_mart` | Gold | facility, fiscal year |
| `gold_contoso_scorecard` | Gold | Contoso facility, condition, fiscal year |
| `gold_penalty_predictions` | Gold | facility, condition, FY2028 |
| `Fiscal Year` | built in Power Query | FY2013 to FY2028, with start and end dates, `cutoff_is_approximate` and the cutoff rule |
| `Facility` | distinct hospitals from `gold_penalty_mart` | facility (CCN), with the Contoso flag |
| `Condition` | built in Power Query | the six HRRP conditions |

Every Gold table joins many-to-one, single direction, to `Fiscal Year` (on `fiscal_year`, or
`target_fiscal_year` for predictions) and to `Facility` (on `facility_id`). The three tables with
a condition column also join to `Condition` (on `measure`). The penalty mart has one row per
hospital and year, so it has no condition key. Table and column descriptions are the Unity Catalog
comments.

Measures, each with a one-line description in the model:

| Measure | Table | Definition |
|---|---|---|
| Conditions Reported | `gold_facility_condition_year` | rows with an ERR |
| Conditions Above Cutoff | `gold_facility_condition_year` | rows with `above_cutoff` |
| Share Above Cutoff | `gold_facility_condition_year` | the two above, divided |
| Average ERR | `gold_facility_condition_year` | mean ERR |
| ERR vs Peer Cutoff | `gold_facility_condition_year` | mean of ERR minus `penalty_cutoff` |
| Anomalies Flagged | `gold_facility_condition_year` | rows with `anomaly_flag` |
| Hospitals, Hospitals Penalized, Share of Hospitals Penalized | `gold_penalty_mart` | distinct CCNs, distinct CCNs with `penalized` |
| Contoso Conditions Above Cutoff, Contoso Average ERR, Contoso ERR vs Peer Cutoff | `gold_contoso_scorecard` | as above, over Contoso's rows |
| Follow-up Completion Rate | `gold_contoso_scorecard` | completed calls over Contoso's all-payer discharges, the rule `followup_rate` uses in Gold |
| Follow-up Within 72h Rate, Calls Completed per Attempt | `gold_contoso_scorecard` | completed within 72 hours over discharges; completed over attempted |
| Predicted Conditions Above Cutoff, Average Penalty Probability | `gold_penalty_predictions` | rows scored at 0.5 or higher; mean probability |

"Peer cutoff" is exact only for FY2013 to FY2018 (1.0, the rule then) and FY2027 (the
published peer group median). For FY2019 to FY2026 it is 1.0 standing in for a median no public
file carries, so those years' cutoffs are approximate (`docs/gold.md`). Both report pages say so
in their caption.

## Report pages

The AI/BI dashboard wireframe (`engagement/wireframe.md`) was not on main when this was built, so
the pages are the two in the task card.

- **Contoso scorecard**: cards for Contoso Conditions Above Cutoff, Contoso Average ERR, Contoso ERR
  vs Peer Cutoff and Follow-up Completion Rate; Average ERR by Contoso facility (bar chart); a
  table of facility, condition, ERR, cutoff, above cutoff, approximate flag and follow-up rate; a
  fiscal-year slicer.
- **Penalty landscape**: cards for Hospitals Penalized, Share of Hospitals Penalized and Average
  ERR; Share Above Cutoff by fiscal year (line) and by condition (column); the FY2028 predictions
  ranked by `risk_rank`; a fiscal-year slicer, which leaves the predictions table alone (it holds
  FY2028 only, so any other year would empty it).

## Import, not DirectQuery

The model uses Import. The data is small: the largest table is 266,159 rows, and the whole model
is about 340,000 rows. The Gold tables change once per pipeline run, so a scheduled refresh after
the job is as current as DirectQuery would be. The warehouse is the Free Edition serverless
starter warehouse, which stops when idle and runs under a daily compute quota. DirectQuery would
start it on every page view and send one SQL query per visual, while Import starts it once per
refresh. Import also leaves the DAX unrestricted: DirectQuery limits some functions and sends the
rest to Databricks SQL.

## Connection

`expressions.tmdl` holds four Power Query parameters. The connection reads only these:

| Parameter | Default in the repo | Set it to |
|---|---|---|
| `DatabricksServerHostname` | `<workspace-host>` | your workspace's Server hostname |
| `DatabricksHttpPath` | `<warehouse-http-path>` | your SQL warehouse's HTTP path |
| `DatabricksCatalog` | `workspace` | leave |
| `DatabricksSchema` | `readmission` | leave |

Find both values in Databricks: **SQL Warehouses**, open the warehouse (here the Serverless
Starter Warehouse), **Connection details**. The hostname goes in without `https://`. No file in
`powerbi/` holds a hostname, an HTTP path or a token, and `tests/test_powerbi.py` fails if one
does.

The source is `Databricks.Catalogs`, the **Azure Databricks** connector. Microsoft's docs say to
use it "for all Databricks SQL Warehouse data" and that its personal access token sign-in works on
AWS, Azure and GCP. This workspace is Databricks on AWS (Free Edition), so authenticate with a
Databricks personal access token. Create one in Databricks under **Settings**, **Developer**,
**Access tokens**, give it a short lifetime, and type it only into Power BI's credential dialog.

## Path 1: browser only, through Fabric Git integration

This path needs no Windows. The Power BI service imports the two folders straight from this
repository. Per Microsoft's docs, Fabric Git integration supports GitHub, and its supported items
include Report and Semantic model, both in preview. Fabric's Git export writes reports as PBIR
and semantic models as TMDL, the formats used here.

What it needs:

- **A work or school account.** Power BI and Fabric do not accept personal Microsoft accounts or
  Gmail addresses. A Microsoft Entra ID user in your own Azure tenant works, for example a member
  user `you@<tenant>.onmicrosoft.com` created under Entra ID, Users. A user account is not a
  billable Azure resource.
- **A Fabric capacity.** Git integration requires one. The Fabric trial gives a 60-day trial
  capacity: sign in at app.fabric.microsoft.com, open the account manager (your picture, top
  right), and select **Start trial** (or **Free trial**).
- **Tenant settings.** In the Admin portal, **Tenant settings**, these must be on: *Users can
  create Fabric items*, *Users can synchronize workspace items with their Git repositories* and
  *Users can synchronize workspace items with GitHub repositories*. In a tenant you created
  yourself you are the admin; check these before connecting.
- **A GitHub personal access token.** A fine-grained token limited to
  `ChrisTeceno/readmission-lakehouse` with **Contents: Read and write** (Fabric requires write
  even if you only pull).

Steps:

1. In Fabric, **Workspaces**, **New workspace**. Name it (for example `Contoso Readmissions`). Under
   **Advanced**, pick the trial license mode so the workspace sits on the trial capacity.
2. Open the workspace, **Workspace settings**, **Git integration**, **GitHub**, **Add account**.
   Enter a display name and the token. Leave the repository URL empty or enter
   `https://github.com/ChrisTeceno/readmission-lakehouse`. Select **Connect**.
3. Choose branch `main` and folder `powerbi`. Select **Connect and sync**. The workspace is empty,
   so Fabric copies the items from Git into it: one semantic model and one report, both named
   `ContosoReadmissions`. Fabric reads the item folders (each has a `.platform`); the `.pbip`,
   `.gitignore` and this README at the folder root are not items.
4. Open the semantic model's **Settings**. Under **Parameters**, set `DatabricksServerHostname` and
   `DatabricksHttpPath` to your workspace's values and select **Apply**.
5. Under **Data source credentials**, select **Edit credentials** for the Databricks source.
   Choose the key (personal access token) authentication method, paste the token, set the privacy
   level to Organizational and sign in. No gateway is needed: Databricks is a cloud source.
6. Select **Refresh now** on the semantic model. When it finishes, open the report.
7. Optionally, under **Refresh**, schedule a daily refresh after the `readmission_pipeline` run.

Keep the workspace pulling, not pushing: use **Source control**, **Updates** to bring in new
commits. A **Commit** from the workspace writes Fabric's own version of these files to `main`.

If Fabric rejects the report or the model on sync (the Git item types are in preview), fall back
to the Fabric REST API, which takes the same definition parts and also runs from a Mac. Create the
semantic model with `POST /v1/workspaces/{workspaceId}/semanticModels`, sending
`definition.pbism` and each `definition/*.tmdl` file as base64 parts. Then create the report with
`POST /v1/workspaces/{workspaceId}/reports`, sending `definition.pbir` changed to a `byConnection`
reference of `semanticmodelid=<new model id>` (Microsoft's docs say REST deployments must use
`byConnection`) together with the `definition/` parts. Then do steps 4 to 6. If both fail, a
last resort works with no file import at all: in the service, create a semantic model from the
Azure Databricks connector over the four Gold tables, and rebuild the measures from the table
above.

## Path 2: Power BI Desktop on Windows

1. Install a current Power BI Desktop (Microsoft Store or the installer). PBIR is the default
   report format in current releases.
2. Clone the repository and open `powerbi/ContosoReadmissions.pbip`. Desktop opens the report and
   the semantic model. The model has no data yet, because `cache.abf` is not committed.
3. **Transform data**, **Edit parameters**: set `DatabricksServerHostname` and `DatabricksHttpPath`
   to your workspace's values.
4. **Refresh**. When Desktop asks for credentials for Azure Databricks, choose **Personal Access
   Token** and paste the token.
5. **Save**. Desktop writes the project back to the same folders; review the diff before
   committing, since Desktop adds lineage tags and some defaults.
6. To publish, **Home**, **Publish**, pick a workspace, then do steps 5 to 7 of Path 1 in the
   service.

## Checking the numbers after a refresh

Computed with SQL on the Gold tables from the 2026-10-21 run. With `FY2027` selected in the
slicer:

| Measure | Expected |
|---|---|
| Conditions Above Cutoff / Conditions Reported | 7,274 / 14,575 (Share Above Cutoff 49.9 percent) |
| Average ERR | 1.0021 |
| Hospitals Penalized / Hospitals | 2,641 / 2,912 (90.7 percent) |
| Contoso Conditions Above Cutoff | 25 of 67 conditions with an ERR |
| Contoso Average ERR | 0.9992 |
| Follow-up Completion Rate | 51.7 percent |

The FY2028 predictions table holds 17,472 rows, and Predicted Conditions Above Cutoff is 8,449.

## What was verified, and what was not

Verified on macOS:

- **Layout.** `tests/test_powerbi.py` checks both item folders against Microsoft's published
  `definitionStructure` manifests (the required and allowed files, their counts and sizes, and the
  schema version each file must name). It also checks that the report is PBIR (`definition/`, no
  root `report.json`) and the model is TMDL (`definition/`, no `model.bim`).
- **Every JSON part** (`.pbip`, `.pbir`, `.pbism`, `.platform`, `editorSettings.json`,
  `version.json`, `report.json`, `pages.json`, each `page.json` and `visual.json`) validates
  against the JSON schema its `$schema` names, using Microsoft's schemas vendored in
  `tests/fixtures/powerbi_schemas` (from github.com/microsoft/json-schemas at commit `be65eaa`,
  MIT license).
- **TMDL parses with Microsoft's own serializer.** `scripts/tmdl_check` loads the model with
  `TmdlSerializer` from `Microsoft.AnalysisServices` 19.84.1, the library behind Desktop and
  Fabric, on .NET 8 on Apple silicon. It found 7 tables, 17 measures (all with descriptions), 11
  many-to-one relationships, 5 expressions and Import partitions, and serializing the result back
  gives the same files byte for byte:

  ```
  dotnet run --project scripts/tmdl_check -- powerbi/ContosoReadmissions.SemanticModel/definition
  ```

- **References.** The tests check that every field a visual uses exists in the model as a column
  or measure of that kind, that relationships join existing columns onto key columns, that the
  model lists every table, and that page and visual names match their folders and Microsoft's
  naming rule.
- **No credentials.** The tests fail on any Databricks hostname, warehouse path, `dapi`/`dose`
  token, JWT or quoted password in `powerbi/`.
- **Data assumptions**, by SQL on the Gold tables: every `facility_id` in the facts and the
  predictions is in `gold_penalty_mart`, each hospital's name, state, type and Contoso flag are
  the same in every year (so `Facility` has one row per CCN), the facts hold the six condition
  codes, and the predictions are all for FY2028.

Not verified, because each needs Power BI Desktop (Windows only) or the Power BI service:

- Power BI Desktop or Fabric opening the project and applying the definition.
- The M queries running against Databricks, and the refresh.
- The DAX measures evaluating. The numbers above are SQL on Gold, not Power BI output.
- The visuals rendering as intended. The data role names (`Values`, `Category`, `Y`), the textbox
  paragraph format and the title formatting follow Desktop's output format but the schema leaves
  them untyped, so only a render confirms them. So does the built-in theme name `CY24SU10` and its
  `reportVersionAtImport` values.
- Fabric Git integration importing the folders, which is the main thing Path 1 exercises.

`pbi-tools` was not tried. The parse it would do on the model is the one `scripts/tmdl_check`
already runs with Microsoft's serializer.

## Microsoft docs used (read 2026-10-21)

- Power BI Desktop projects (PBIP): https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview
- Project report folder and the PBIR format: https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report
- Project semantic model folder: https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-dataset
- Tabular Model Definition Language (TMDL): https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview
- PBIP and PBIR JSON schemas: https://github.com/microsoft/json-schemas/tree/main/fabric
- Azure Databricks Power Query connector: https://learn.microsoft.com/en-us/power-query/connectors/databricks-azure
- Databricks Power Query connector (OAuth only, AWS): https://learn.microsoft.com/en-us/power-query/connectors/databricks
- Overview of Fabric Git integration: https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration
- Get started with Git integration: https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-get-started
- Git source code format (`.platform`): https://learn.microsoft.com/en-us/fabric/cicd/git-integration/source-code-format
- Deploy a PBIP project with the Fabric REST API: https://learn.microsoft.com/en-us/rest/api/fabric/articles/get-started/deploy-project
