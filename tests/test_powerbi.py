"""Offline checks for the Power BI project in powerbi/.

Power BI Desktop runs only on Windows, so these tests check what can be checked without it:
the folder layout against Microsoft's published definitionStructure manifests, every JSON part
against the JSON schema it names (vendored from github.com/microsoft/json-schemas, MIT), the
TMDL model's internal references, and that no file carries a workspace host or a credential.
"""

import json
import re
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

POWERBI = Path(__file__).parent.parent / "powerbi"
REPORT = POWERBI / "ContosoReadmissions.Report"
MODEL = POWERBI / "ContosoReadmissions.SemanticModel"
TMDL = MODEL / "definition"
SCHEMAS = Path(__file__).parent / "fixtures" / "powerbi_schemas"
SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/"
JSON_SUFFIXES = {".json", ".pbip", ".pbir", ".pbism", ".platform"}
NAME = re.compile(r"^[\w-]+$")


def _registry():
    resources = []
    for path in SCHEMAS.rglob("*.json"):
        contents = json.loads(path.read_text())
        resource = Resource.from_contents(contents, default_specification=DRAFT7)
        resources.append((SCHEMA_BASE + path.relative_to(SCHEMAS).as_posix(), resource))
        if "$id" in contents:
            resources.append((contents["$id"], resource))
    return Registry().with_resources(resources)


REGISTRY = _registry()


def _json_parts():
    return sorted(
        p for p in POWERBI.rglob("*") if p.is_file() and (p.suffix or p.name) in JSON_SUFFIXES
    )


def _manifest(rel):
    return json.loads((SCHEMAS / "fabric" / "item" / rel).read_text())


def _matches(root, pattern):
    if pattern.endswith("/**"):
        base = root / pattern[:-3]
        return [p for p in base.rglob("*") if p.is_file()] if base.exists() else []
    return [p for p in root.glob(pattern) if p.is_file()]


def _tmdl_tables():
    """{table name: {"columns": set, "measures": set, "text": str}} from tables/*.tmdl."""
    tables = {}
    for path in (TMDL / "tables").glob("*.tmdl"):
        text = path.read_text()
        name = re.search(r"^table (?:'([^']+)'|(\S+))$", text, re.MULTILINE)
        name = name.group(1) or name.group(2)
        columns = {
            a or b for a, b in re.findall(r"^\tcolumn (?:'([^']+)'|(\S+))$", text, re.MULTILINE)
        }
        measures = {
            a or b for a, b in re.findall(r"^\tmeasure (?:'([^']+)'|(\S+)) =", text, re.MULTILINE)
        }
        tables[name] = {"columns": columns, "measures": measures, "text": text, "file": path}
    return tables


def _unquote(ref):
    table, _, column = ref.rpartition(".")
    return table.strip("'"), column.strip("'")


def test_project_root_has_the_documented_files():
    assert sorted(p.name for p in POWERBI.iterdir()) == [
        ".gitignore",
        "ContosoReadmissions.Report",
        "ContosoReadmissions.SemanticModel",
        "ContosoReadmissions.pbip",
        "README.md",
    ]
    pbip = json.loads((POWERBI / "ContosoReadmissions.pbip").read_text())
    assert pbip["artifacts"] == [{"report": {"path": "ContosoReadmissions.Report"}}]
    ignored = (POWERBI / ".gitignore").read_text().split()
    assert ignored == ["**/.pbi/localSettings.json", "**/.pbi/cache.abf"]


def test_report_is_pbir_bound_to_the_local_model():
    pbir = json.loads((REPORT / "definition.pbir").read_text())
    assert pbir["datasetReference"] == {"byPath": {"path": "../ContosoReadmissions.SemanticModel"}}
    # PBIR needs definition.pbir version 4.0 or higher; report.json at the root is PBIR-Legacy.
    assert float(pbir["version"]) >= 4.0
    assert not (REPORT / "report.json").exists()
    pbism = json.loads((MODEL / "definition.pbism").read_text())
    # TMDL needs definition.pbism version 4.0 or higher; model.bim is the TMSL format.
    assert float(pbism["version"]) >= 4.0
    assert not (MODEL / "model.bim").exists()


@pytest.mark.parametrize(
    "root, manifest",
    [
        (REPORT, "report/definition/definitionStructure/3.3.0/definitionStructure.json"),
        (MODEL, "semanticModel/definitionStructure/1.0.0/definitionStructure.json"),
    ],
)
def test_layout_matches_the_published_definition_structure(root, manifest):
    spec = _manifest(manifest)
    covered = set()
    for entry in spec["files"]:
        found = _matches(root, entry["path"])
        covered.update(found)
        assert entry["minCount"] <= len(found) <= entry["maxCount"], entry["path"]
        for path in found:
            assert path.stat().st_size <= entry["maxSize"], path
            if entry["type"] == "json":
                named = json.loads(path.read_text())["$schema"]
                assert named in [ref["uri"] for ref in entry["schemaRef"]], path
    platform = root / ".platform"
    assert platform.exists()
    stray = {p for p in root.rglob("*") if p.is_file()} - covered - {platform}
    assert not stray, sorted(str(p) for p in stray)


@pytest.mark.parametrize("path", _json_parts(), ids=lambda p: str(p.relative_to(POWERBI)))
def test_json_part_matches_the_schema_it_names(path):
    document = json.loads(path.read_text())
    uri = document["$schema"]
    assert uri.startswith(SCHEMA_BASE)
    schema = REGISTRY.get_or_retrieve(uri).value.contents
    validator = jsonschema.Draft7Validator(schema, registry=REGISTRY)
    errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(document)]
    assert not errors


def test_platform_files_identify_each_item_once():
    ids = set()
    for root, kind in [(REPORT, "Report"), (MODEL, "SemanticModel")]:
        platform = json.loads((root / ".platform").read_text())
        assert platform["metadata"]["type"] == kind
        assert platform["metadata"]["displayName"] == "ContosoReadmissions"
        assert re.fullmatch(
            r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", platform["config"]["logicalId"]
        )
        ids.add(platform["config"]["logicalId"])
    assert len(ids) == 2


def test_pages_and_visuals_follow_the_naming_convention():
    pages = REPORT / "definition" / "pages"
    meta = json.loads((pages / "pages.json").read_text())
    folders = sorted(p.name for p in pages.iterdir() if p.is_dir())
    assert sorted(meta["pageOrder"]) == folders
    assert meta["activePageName"] in folders
    displays = []
    for folder in folders:
        page = json.loads((pages / folder / "page.json").read_text())
        assert page["name"] == folder and NAME.match(folder)
        displays.append(page["displayName"])
        visuals = {p.name for p in (pages / folder / "visuals").iterdir()}
        for visual in visuals:
            container = json.loads(
                (pages / folder / "visuals" / visual / "visual.json").read_text()
            )
            assert container["name"] == visual and NAME.match(visual)
            pos = container["position"]
            assert pos["x"] + pos["width"] <= page["width"]
            assert pos["y"] + pos["height"] <= page["height"]
        for interaction in page.get("visualInteractions", []):
            assert {interaction["source"], interaction["target"]} <= visuals
    assert sorted(displays) == ["Contoso scorecard", "Penalty landscape"]


def _field_refs(node):
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            if kind in node and "Property" in node[kind]:
                yield kind, node[kind]["Expression"]["SourceRef"]["Entity"], node[kind]["Property"]
        for value in node.values():
            yield from _field_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _field_refs(value)


def test_every_visual_field_exists_in_the_model():
    tables = _tmdl_tables()
    refs = set()
    for path in (REPORT / "definition" / "pages").rglob("visual.json"):
        refs.update(_field_refs(json.loads(path.read_text())))
    assert refs
    for kind, table, name in refs:
        assert table in tables, table
        assert name in tables[table]["columns" if kind == "Column" else "measures"], (table, name)


def test_model_lists_every_table_and_relationships_join_existing_columns():
    tables = _tmdl_tables()
    model = (TMDL / "model.tmdl").read_text()
    listed = {
        a or b for a, b in re.findall(r"^ref table (?:'([^']+)'|(\S+))$", model, re.MULTILINE)
    }
    assert listed == set(tables)
    gold = {
        "gold_contoso_scorecard",
        "gold_penalty_mart",
        "gold_facility_condition_year",
        "gold_penalty_predictions",
    }
    assert gold <= set(tables)
    relationships = (TMDL / "relationships.tmdl").read_text()
    pairs = re.findall(r"fromColumn: (.+)\n\ttoColumn: (.+)", relationships)
    assert len(pairs) == relationships.count("relationship ")
    targets = set()
    for source, target in pairs:
        for ref in (source, target):
            table, column = _unquote(ref)
            assert column in tables[table]["columns"], ref
        targets.add(_unquote(target)[0])
        assert "isKey" in tables[_unquote(target)[0]]["text"]
    assert targets == {"Fiscal Year", "Facility", "Condition"}
    for table in gold:
        assert table in {_unquote(s)[0] for s, _ in pairs}


def test_every_measure_has_a_description():
    tables = _tmdl_tables()
    count = 0
    for table in tables.values():
        lines = table["text"].splitlines()
        for i, line in enumerate(lines):
            if line.startswith("\tmeasure "):
                count += 1
                assert lines[i - 1].startswith("\t/// "), line
    assert count >= 4
    names = set().union(*(t["measures"] for t in tables.values()))
    for required in [
        "Conditions Above Cutoff",
        "Average ERR",
        "ERR vs Peer Cutoff",
        "Follow-up Completion Rate",
    ]:
        assert required in names


def test_connection_is_parameterized_and_import_mode():
    expressions = (TMDL / "expressions.tmdl").read_text()
    for parameter in ("DatabricksServerHostname", "DatabricksHttpPath"):
        value = re.search(
            rf'^expression {parameter} = "([^"]*)" meta \[IsParameterQuery=true',
            expressions,
            re.MULTILINE,
        )
        assert value and re.fullmatch(r"<[a-z-]+>", value.group(1)), parameter
    assert "Databricks.Catalogs(DatabricksServerHostname, DatabricksHttpPath" in expressions
    for table in _tmdl_tables().values():
        assert re.findall(r"^\t\tmode: (\S+)$", table["text"], re.MULTILINE) == ["import"], table[
            "file"
        ]


def test_tmdl_indents_with_tabs():
    for path in TMDL.rglob("*.tmdl"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            assert not line.startswith(" "), f"{path.name}:{number}"


SECRET_PATTERNS = [
    r"\.cloud\.databricks\.com",
    r"\.azuredatabricks\.net",
    r"\.gcp\.databricks\.com",
    r"dbc-[0-9a-f]{8}-[0-9a-f]{4}",
    r"/sql/1\.0/warehouses/[0-9a-f]{6,}",
    r"dapi[0-9a-f]{32}",
    r"dose[0-9a-f]{32}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"(?i)(token|password|secret|pwd)\s*[=:]\s*[\"'][^\"'<]",
]


def test_no_file_carries_a_workspace_host_or_credential():
    files = [p for p in POWERBI.rglob("*") if p.is_file()]
    assert len(files) > 20
    for path in files:
        text = path.read_text()
        for pattern in SECRET_PATTERNS:
            assert not re.search(pattern, text), (path, pattern)
