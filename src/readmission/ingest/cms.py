"""CMS Provider Data Catalog client: paginated datastore queries and dataset metadata.

Pure Python, no Spark. The fetch step in the Databricks job and the ADF fallback both use it.
"""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

BASE_URL = "https://data.cms.gov/provider-data/api/1"

HRRP = "9n3s-kdb3"
HOSPITAL_INFO = "xubh-q36u"
UNPLANNED_VISITS = "632h-zaca"

# Largest `limit` the datastore accepts. Verified 2026-10-21 against the HRRP dataset: 1500
# returned HTTP 200, 1501, 1800 and 2000 returned HTTP 400 (JSON schema validation on `limit`).
DEFAULT_PAGE_SIZE = 1500

# Responses are slow (a 1500-row page took 40 to 55 seconds), so the read timeout is generous.
TIMEOUT = httpx.Timeout(60.0, connect=10.0)

DEFAULT_MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 60.0

RETRYABLE_STATUSES = frozenset({429}) | frozenset(range(500, 600))


class CmsApiError(Exception):
    """The CMS API returned something we cannot use, or kept failing after retries."""


@dataclass(frozen=True)
class DatasetMetadata:
    dataset_id: str
    title: str
    modified: str
    download_url: str


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _backoff_seconds(attempt: int) -> float:
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
    return random.uniform(ceiling / 2, ceiling)


def _get_json(
    client: httpx.Client,
    path: str,
    params: dict[str, int] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    url = f"{BASE_URL}{path}"
    last_error = "no attempt made"
    for attempt in range(1, max_attempts + 1):
        delay = None
        try:
            response = client.get(url, params=params, timeout=TIMEOUT)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code not in RETRYABLE_STATUSES:
                return _parse_json(response)
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            delay = _retry_after_seconds(response)
        if attempt < max_attempts:
            sleep(delay if delay is not None else _backoff_seconds(attempt))
    raise CmsApiError(f"GET {url} failed after {max_attempts} attempts, last error {last_error}")


def _parse_json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.is_success and isinstance(body, dict):
        return body
    raise CmsApiError(
        f"GET {response.request.url} returned HTTP {response.status_code} "
        f"with {'an unusable JSON body' if body is not None else 'a non-JSON body'}: "
        f"{response.text[:200]}"
    )


def fetch_records(
    dataset_id: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    client: httpx.Client | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Return every row of a datastore dataset, following offset pagination."""
    owned = client is None
    client = client or httpx.Client()
    try:
        records: list[dict] = []
        offset = 0
        while True:
            page = _get_json(
                client,
                f"/datastore/query/{dataset_id}/0",
                {"limit": page_size, "offset": offset},
                max_attempts,
                sleep,
            )
            results = page["results"]
            records.extend(results)
            offset += page_size
            # An empty page also ends the loop, so a wrong count cannot spin forever.
            if not results or offset >= page["count"]:
                return records
    finally:
        if owned:
            client.close()


def fetch_metadata(
    dataset_id: str,
    client: httpx.Client | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> DatasetMetadata:
    """Return title, modified date and CSV download URL for a dataset."""
    owned = client is None
    client = client or httpx.Client()
    try:
        item = _get_json(
            client, f"/metastore/schemas/dataset/items/{dataset_id}", None, max_attempts, sleep
        )
    finally:
        if owned:
            client.close()
    csv_urls = [
        d["downloadURL"] for d in item.get("distribution", []) if d.get("mediaType") == "text/csv"
    ]
    if not csv_urls:
        raise CmsApiError(f"dataset {dataset_id} metadata has no CSV distribution")
    return DatasetMetadata(
        dataset_id=dataset_id,
        title=item["title"],
        modified=item["modified"],
        download_url=csv_urls[0],
    )
