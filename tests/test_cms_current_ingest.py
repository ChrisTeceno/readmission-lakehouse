import json
from pathlib import Path

import httpx

from readmission.ingest import cms
from readmission.ingest.cms_current import (
    HOSPITAL_INFO_SOURCE,
    HRRP_SOURCE,
    UNPLANNED_VISITS_SOURCE,
    fetch_dataset,
    read_csv_rows,
)
from readmission.ingest.fetch import SOURCES, main
from readmission.ingest.landing import LocalLander, land_manifest, list_manifests, sha256_hex

CMS_FIXTURES = Path(__file__).parent / "fixtures" / "cms"
FIXTURES = Path(__file__).parent / "fixtures" / "cms_current"

HRRP_METADATA_URL = f"{cms.BASE_URL}/metastore/schemas/dataset/items/{cms.HRRP}"


def hrrp_server(calls: list, modified: str | None = None) -> httpx.MockTransport:
    """Serves the recorded HRRP metadata and a CSV cut from the real download."""
    metadata = json.loads((CMS_FIXTURES / "hrrp_metadata.json").read_text())
    if modified:
        metadata["modified"] = modified
    csv_url = metadata["distribution"][0]["downloadURL"]
    files = {HRRP_METADATA_URL: json.dumps(metadata).encode(), csv_url: (FIXTURES / "hrrp.csv")}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = files.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body if isinstance(body, bytes) else body.read_bytes())

    return httpx.MockTransport(handler)


def test_fetch_lands_dataset_csv_with_manifest(tmp_path):
    calls = []
    client = httpx.Client(transport=hrrp_server(calls))
    lander = LocalLander(str(tmp_path / "landing"))

    landed = fetch_dataset(client, lander, "b1", tmp_path / "work", source=HRRP_SOURCE)

    name = "FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv"
    csv_bytes = (FIXTURES / "hrrp.csv").read_bytes()
    assert len(landed) == 1
    entry = landed[0]
    assert entry["path"] == str(tmp_path / "landing" / "cms_hrrp" / "b1" / name)
    assert Path(entry["path"]).read_bytes() == csv_bytes
    assert entry["name"] == name
    assert entry["url"].endswith(f"/{name}")
    assert entry["dataset_id"] == "9n3s-kdb3"
    assert entry["modified"] == "2026-01-26"
    assert entry["sha256"] == sha256_hex(csv_bytes)
    assert entry["row_count"] == 6
    assert list_manifests(str(tmp_path / "landing"), "cms_hrrp")[0]["files"] == [entry]


def test_fetch_skips_when_modified_matches_latest_manifest(tmp_path):
    lander = LocalLander(str(tmp_path))
    land_manifest(lander, "cms_hrrp", "b1", [{"name": "a.csv", "modified": "2025-07-09"}])
    land_manifest(lander, "cms_hrrp", "b2", [{"name": "a.csv", "modified": "2026-01-26"}])
    calls = []
    client = httpx.Client(transport=hrrp_server(calls))

    landed = fetch_dataset(client, lander, "b3", tmp_path / "work", source=HRRP_SOURCE)

    assert landed == []
    assert calls == [HRRP_METADATA_URL]
    assert [m["batch_id"] for m in list_manifests(str(tmp_path), "cms_hrrp")] == ["b1", "b2"]


def test_fetch_lands_again_when_cms_modifies_the_dataset(tmp_path):
    lander = LocalLander(str(tmp_path))
    land_manifest(lander, "cms_hrrp", "b1", [{"name": "a.csv", "modified": "2026-01-26"}])
    client = httpx.Client(transport=hrrp_server([], modified="2026-11-01"))

    landed = fetch_dataset(client, lander, "b2", tmp_path / "work", source=HRRP_SOURCE)

    assert [e["modified"] for e in landed] == ["2026-11-01"]
    assert [m["batch_id"] for m in list_manifests(str(tmp_path), "cms_hrrp")] == ["b1", "b2"]


def test_fetch_cli_registers_every_source_and_takes_several(tmp_path, monkeypatch, capsys):
    assert {
        HRRP_SOURCE,
        HOSPITAL_INFO_SOURCE,
        UNPLANNED_VISITS_SOURCE,
        "hrrp_history_pdc",
        "hrrp_history_ipps",
    } == SOURCES.keys()
    land_manifest(
        LocalLander(str(tmp_path)), "cms_hrrp", "b1", [{"name": "a", "modified": "2026-01-26"}]
    )
    monkeypatch.setattr(
        "readmission.ingest.fetch.make_client",
        lambda: httpx.Client(transport=hrrp_server([])),
    )

    main([HRRP_SOURCE, HRRP_SOURCE, "--local", str(tmp_path), "--work-dir", str(tmp_path)])

    assert (
        capsys.readouterr().out.splitlines()
        == [
            "cms_hrrp: unchanged since the last landed batch, nothing landed",
        ]
        * 2
    )


def test_read_csv_rows_keeps_values_as_received():
    rows = read_csv_rows(FIXTURES / "hrrp.csv")

    assert list(rows[0])[:2] == ["Facility Name", "Facility ID"]
    assert rows[0]["Facility ID"] == "010001"
    assert rows[0]["Number of Discharges"] == "N/A"
    assert rows[0]["Footnote"] == ""
    assert rows[0]["Number of Readmissions"] == "Too Few to Report"
