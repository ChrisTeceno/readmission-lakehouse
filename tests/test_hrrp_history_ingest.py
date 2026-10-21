import io
import json
import random
import zipfile
from pathlib import Path

import httpx
import pytest

from readmission.ingest.hrrp_history import (
    IPPS_FILES,
    PDC_INDEX_URL,
    PDC_MEMBER_PATTERNS,
    Snapshot,
    download,
    fetch_ipps,
    fetch_pdc,
    find_member,
    fiscal_year_from_period_start,
    peek_pdc_fiscal_year,
    read_ipps_rows,
    read_pdc_rows,
    request_with_retry,
    select_pdc_snapshots,
    theme_snapshots,
)
from readmission.ingest.landing import LocalLander

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
            zf.writestr(name, data)
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

    path = download(client, "https://example.test/a.zip", tmp_path / "a.zip")

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

    assert [(f.fiscal_year, f.snapshot_date, f.name) for f in landed] == [
        (2019, "2019-07-31", "pdc_fy2019.csv"),
        (2022, "2022-10-06", "pdc_fy2022.csv"),
    ]
    assert Path(landed[1].path).read_bytes() == fy2022
    assert landed[1].source_ref.endswith(
        "hospitals_2022-10-06.zip#hospitals_10_2022/FY_2022_Hospital_Readmissions_Reduction_Program_Hospital.csv"
    )


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
    assert landed[0].member == "Readmissions PUF-FY 2013 IPPS Correction-March 2013.txt"
    assert Path(landed[0].path).read_bytes() == fy2013


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
