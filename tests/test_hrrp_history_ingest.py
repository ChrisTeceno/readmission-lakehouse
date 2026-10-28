import io
import json
import random
import zipfile
from pathlib import Path

import httpx
import pytest

from readmission.ingest.fetch import SOURCES, run
from readmission.ingest.hrrp_history import (
    IPPS_FILES,
    PDC_INDEX_URL,
    PDC_MEMBER_PATTERNS,
    Snapshot,
    check_ipps_columns,
    fetch_ipps,
    fetch_pdc,
    find_member,
    fiscal_year_from_period_start,
    peek_pdc_fiscal_year,
    read_ipps_rows,
    read_pdc_rows,
    select_pdc_snapshots,
    theme_snapshots,
)
from readmission.ingest.http import download_zip, request_with_retry
from readmission.ingest.ipps_impact import IMPACT_FILES, fetch_ipps_impact, read_impact_rows
from readmission.ingest.landing import LocalLander, list_manifests, sha256_hex

FIXTURES = Path(__file__).parent / "fixtures" / "hrrp_history"

# SPEC.md, "Historical HRRP: how to fetch it". FY2023 is the one difference: SPEC lists
# 2023-10-06, but 2023-11-06 also carries FY2023 and is later. Their HRRP files are
# byte-identical (checked 2026-10-21), so the later one is kept, as for every other year.
EXPECTED_SNAPSHOTS = {
    2019: "2019-10-31",
    2020: "2020-10-16",
    2021: "2021-10-27",
    2022: "2022-10-06",
    2023: "2023-11-06",
    2024: "2024-10-30",
    2025: "2025-11-26",
    2026: "2026-08-13",
}


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(zipfile.ZipInfo(name, date_time=(2013, 3, 11, 13, 45, 0)), data)
    return buffer.getvalue()


def range_server(files: dict[str, bytes], calls: list | None = None) -> httpx.MockTransport:
    """Serves whole files, or byte ranges when asked, like data.cms.gov does."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        body = files.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        if "Range" in request.headers:
            start, end = request.headers["Range"].removeprefix("bytes=").split("-")
            chunk = body[int(start) : int(end) + 1]
            content_range = f"bytes {start}-{int(start) + len(chunk) - 1}/{len(body)}"
            return httpx.Response(206, content=chunk, headers={"Content-Range": content_range})
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


def no_sleep(_seconds):
    pass


def test_find_member_handles_every_pdc_layout():
    assert (
        find_member(
            ["ASC_Facility.csv", "HOSPITAL_QUARTERLY_QUALITYMEASURE_RRP_HOSPITAL.csv"],
            PDC_MEMBER_PATTERNS,
        )
        == "HOSPITAL_QUARTERLY_QUALITYMEASURE_RRP_HOSPITAL.csv"
    )
    assert find_member(["9n3s-kdb3.csv", "xubh-q36u.csv"], PDC_MEMBER_PATTERNS) == "9n3s-kdb3.csv"
    nested = "hospitals_10_2022/FY_2022_Hospital_Readmissions_Reduction_Program_Hospital.csv"
    assert find_member(["hospitals_10_2022/", nested], PDC_MEMBER_PATTERNS) == nested


def test_find_member_skips_macos_resource_forks():
    real = "hospitals_2026-08-13/9n3s-kdb3_2026-01-26_FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv"
    fork = "__MACOSX/hospitals_2026-08-13/._9n3s-kdb3_2026-01-26_FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv"
    assert find_member([real, fork], PDC_MEMBER_PATTERNS) == real


def test_find_member_none_when_snapshot_has_no_hrrp_file():
    assert (
        find_member(
            ["ASC_Facility.csv", "Timely_and_Effective_Care-Hospital.csv"], PDC_MEMBER_PATTERNS
        )
        is None
    )


def test_ipps_pattern_picks_latest_correction():
    names = [
        "Readmissions PUF-FY 2013 IPPS Correction-March 2013.txt",
        "Readmissions PUF-FY 2013 IPPS Correction-March 2013.xlsx",
        "Readmissions PUF-FY 2013 IPPS Correction-September 2012.txt",
        "Readmissions PUF-FY 2013 IPPS-August 2012.txt",
        "Readmissions PUF-FY 2013 IPPS-File Layout.txt",
    ]
    assert find_member(names, [IPPS_FILES[2013][1]]) == names[0]
    fy2015 = ["Final FY15.txt", "Final FY15-CN Oct 2014.txt", "FY 2015 DRG Info-CN Oct 2014.txt"]
    assert find_member(fy2015, [IPPS_FILES[2015][1]]) == "Final FY15-CN Oct 2014.txt"


def test_fiscal_year_from_period_start():
    assert fiscal_year_from_period_start("07/01/2014") == 2019
    assert fiscal_year_from_period_start("07/01/2021") == 2026
    with pytest.raises(ValueError):
        fiscal_year_from_period_start("01/01/2020")


def test_theme_snapshots_skip_annual_bundles_newest_first():
    index = json.loads((FIXTURES / "pdc_archive_index.json").read_text())

    snapshots = theme_snapshots(index)

    assert len(snapshots) == 34
    assert snapshots[0] == Snapshot(
        "2026-09-30",
        "https://data.cms.gov/provider-data/sites/default/files/dataset-archives/theme/hospitals/hospitals_2026-09-30.zip",
    )
    assert all("annual" not in s.url for s in snapshots)


def test_snapshot_selection_reproduces_spec_table():
    index = json.loads((FIXTURES / "pdc_archive_index.json").read_text())
    starts = json.loads((FIXTURES / "pdc_snapshot_period_starts.json").read_text())
    peeked = []

    def fiscal_year_of(snapshot):
        peeked.append(snapshot.date)
        start = starts[snapshot.date]
        return fiscal_year_from_period_start(start) if start else None

    selected = select_pdc_snapshots(theme_snapshots(index), fiscal_year_of)

    assert {fy: s.date for fy, s in selected.items()} == EXPECTED_SNAPSHOTS
    # Stops once every year is found, so the older FY2019 copies are never opened.
    assert peeked[-1] == "2019-10-31"


def test_retry_on_429_and_5xx_then_success():
    responses = iter([httpx.Response(429), httpx.Response(503), httpx.Response(200, text="ok")])
    sleeps = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: next(responses)))

    response = request_with_retry(client, "https://example.test/x", sleep=sleeps.append)

    assert response.text == "ok"
    assert sleeps == [1.0, 2.0]


def test_retry_on_timeout():
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert request_with_retry(client, "https://example.test/x", sleep=no_sleep).text == "ok"
    assert len(attempts) == 2


def test_retry_gives_up_and_client_errors_do_not_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if "flaky" in str(request.url) else 404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(client, "https://example.test/flaky", attempts=3, sleep=no_sleep)
    assert len(calls) == 3
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(client, "https://example.test/missing", sleep=no_sleep)
    assert len(calls) == 4


def test_peek_reads_fiscal_year_through_range_requests():
    csv_bytes = (FIXTURES / "pdc_fy2022.csv").read_bytes()
    nested = "hospitals_10_2022/FY_2022_Hospital_Readmissions_Reduction_Program_Hospital.csv"
    padding = random.Random(0).randbytes(2_000_000)
    body = zip_bytes({"hospitals_10_2022/ASC_Facility.bin": padding, nested: csv_bytes})
    url = "https://data.cms.gov/hospitals_2022-10-06.zip"
    calls = []
    client = httpx.Client(transport=range_server({url: body}, calls))

    assert peek_pdc_fiscal_year(client, Snapshot("2022-10-06", url)) == 2022
    ranges = [c.headers["Range"].removeprefix("bytes=").split("-") for c in calls]
    assert sum(int(end) - int(start) + 1 for start, end in ranges) < len(body) / 2


def test_download_retries_truncated_zip(tmp_path):
    body = zip_bytes({"a.txt": b"hello" * 1000})
    bodies = iter([body[: len(body) // 2], body])
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=next(bodies)))
    )

    path = download_zip(client, "https://example.test/a.zip", tmp_path / "a.zip")

    assert path.read_bytes() == body


def test_fetch_pdc_lands_selected_hrrp_files(tmp_path):
    base = "https://data.cms.gov/provider-data/sites/default/files/dataset-archives/theme/hospitals"
    fy2019 = (FIXTURES / "pdc_fy2019_provider_id.csv").read_bytes()
    fy2022 = (FIXTURES / "pdc_fy2022.csv").read_bytes()
    files = {
        f"{base}/hospitals_2019-07-31.zip": zip_bytes(
            {"HOSPITAL_QUARTERLY_QUALITYMEASURE_RRP_HOSPITAL.csv": fy2019}
        ),
        f"{base}/hospitals_2020-07-04.zip": zip_bytes({"ASC_Facility.csv": b"x\n"}),
        f"{base}/hospitals_2022-10-06.zip": zip_bytes(
            {
                "hospitals_10_2022/FY_2022_Hospital_Readmissions_Reduction_Program_Hospital.csv": fy2022
            }
        ),
    }
    index = {
        "data": [
            {"type": "theme", "date": d, "url": u.removeprefix("https://data.cms.gov")}
            for u, d in zip(files, ["2019-07-31", "2020-07-04", "2022-10-06"], strict=True)
        ]
        + [{"type": "annual_theme", "date": "2022-12-31", "url": "/annual.zip"}]
    }
    files[PDC_INDEX_URL] = json.dumps(index).encode()
    client = httpx.Client(transport=range_server(files))
    lander = LocalLander(str(tmp_path / "landing"))

    landed = fetch_pdc(client, lander, "b1", tmp_path / "work", fiscal_years=[2019, 2022])

    assert [(f["fiscal_year"], f["snapshot_date"], f["name"], f["row_count"]) for f in landed] == [
        (2019, "2019-07-31", "pdc_fy2019.csv", 8),
        (2022, "2022-10-06", "pdc_fy2022.csv", 12),
    ]
    fy2022_entry = landed[1]
    assert fy2022_entry["path"] == str(
        tmp_path / "landing" / "hrrp_history_pdc" / "2022" / "b1" / "pdc_fy2022.csv"
    )
    assert Path(fy2022_entry["path"]).read_bytes() == fy2022
    assert fy2022_entry["sha256"] == sha256_hex(fy2022)
    assert fy2022_entry["dataset_id"] == "9n3s-kdb3"
    assert fy2022_entry["source_ref"].endswith(
        "hospitals_2022-10-06.zip#hospitals_10_2022/FY_2022_Hospital_Readmissions_Reduction_Program_Hospital.csv"
    )
    manifests = list_manifests(str(tmp_path / "landing"), "hrrp_history_pdc")
    assert [(m["partition"], m["batch_id"], m["files"]) for m in manifests] == [
        ("2019", "b1", [landed[0]]),
        ("2022", "b1", [landed[1]]),
    ]
    # Only the extracted HRRP file and its manifest land, never the zip.
    landed_names = {p.name for p in (tmp_path / "landing").rglob("*") if p.is_file()}
    assert landed_names == {"pdc_fy2019.csv", "pdc_fy2022.csv", "_manifest.json"}


def test_fetch_ipps_lands_readmissions_file_only(tmp_path):
    fy2013 = (FIXTURES / "ipps_fy2013.txt").read_bytes()
    url = "https://www.cms.gov" + IPPS_FILES[2013][0]
    body = zip_bytes(
        {
            "Readmissions PUF-FY 2013 IPPS Correction-March 2013.txt": fy2013,
            "Readmissions PUF-FY 2013 IPPS-August 2012.txt": b"draft",
            "Readmissions PUF-FY 2013 IPPS-File Layout.txt": b"layout",
        }
    )
    client = httpx.Client(transport=range_server({url: body}))

    landed = fetch_ipps(
        client, LocalLander(str(tmp_path / "landing")), "b1", tmp_path / "work", [2013]
    )

    assert len(landed) == 1
    assert landed[0]["member"] == "Readmissions PUF-FY 2013 IPPS Correction-March 2013.txt"
    assert landed[0]["member_modified"] == "2013-03-11"
    assert landed[0]["row_count"] == 3
    assert Path(landed[0]["path"]).read_bytes() == fy2013


def test_ipps_fy2026_pattern_skips_the_case_info_tab():
    names = ["FY2026_HRRP_Supplemental_File.xlsx", "FR FY 2026.txt", "FR FY 2026 Case Info.txt"]
    assert find_member(names, [IPPS_FILES[2026][1]]) == "FR FY 2026.txt"


PAYMENT_FIXTURES = Path(__file__).parent / "fixtures" / "hrrp_payment"

# Member names of the FY2019 to FY2025 supplemental zips (downloaded 2026-10-27), with the file
# each year's pattern must pick: the hospital-level text copy, not the workbook, the case-info
# tab or a variable description.
PEER_ERA_MEMBERS = {
    2019: (
        [
            "fy_2019_ipps_final_rule_hrrp_supplemental_file.xlsx",
            "Section 508 - FR FY 19 - tab - fy_2019_ipps_final_rule_hrrp_supplemental_file.txt",
            "Section 508 - FR FY Case Info tab - fy_2019_ipps_final_rule_hrrp_supplemental_file.txt",
            "Section 508 - Variable Description tab - fy_2019_ipps_final_rule_hrrp_supplemental_file.txt",
        ],
        1,
    ),
    2020: (
        [
            "FY2020_IPPS_Final_Rule_HRRP_Supplemental_File tab1.txt",
            "FY2020_IPPS_Final_Rule_HRRP_Supplemental_File tab2.txt",
            "FY2020_IPPS_Final_Rule_HRRP_Supplemental_File tab4.txt",
            "FY2020_IPPS_Final_Rule_HRRP_Supplemental_File.xlsx",
        ],
        1,
    ),
    2021: (
        [
            "FY2021_Final_Rule_Supplemental_File.xlsx",
            "FY2021_Final_Rule_Supplemental_File_FR FY 2021 Case Info.txt",
            "FY2021_Final_Rule_Supplemental_File_FR FY 2021.txt",
            "FY2021_Final_Rule_Supplemental_File_Variable Description.txt",
        ],
        2,
    ),
    2022: (
        [
            "508_Compliant_Version_of_FR FY 2022 Case Info.csv",
            "508_Compliant_Version_of_FR FY 2022.csv",
            "508_Compliant_Version_of_Variable Description.csv",
            "FY2022_Final_Rule_Supplemental_File.xlsx",
        ],
        1,
    ),
    2023: (
        [
            "FY2023_Final_Rule_Supplemental_File FR FY 2023 Case Info.txt",
            "FY2023_Final_Rule_Supplemental_File.xlsx",
            "FY2023_FR_Supplemental_File FR FY 2023.txt",
            "FY2023_FR_Supplemental_File Variable Description.txt",
        ],
        2,
    ),
    2024: (
        [
            "508 version of FY2024_Final_Rule_Supplemental_File Variables tab .txt",
            "508 version of FY2024_Final_Rule_Supplemental_File Variations FR FY 2024 tab .txt",
            "508 version of FY2024_Final_Rule_Supplemental_File Variations Variable FR FY 2024 - Case Info tab .txt",
            "FY2024_Final_Rule_Supplemental_File.xlsx",
        ],
        1,
    ),
    2025: (
        [
            "508-Version-FR-FY-2025-Case-Info.csv",
            "508-Version-FR-FY-2025.csv",
            "508-Version-Variable-Description.csv",
            "FY2025_Final_Rule_Supplemental_File.xlsx",
        ],
        1,
    ),
}


def test_ipps_files_cover_every_fiscal_year():
    assert sorted(IPPS_FILES) == list(range(2013, 2028))


@pytest.mark.parametrize("fiscal_year", sorted(PEER_ERA_MEMBERS))
def test_ipps_peer_era_pattern_picks_the_hospital_text_file(fiscal_year):
    names, expected = PEER_ERA_MEMBERS[fiscal_year]
    assert find_member(names, [IPPS_FILES[fiscal_year][1]]) == names[expected]


def test_read_ipps_rows_reads_comma_separated_files():
    # FY2025's text copy is a CSV with a byte order mark and quoted thousands separators.
    rows = read_ipps_rows(PAYMENT_FIXTURES / "supplemental_fy2025.csv")

    assert len(rows) == 12
    ch04 = next(r for r in rows if r["Hospital CCN"] == "360051")
    assert ch04["Payment adjustment factor"] == "0.9946"
    assert ch04["Number of eligible discharges for HF"] == "1,025"
    assert "" not in ch04


def test_check_ipps_columns_matches_names_whatever_the_capitalization():
    fy2019 = read_ipps_rows(PAYMENT_FIXTURES / "supplemental_fy2019.txt")
    assert "Peer Group Assignment" in fy2019[0]
    check_ipps_columns(fy2019, 2019)
    check_ipps_columns(read_ipps_rows(PAYMENT_FIXTURES / "supplemental_fy2025.csv"), 2025)
    # Before peer grouping there is nothing to check.
    check_ipps_columns(read_ipps_rows(FIXTURES / "ipps_fy2013.txt"), 2013)


def test_check_ipps_columns_fails_on_a_missing_column():
    rows = read_ipps_rows(PAYMENT_FIXTURES / "supplemental_fy2019.txt")
    without_median = [
        {k: v for k, v in r.items() if k != "Peer Group Median ERR for HF"} for r in rows
    ]
    without_group = [{k: v for k, v in r.items() if k != "Peer Group Assignment"} for r in rows]

    with pytest.raises(ValueError, match="peer group median err for hf"):
        check_ipps_columns(without_median, 2019)
    with pytest.raises(ValueError, match="peer group assignment"):
        check_ipps_columns(without_group, 2019)
    with pytest.raises(ValueError, match="no data rows"):
        check_ipps_columns([], 2020)


def test_fetch_ipps_lands_a_csv_under_its_own_extension(tmp_path):
    data = (PAYMENT_FIXTURES / "supplemental_fy2025.csv").read_bytes()
    url = "https://www.cms.gov" + IPPS_FILES[2025][0]
    body = zip_bytes(
        {
            "508-Version-FR-FY-2025.csv": data,
            "508-Version-FR-FY-2025-Case-Info.csv": b"case info",
            "FY2025_Final_Rule_Supplemental_File.xlsx": b"spreadsheet",
        }
    )
    client = httpx.Client(transport=range_server({url: body}))

    landed = fetch_ipps(
        client, LocalLander(str(tmp_path / "landing")), "b1", tmp_path / "work", [2025]
    )

    assert (landed[0]["name"], landed[0]["row_count"]) == ("ipps_fy2025.csv", 12)
    assert Path(landed[0]["path"]).read_bytes() == data
    assert len(read_ipps_rows(landed[0]["path"])) == 12


def test_fetch_ipps_refuses_a_file_without_peer_medians(tmp_path):
    text = (PAYMENT_FIXTURES / "supplemental_fy2019.txt").read_text()
    data = text.replace("Peer Group Median ERR for AMI", "Median ERR for AMI").encode()
    url = "https://www.cms.gov" + IPPS_FILES[2019][0]
    body = zip_bytes({"Section 508 - FR FY 19 - tab - fy_2019.txt": data})
    client = httpx.Client(transport=range_server({url: body}))
    lander = LocalLander(str(tmp_path / "landing"))

    with pytest.raises(ValueError, match="peer group median err for ami"):
        fetch_ipps(client, lander, "b1", tmp_path / "work", [2019])
    assert list_manifests(str(tmp_path / "landing"), "hrrp_history_ipps") == []


def test_fetch_ipps_impact_lands_the_correction_notice(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "hrrp_payment" / "impact_fy2027.txt"
    data = fixture.read_bytes()
    url = "https://www.cms.gov" + IMPACT_FILES[2027][0]
    body = zip_bytes(
        {
            "FY 2027 IPPS Impact File - Final Rule.txt": b"superseded",
            "FY 2027 IPPS Impact File - Correction Notice.txt": data,
            "FY 2027 IPPS FR & CN Impact File.xlsx": b"spreadsheet",
        }
    )
    client = httpx.Client(transport=range_server({url: body}))

    landed = fetch_ipps_impact(
        client, LocalLander(str(tmp_path / "landing")), "b1", tmp_path / "work", [2027]
    )

    assert landed[0]["member"] == "FY 2027 IPPS Impact File - Correction Notice.txt"
    assert landed[0]["row_count"] == 12
    assert Path(landed[0]["path"]).read_bytes() == data
    (manifest,) = list_manifests(str(tmp_path / "landing"), "ipps_impact")
    assert (manifest["partition"], manifest["files"][0]["name"]) == ("2027", "impact_fy2027.txt")
    assert "ipps_impact" in SOURCES


def test_read_impact_rows_uses_the_provider_number_header():
    rows = read_impact_rows(
        Path(__file__).parent / "fixtures" / "hrrp_payment" / "impact_fy2026.txt"
    )
    assert len(rows) == 12
    assert rows[0]["Provider Number"] == "360027"
    assert rows[0]["CASETA43"] and rows[0]["FY 2026 Wage Index"] == "0.8767"


def test_fetch_cli_registry_runs_a_source(tmp_path):
    fy2027 = (FIXTURES / "ipps_fy2027.txt").read_bytes()
    url = "https://www.cms.gov" + IPPS_FILES[2027][0]
    client = httpx.Client(
        transport=range_server(
            {url: zip_bytes({"FY2027_HRRP_Supplemental_File - FR FY 2027 Tab.txt": fy2027})}
        )
    )
    calls = []

    def only_2027(client, lander, batch_id, work_dir):
        calls.append(batch_id)
        return fetch_ipps(client, lander, batch_id, work_dir, [2027])

    SOURCES["test_ipps_2027"] = only_2027
    try:
        landed = run("test_ipps_2027", LocalLander(str(tmp_path)), tmp_path / "work", client=client)
    finally:
        del SOURCES["test_ipps_2027"]

    assert {"hrrp_history_pdc", "hrrp_history_ipps"} <= SOURCES.keys()
    assert len(calls[0]) == len("20261021T080509Z")
    assert landed[0]["row_count"] == 3


def test_read_ipps_rows_skips_title_and_note_rows():
    fy2013 = read_ipps_rows(FIXTURES / "ipps_fy2013.txt")
    fy2027 = read_ipps_rows(FIXTURES / "ipps_fy2027.txt")

    assert [r["PROV"] for r in fy2013] == ["010001", "010005", "010007"]
    assert fy2013[1]["Acute Myocardial Infarction Excess Readmission Ratio"] == "0.0000"
    assert [r["Hospital CCN"] for r in fy2027] == ["010001", "010005", "010007"]
    assert fy2027[0]["Payment adjustment factor"] == "0.9996"
    assert fy2027[0]["Number of eligible discharges for HF"] == "1,110"
    assert "" not in fy2027[0]


def test_read_ipps_rows_drops_trailing_blank_rows():
    rows = read_ipps_rows(FIXTURES / "ipps_fy2017.txt")

    assert [r["Provider"] for r in rows] == ["010001", "010005", "010007"]
    assert "Coronary Artery Bypass Graft Excess Readmission Ratio" in rows[0]


def test_read_pdc_rows_old_header():
    rows = read_pdc_rows(FIXTURES / "pdc_fy2019_provider_id.csv")

    assert list(rows[0])[:2] == ["Hospital Name", "Provider ID"]
    assert rows[0]["Number of Discharges"] == "Not Available"
