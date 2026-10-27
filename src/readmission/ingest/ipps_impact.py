"""Fetch the IPPS final rule impact files, FY2026 and FY2027, and parse them for Bronze.

One row per IPPS hospital with the inputs CMS uses to model the fiscal year's payments: wage
index, provider type, transfer-adjusted cases and case mix index. Gold multiplies them by the
national standardized amounts to estimate each Contoso facility's base operating DRG payments,
the amount the HRRP payment adjustment factor reduces (readmission.gold.penalty_dollars).

Fetch lands the one impact text file from each zip unchanged under
`ipps_impact/<fiscal_year>/<batch_id>/` with a manifest, like the IPPS readmissions files.
"""

import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import httpx

from readmission.ingest.hrrp_history import IPPS_BASE, IPPS_ENCODING, find_member, parse_ipps_rows
from readmission.ingest.http import download_zip
from readmission.ingest.landing import Lander, land_manifest, sha256_hex

SOURCE = "ipps_impact"

# FY2027 has a final rule and a correction notice file in one zip; the correction notice is the
# later one and the one its Table 1B amounts go with (checked 2026-10-26).
IMPACT_FILES: dict[int, tuple[str, str]] = {
    2026: ("/files/zip/fy2026-ipps-fr-impact-file.zip", "FY 2026 IPPS Final Rule Impact File.txt"),
    2027: (
        "/files/zip/fy2027-ipps-fr-impact-file.zip",
        "FY 2027 IPPS Impact File - Correction Notice.txt",
    ),
}
IMPACT_CCN_HEADERS = ("Provider Number",)


def parse_impact_rows(text: str) -> list[dict[str, str]]:
    """One title row, then the header; values stay strings as received."""
    return parse_ipps_rows(text, IMPACT_CCN_HEADERS)


def read_impact_rows(path: Path | str) -> list[dict[str, str]]:
    return parse_impact_rows(Path(path).read_text(encoding=IPPS_ENCODING))


def fetch_ipps_impact(
    client: httpx.Client,
    lander: Lander,
    batch_id: str,
    work_dir: Path,
    fiscal_years: Iterable[int] = IMPACT_FILES.keys(),
) -> list[dict]:
    landed = []
    for fiscal_year in sorted(fiscal_years):
        url_path, member_name = IMPACT_FILES[fiscal_year]
        url = IPPS_BASE + url_path
        zip_path = download_zip(client, url, work_dir / f"fy{fiscal_year}.zip")
        with zipfile.ZipFile(zip_path) as zip_file:
            member = find_member(zip_file.namelist(), [member_name])
            if member is None:
                raise ValueError(f"{zip_path}: no member named {member_name!r}")
            data = zip_file.read(member)
            member_date = datetime(*zip_file.getinfo(member).date_time).date().isoformat()
        name = f"impact_fy{fiscal_year}.txt"
        partition = str(fiscal_year)
        path = lander.land_file(SOURCE, batch_id, name, data, partition)
        entry = {
            "url": url,
            "dataset_id": None,
            "snapshot_date": None,
            "name": name,
            "fiscal_year": fiscal_year,
            "member": member,
            "member_modified": member_date,
            "source_ref": f"{url}#{member}",
            "sha256": sha256_hex(data),
            "row_count": len(parse_impact_rows(data.decode(IPPS_ENCODING))),
            "path": path,
        }
        land_manifest(lander, SOURCE, batch_id, [entry], partition)
        landed.append(entry)
    return landed
