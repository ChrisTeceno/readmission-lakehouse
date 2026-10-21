import json
from pathlib import Path

import httpx
import pytest

from readmission.ingest import cms

FIXTURES = Path(__file__).parent / "fixtures" / "cms"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def no_sleep(seconds):
    pass


def test_pagination_stops_at_count_and_concatenates():
    seen = []

    def handler(request):
        offset = int(request.url.params["offset"])
        seen.append((int(request.url.params["limit"]), offset))
        rows = [{"n": i} for i in range(offset, min(offset + 2, 5))]
        return httpx.Response(200, json={"results": rows, "count": 5})

    records = cms.fetch_records(cms.HRRP, page_size=2, client=client_for(handler))
    assert seen == [(2, 0), (2, 2), (2, 4)]
    assert records == [{"n": i} for i in range(5)]


def test_pagination_stops_on_empty_page_when_count_is_wrong():
    calls = []

    def handler(request):
        calls.append(request)
        offset = int(request.url.params["offset"])
        rows = [{"n": 0}, {"n": 1}] if offset == 0 else []
        return httpx.Response(200, json={"results": rows, "count": 999})

    records = cms.fetch_records(cms.HRRP, page_size=2, client=client_for(handler))
    assert records == [{"n": 0}, {"n": 1}]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "dataset_id,name",
    [
        (cms.HRRP, "hrrp_page.json"),
        (cms.HOSPITAL_INFO, "hospital_info_page.json"),
        (cms.UNPLANNED_VISITS, "unplanned_visits_page.json"),
    ],
)
def test_recorded_first_page_is_parsed(dataset_id, name):
    page = fixture(name)

    def handler(request):
        assert request.url.path == f"/provider-data/api/1/datastore/query/{dataset_id}/0"
        return httpx.Response(200, json=page)

    records = cms.fetch_records(dataset_id, page_size=1500, client=client_for(handler))
    # The fixture is trimmed to 3 rows but keeps the real count, so one page is not the end.
    assert page["count"] > len(page["results"])
    assert records[: len(page["results"])] == page["results"]


def test_429_then_200_succeeds_and_honors_retry_after():
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}, text="slow down"),
        httpx.Response(200, json={"results": [{"n": 1}], "count": 1}),
    ]
    sleeps = []

    records = cms.fetch_records(
        cms.HRRP,
        client=client_for(lambda request: responses.pop(0)),
        sleep=sleeps.append,
    )
    assert records == [{"n": 1}]
    assert sleeps == [7.0]


def test_repeated_503_fails_after_retry_limit():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, text="<html>Service Unavailable</html>")

    sleeps = []
    with pytest.raises(cms.CmsApiError, match="failed after 3 attempts"):
        cms.fetch_records(cms.HRRP, client=client_for(handler), max_attempts=3, sleep=sleeps.append)
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert all(s > 0 for s in sleeps)


def test_timeout_is_retried():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"results": [], "count": 0})

    assert cms.fetch_records(cms.HRRP, client=client_for(handler), sleep=no_sleep) == []
    assert len(calls) == 2


def test_other_4xx_fails_fast():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404, json={"message": "not found"})

    with pytest.raises(cms.CmsApiError, match="HTTP 404"):
        cms.fetch_records(cms.HRRP, client=client_for(handler), sleep=no_sleep)
    assert len(calls) == 1


def test_html_body_raises_cms_api_error():
    html = "<html><body>" + "x" * 500 + "</body></html>"

    def handler(request):
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    with pytest.raises(cms.CmsApiError) as excinfo:
        cms.fetch_records(cms.HRRP, client=client_for(handler), sleep=no_sleep)
    message = str(excinfo.value)
    assert f"{cms.BASE_URL}/datastore/query/{cms.HRRP}/0" in message
    assert "HTTP 200" in message
    assert html[:200] in message
    assert html[:201] not in message


@pytest.mark.parametrize(
    "dataset_id,name,title",
    [
        (cms.HRRP, "hrrp_metadata.json", "Hospital Readmissions Reduction Program"),
        (cms.HOSPITAL_INFO, "hospital_info_metadata.json", None),
        (cms.UNPLANNED_VISITS, "unplanned_visits_metadata.json", None),
    ],
)
def test_metadata_returns_modified_date_and_download_url(dataset_id, name, title):
    item = fixture(name)

    def handler(request):
        assert request.url.path == (
            f"/provider-data/api/1/metastore/schemas/dataset/items/{dataset_id}"
        )
        return httpx.Response(200, json=item)

    metadata = cms.fetch_metadata(dataset_id, client=client_for(handler))
    assert metadata.dataset_id == dataset_id
    assert metadata.title == (title or item["title"])
    assert metadata.modified == item["modified"]
    assert metadata.download_url == item["distribution"][0]["downloadURL"]
    assert metadata.download_url.endswith(".csv")


@pytest.mark.live
def test_live_hrrp_first_page():
    with httpx.Client() as client:
        page = cms._get_json(client, f"/datastore/query/{cms.HRRP}/0", {"limit": 1, "offset": 0})
    assert page["count"] > 10000
    assert len(page["results"]) == 1
