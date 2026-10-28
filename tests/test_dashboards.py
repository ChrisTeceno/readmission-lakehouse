import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
DASHBOARD = ROOT / "src" / "dashboards" / "readmission.lvdash.json"
GENIE = ROOT / "src" / "genie" / "readmission.geniespace.json"
WIREFRAME = ROOT / "engagement" / "wireframe.md"

PAGES = [
    "Contoso scorecard",
    "National penalty landscape",
    "Predictions",
    "Follow-up operations",
    "Penalty dollars and follow-up calls",
    "Pipeline health",
]
# The tables the Genie space reads; the dashboard also reads the penalty dollar tables.
TABLES = {
    "gold_contoso_scorecard",
    "gold_facility_condition_year",
    "gold_penalty_mart",
    "gold_penalty_predictions",
    "ops_dq_metrics",
    "ops_run_log",
}
DASHBOARD_TABLES = TABLES | {
    "gold_nurse_call_economics",
    "gold_penalty_by_condition",
    "gold_penalty_dollars",
}


@pytest.fixture(scope="module")
def dashboard():
    return json.loads(DASHBOARD.read_text())


@pytest.fixture(scope="module")
def genie():
    return json.loads(GENIE.read_text())


def widgets(page):
    return [item["widget"] for item in page["layout"]]


def page_text(page):
    return " ".join(
        "".join(w["multilineTextboxSpec"]["lines"])
        for w in widgets(page)
        if "multilineTextboxSpec" in w
    )


def page(dashboard, name):
    return next(p for p in dashboard["pages"] if p["displayName"] == name)


def dataset_sql(dashboard):
    return {ds["name"]: "".join(ds["queryLines"]) for ds in dashboard["datasets"]}


def test_dashboard_has_the_wireframe_pages_in_order(dashboard):
    assert [p["displayName"] for p in dashboard["pages"]] == PAGES


def test_every_widget_query_names_a_dataset_and_its_fields_exist(dashboard):
    sql = dataset_sql(dashboard)
    for p in dashboard["pages"]:
        for w in widgets(p):
            for query in w.get("queries", []):
                name = query["query"]["datasetName"]
                assert name in sql, (w["name"], name)
                for field in query["query"].get("fields", []):
                    for column in re.findall(r"`([a-z_0-9]+)`", field["expression"]):
                        if column != "associative_filter_predicate_group":
                            assert re.search(rf"\b{column}\b", sql[name]), (w["name"], column)


def test_widget_encodings_use_fields_their_query_selects(dashboard):
    for p in dashboard["pages"]:
        for w in widgets(p):
            if "spec" not in w or w["spec"]["widgetType"].startswith("filter"):
                continue
            selected = {f["name"] for q in w["queries"] for f in q["query"]["fields"]}
            encodings = w["spec"]["encodings"]
            used = [e["fieldName"] for e in encodings.get("columns", [])]
            used += [e["fieldName"] for e in encodings.values() if isinstance(e, dict)]
            assert set(used) <= selected, w["name"]


def test_table_widgets_use_the_spec_the_importer_accepts(dashboard):
    # Version 1 tables import as "Invalid widget definition is imported" in the published view.
    for p in dashboard["pages"]:
        for w in widgets(p):
            spec = w.get("spec")
            if not spec or spec["widgetType"] != "table":
                continue
            assert spec["version"] == 2, w["name"]
            encodings = spec["encodings"]
            assert set(encodings) == {
                "columns",
                "itemsPerPage",
                "paginationSize",
                "condensed",
                "withRowNumber",
            }, w["name"]
            for column in encodings["columns"]:
                assert {"fieldName", "type", "displayAs", "visible", "order", "title"} <= set(
                    column
                ), (w["name"], column["fieldName"])


def test_mean_err_counter_shows_three_decimals(dashboard):
    counter = next(
        w for w in widgets(page(dashboard, "Contoso scorecard")) if w["name"] == "sc_mean_err"
    )
    places = counter["spec"]["encodings"]["value"]["format"]["decimalPlaces"]
    assert places == {"type": "exact", "places": 3}


def test_widget_names_are_unique_and_layouts_fit_the_grid(dashboard):
    names = [w["name"] for p in dashboard["pages"] for w in widgets(p)]
    assert len(names) == len(set(names))
    for p in dashboard["pages"]:
        for item in p["layout"]:
            position = item["position"]
            assert position["x"] + position["width"] <= 6, item["widget"]["name"]


def test_fiscal_year_parameter_defaults_to_the_exact_cutoff_year(dashboard):
    for ds in dashboard["datasets"]:
        uses = ":fiscal_year" in "".join(ds["queryLines"])
        params = {p["keyword"]: p for p in ds.get("parameters", [])}
        assert uses == ("fiscal_year" in params), ds["name"]
        if uses:
            default = params["fiscal_year"]["defaultSelection"]["values"]["values"]
            assert default == [{"value": "2027"}]


def test_datasets_read_only_gold_and_ops_tables_without_a_catalog(dashboard):
    for name, sql in dataset_sql(dashboard).items():
        ctes = set(re.findall(r"\b([a-z_]+) AS \(", sql))
        referenced = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_.]+)", sql)) - ctes
        assert referenced <= DASHBOARD_TABLES, (name, referenced - DASHBOARD_TABLES)


def test_pages_caption_the_cutoff_rule_and_the_model(dashboard):
    for name in ("Contoso scorecard", "National penalty landscape"):
        text = page_text(page(dashboard, name))
        assert "The only rows on a 1.0 stand-in" in text and "FY2023 pneumonia" in text
    predictions = page_text(page(dashboard, "Predictions"))
    assert "probabilities" in predictions and "0.679" in predictions
    assert "fictional client" in page_text(page(dashboard, "Contoso scorecard"))


def test_penalty_dollars_page_labels_estimates_and_keeps_the_ledgers_apart(dashboard):
    p = page(dashboard, "Penalty dollars and follow-up calls")
    text = page_text(p)
    assert "estimate" in text and "Table 1B" in text and "fictional client" in text
    assert "never added" in text and "0.53 to 1.05" in text and "3.44" in text
    kinds = [w["spec"]["widgetType"] for w in widgets(p) if "spec" in w]
    assert kinds.count("bar") == 2 and kinds.count("table") == 2 and kinds.count("counter") == 4
    assert "names the year the penalty is paid" in text and "FY2029 to FY2031" in text
    titles = [w["spec"]["frame"]["title"] for w in widgets(p) if "spec" in w]
    assert any("FY2026" in t for t in titles) and any("FY2027" in t for t in titles)
    table = next(w for w in widgets(p) if w["name"] == "pd_scenarios")
    headers = [c["title"] for c in table["spec"]["encodings"]["columns"]]
    assert "Payer savings, not Contoso's (est.)" in headers
    assert all("(est.)" in h for h in headers if h.endswith(("cost (est.)", "net (est.)")))


def test_penalty_datasets_show_only_contoso_names(dashboard):
    for name, sql in dataset_sql(dashboard).items():
        if re.search(r"gold_(penalty_dollars|penalty_by_condition|nurse_call_economics)", sql):
            assert not re.search(r"(?<!contoso_)facility_name", sql), name


@pytest.mark.parametrize(
    "resource, source",
    [("dashboard.yml", DASHBOARD), ("genie.yml", GENIE)],
)
def test_bundle_resources_point_at_the_files_and_warehouse(resource, source):
    text = (ROOT / "resources" / resource).read_text()
    (file_path,) = re.findall(r"^\s+file_path: (\S+)$", text, re.MULTILINE)
    assert (ROOT / "resources" / file_path).resolve() == source
    assert "warehouse_id: ${var.warehouse_id}" in text


def test_genie_space_follows_the_api_ordering_and_id_rules(genie):
    ids = []
    for item in genie["config"]["sample_questions"]:
        ids.append(item["id"])
    instructions = genie["instructions"]
    for key in ("text_instructions", "example_question_sqls"):
        items = instructions.get(key, [])
        assert [i["id"] for i in items] == sorted(i["id"] for i in items), key
        ids += [i["id"] for i in items]
    questions = genie["config"]["sample_questions"]
    assert [q["id"] for q in questions] == sorted(q["id"] for q in questions)
    assert all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)
    assert len(ids) == len(set(ids))
    assert len(instructions["text_instructions"]) == 1

    tables = genie["data_sources"]["tables"]
    identifiers = [t["identifier"] for t in tables]
    assert identifiers == sorted(identifiers)
    assert {i.removeprefix("workspace.readmission.") for i in identifiers} == TABLES
    for table in tables:
        columns = [c["column_name"] for c in table.get("column_configs", [])]
        assert columns == sorted(columns), table["identifier"]


def test_genie_sample_questions_are_the_wireframe_questions(genie):
    wireframe = WIREFRAME.read_text()
    questions = [q["question"][0] for q in genie["config"]["sample_questions"]]
    assert len(questions) == 5
    for question in questions:
        assert question in wireframe


def test_genie_instructions_flag_stand_in_cutoffs_and_fiction(genie):
    text = genie["instructions"]["text_instructions"][0]["content"][0]
    assert "FY2023 pneumonia" in text and "cutoff_is_approximate" in text
    assert "fictional" in text
