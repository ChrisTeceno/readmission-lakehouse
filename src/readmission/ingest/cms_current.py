"""Fetch the current CMS Provider Data datasets and parse them for Bronze.

Three sources (see docs/data-sources.md): `cms_hrrp` (`9n3s-kdb3`), `cms_hospital_info`
(`xubh-q36u`) and `cms_unplanned_visits` (`632h-zaca`). Fetch lands the dataset CSV named by the
metadata `downloadURL`, bytes unchanged, under `<source>/<batch_id>/` with a manifest. One CSV
request takes seconds where paging the datastore takes about 50 seconds per 1500 rows, and the
landed file is exactly what CMS publishes, so its sha256 identifies the data.

CMS bumps `modified` when it republishes a dataset. A fetch whose `modified` matches the latest
landed manifest lands nothing.
"""

import csv
import io
from functools import partial
from pathlib import Path

import httpx

from readmission.ingest import cms
from readmission.ingest.http import request_with_retry
from readmission.ingest.landing import Lander, land_manifest, list_manifests, sha256_hex

HRRP_SOURCE = "cms_hrrp"
HOSPITAL_INFO_SOURCE = "cms_hospital_info"
UNPLANNED_VISITS_SOURCE = "cms_unplanned_visits"

DATASETS = {
    HRRP_SOURCE: cms.HRRP,
    HOSPITAL_INFO_SOURCE: cms.HOSPITAL_INFO,
    UNPLANNED_VISITS_SOURCE: cms.UNPLANNED_VISITS,
}

CSV_ENCODING = "utf-8-sig"


def parse_csv_rows(text: str) -> list[dict[str, str]]:
    """Plain CSV with one header row; values stay strings as received."""
    return list(csv.DictReader(io.StringIO(text, newline="")))


def read_csv_rows(path: Path | str) -> list[dict[str, str]]:
    return parse_csv_rows(Path(path).read_text(encoding=CSV_ENCODING))


def latest_modified(root: str, source: str) -> str | None:
    manifests = list_manifests(root, source)
    return manifests[-1]["files"][0]["modified"] if manifests else None


def fetch_dataset(
    client: httpx.Client,
    lander: Lander,
    batch_id: str,
    work_dir: Path,
    *,
    source: str,
) -> list[dict]:
    """Land the dataset CSV unless CMS has not modified it since the last landed batch."""
    dataset_id = DATASETS[source]
    metadata = cms.fetch_metadata(dataset_id, client=client)
    if metadata.modified == latest_modified(lander.root, source):
        return []
    data = request_with_retry(client, metadata.download_url).content
    name = metadata.download_url.rsplit("/", 1)[-1]
    path = lander.land_file(source, batch_id, name, data)
    entry = {
        "name": name,
        "url": metadata.download_url,
        "dataset_id": dataset_id,
        "title": metadata.title,
        "modified": metadata.modified,
        "sha256": sha256_hex(data),
        "row_count": len(parse_csv_rows(data.decode(CSV_ENCODING))),
        "path": path,
    }
    land_manifest(lander, source, batch_id, [entry])
    return [entry]


FETCHERS = {source: partial(fetch_dataset, source=source) for source in DATASETS}
