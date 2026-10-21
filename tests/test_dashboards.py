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
    "Pipeline health",
]
TABLES = {
    "gold_contoso_scorecard",
    "gold_facility_condition_year",
    "gold_penalty_mart",
    "gold_penalty_predictions",
    "ops_dq_metrics",
    "ops_run_log",
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
            used += [e["fieldName"] for k, e in encodings.items() if k != "columns"]
            assert set(used) <= selected, w["name"]


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
        assert referenced <= TABLES, (name, referenced - TABLES)


def test_pages_caption_the_approximate_cutoff_and_the_model(dashboard):
    for name in ("Contoso scorecard", "National penalty landscape"):
        assert "FY2019 to FY2026 are approximate" in page_text(page(dashboard, name))
    predictions = page_text(page(dashboard, "Predictions"))
    assert "probabilities" in predictions and "0.678" in predictions
    assert "fictional client" in page_text(page(dashboard, "Contoso scorecard"))


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


def test_genie_instructions_flag_approximate_cutoffs_and_fiction(genie):
    text = genie["instructions"]["text_instructions"][0]["content"][0]
    assert "FY2019 to FY2026" in text and "cutoff_is_approximate" in text
    assert "fictional" in text
