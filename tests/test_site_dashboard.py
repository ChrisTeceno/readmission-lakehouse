"""The public static dashboard (site/dashboard) against its committed Gold snapshot.

The export's warehouse queries are not run here; these tests read the committed JSON.
"""

import importlib.util
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from readmission.ingest.contoso import FACILITIES

ROOT = Path(__file__).parent.parent
SITE = ROOT / "site" / "dashboard"
DATA = SITE / "data"
HTML = (SITE / "index.html").read_text()
JS = (SITE / "js" / "dashboard.js").read_text()
CSS = (SITE / "css" / "dashboard.css").read_text()

CONTOSO_CCNS = {f.ccn for f in FACILITIES}
CONTOSO_IDS = {f.facility_id for f in FACILITIES}
# Columns that would name a hospital, or say where it is.
NAME_COLUMN = re.compile(r"(^|_)(name|facility|hospital|city|address|county|zip)($|_)")
# hospital_group is "Contoso", "Ohio" or "All hospitals", never one hospital; hospital_net is
# Contoso's dollars across all 12 facilities.
NOT_A_HOSPITAL = {"model_name", "table_name", "hospital_group", "hospital_net"}


def load_export():
    spec = importlib.util.spec_from_file_location(
        "export_dashboard_data", ROOT / "scripts" / "export_dashboard_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data():
    return {path.stem: json.loads(path.read_text()) for path in sorted(DATA.glob("*.json"))}


def test_export_writes_every_file_and_dataset_the_page_reads(data):
    export = load_export()
    assert set(data) == set(export.QUERIES) | {"meta"}
    for page, datasets in export.QUERIES.items():
        assert set(data[page]) == set(datasets), page
    files = json.loads(re.search(r"const FILES = (\[[^\]]*\]);", JS).group(1))
    assert set(files) == set(data)
    used = set(re.findall(r"\bD\.(\w+)\.(\w+)", JS))
    assert used
    missing = [f"{file}.{name}" for file, name in used if name not in data.get(file, {})]
    assert not missing, f"the page reads datasets the export does not write: {missing}"


def test_fields_the_page_reads_exist_in_the_data(data):
    # Every `key: "..."` column the page tables show is a field of some exported row.
    fields = {
        key for file in data.values() for rows in file.values() for row in rows for key in row
    }
    keys = set(re.findall(r'\{ key: "(\w+)"', JS))
    assert keys
    assert keys <= fields, f"table columns with no exported field: {keys - fields}"


def test_snapshot_is_small(data):
    total = sum(path.stat().st_size for path in DATA.glob("*.json"))
    assert total < 1_000_000


def test_no_hospital_name_outside_contoso_rows(data):
    offending = []
    for file, datasets in data.items():
        for name, rows in datasets.items():
            for row in rows:
                columns = [c for c in row if NAME_COLUMN.search(c) and c not in NOT_A_HOSPITAL]
                if not columns:
                    continue
                contoso = row.get("contoso_facility_id") in CONTOSO_IDS and (
                    "ccn" not in row or row["ccn"] in CONTOSO_CCNS
                )
                if not contoso:
                    offending.append(f"{file}.{name}: {columns} in {row}")
    assert not offending, offending[:5]


def test_contoso_names_and_ccns_match_the_seed(data):
    names = {f.facility_id: f"{f.facility_id} {f.facility_name}" for f in FACILITIES}
    ccns = {f.facility_id: f.ccn for f in FACILITIES}
    seen = 0
    for datasets in data.values():
        for rows in datasets.values():
            for row in rows:
                if "facility" in row:
                    assert row["facility"] == names[row["contoso_facility_id"]]
                    assert row["ccn"] == ccns[row["contoso_facility_id"]]
                    seen += 1
    assert seen


def test_every_ccn_in_the_snapshot_is_contoso(data):
    text = "".join(path.read_text() for path in DATA.glob("*.json"))
    assert set(re.findall(r'"ccn":"(\d+)"', text)) == CONTOSO_CCNS


def test_snapshot_carries_no_secrets_or_paths(data):
    text = "".join(path.read_text() for path in DATA.glob("*.json"))
    for pattern in [r"dapi[0-9a-f]{8}", r"[a-z0-9]+://", r"Bearer ", r"cloud\.databricks\.com"]:
        assert not re.search(pattern, text), pattern


def test_scorecard_summary_agrees_with_its_rows(data):
    rows = data["scorecard"]["scorecard"]
    by_year = defaultdict(list)
    for row in rows:
        by_year[row["fiscal_year"]].append(row)
    for summary in data["scorecard"]["scorecard_summary"]:
        year = by_year[summary["fiscal_year"]]
        above = [r for r in year if r["above_cutoff"]]
        assert summary["facilities"] == len({r["contoso_facility_id"] for r in year})
        assert summary["facilities_above"] == len({r["contoso_facility_id"] for r in above})
        assert summary["conditions_above"] == len(above)
        assert summary["pairs_reported"] == sum(r["err"] is not None for r in year)


def test_fy2027_counts_match_the_readme(data):
    fy2027 = next(r for r in data["scorecard"]["scorecard_summary"] if r["fiscal_year"] == 2027)
    assert (fy2027["facilities_above"], fy2027["facilities"]) == (10, 12)
    assert (fy2027["conditions_above"], fy2027["pairs_reported"]) == (25, 67)
    readme = (ROOT / "README.md").read_text()
    assert "10 of Contoso's 12 facilities and 25 of its 67" in readme
    # The page builds the counters from these fields, as "x of y".
    assert "${s.facilities_above} of ${s.facilities}" in JS
    assert "${s.conditions_above} of ${s.pairs_reported}" in JS


def test_penalty_totals_agree_across_datasets_and_docs(data):
    penalty = data["penalty"]
    summary = penalty["penalty_summary"][0]
    for year in (2026, 2027):
        rows = [r for r in penalty["penalty_dollars"] if r["fiscal_year"] == year]
        assert len(rows) == 12
        assert all(r["is_estimate"] for r in rows)
        assert sum(r["est_penalty"] for r in rows) == pytest.approx(
            summary[f"est_penalty_fy{year}"], abs=12
        )
        assert sum(r[f"est_penalty_fy{year}"] for r in penalty["penalty_change"]) == pytest.approx(
            summary[f"est_penalty_fy{year}"], abs=40
        )
    docs = (ROOT / "docs" / "penalty-dollars.md").read_text()
    assert f"${summary['est_penalty_fy2026']:,.0f}" in docs
    assert f"${summary['est_penalty_fy2027']:,.0f}" in docs
    assert (summary["break_even_full"], summary["break_even_half"]) == (0.36, 0.71)


def test_page_keeps_contoso_ledger_and_payer_savings_apart():
    ledger = JS[JS.index('table("pd-ledger"') : JS.index('table("pd-payer"')]
    payer = JS[JS.index('table("pd-payer"') :]
    assert "hospital_net" in ledger and "payer_savings" not in ledger
    assert "payer_savings" in payer and "hospital_net" not in payer
    assert "never added to Contoso's net" in HTML


def test_page_labels_dollars_as_estimates_and_carries_the_timing():
    assert "Every dollar figure here is an estimate" in HTML
    for phrase in ["FY2026", "The bill paid", "being paid now", "Mostly baked in", "FY2029 on"]:
        assert phrase in HTML
    money_columns = re.findall(r'title: "([^"]+)", fmt: fmt\.money', JS)
    assert money_columns
    assert all("(est.)" in title for title in money_columns), money_columns


def test_page_notes_and_links():
    assert (
        "Contoso Health is fictional; public CMS data and synthetic records; no patient data"
        in HTML
    )
    assert 'href="https://github.com/ChrisTeceno/readmission-lakehouse"' in HTML
    assert "approximate" in HTML and "1.0 stands in" in HTML
    assert "m.gold_job_run_id" in JS and "m.exported_at" in JS


def test_page_is_self_contained_with_a_pinned_plotly():
    scripts = re.findall(r'<script src="([^"]+)"', HTML)
    assert scripts[0].startswith("https://cdnjs.cloudflare.com/ajax/libs/plotly.js/")
    assert re.search(r"/plotly\.js/\d+\.\d+\.\d+/", scripts[0])
    assert 'integrity="sha512-' in HTML
    local = [s for s in scripts[1:]] + re.findall(r'<link rel="stylesheet" href="([^"]+)"', HTML)
    for path in local:
        assert not path.startswith(("/", "http")), path
        assert (SITE / path).exists(), path
    assert "fetch(`data/${name}.json`)" in JS
    assert not re.search(r"""["'`]/(data|js|css)/""", JS + HTML)


def test_page_supports_dark_mode_and_one_accent():
    assert "@media (prefers-color-scheme: dark)" in CSS
    assert ':root[data-theme="dark"]' in CSS
    assert "responsive: true" in JS
    # Plotly colors come from the CSS tokens, never from hard-coded hex values.
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", JS)
