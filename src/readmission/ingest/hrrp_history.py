"""Fetch historical HRRP files, FY2013 to FY2027, and parse them for Bronze.

Two sources (see docs/data-sources.md):
- PDC archive, FY2019 to FY2026: quarterly zip snapshots of the whole hospitals theme.
- CMS.gov IPPS final rule supplemental zips, FY2013 to FY2018 and FY2027.

Fetch downloads each zip into a local work dir, extracts only the HRRP file and lands it
unchanged with a manifest. Parsing works on landed files, so the load step never calls the
internet.
"""

import argparse
import csv
import fnmatch
import io
import re
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx

from readmission.ingest.landing import (
    Lander,
    LocalLander,
    VolumeLander,
    land_manifest,
    new_batch_id,
)

LANDING_SOURCE = "hrrp_history"

PDC_BASE = "https://data.cms.gov"
PDC_INDEX_URL = f"{PDC_BASE}/provider-data/api/1/archive/aggregate/theme/hospitals/relative"
PDC_FISCAL_YEARS = range(2019, 2027)
# The HRRP file name inside a snapshot changed over the years: RRP_HOSPITAL.csv (2019 to early
# 2020), 9n3s-kdb3.csv (late 2020), FY_<year>_Hospital_Readmissions_Reduction_Program_Hospital.csv
# (2021 on, sometimes prefixed with the dataset id and nested in a folder).
PDC_MEMBER_PATTERNS = (
    "*Readmissions_Reduction_Program_Hospital*.csv",
    "9n3s-kdb3*.csv",
    "*RRP_HOSPITAL.csv",
)

IPPS_BASE = "https://www.cms.gov"
_IPPS_DOWNLOADS = "/medicare/medicare-fee-for-service-payment/acuteinpatientpps/downloads"
# Each zip holds several tab-separated files (DRG info, claim trims, earlier drafts). The
# pattern picks the hospital-level readmissions file, and the latest correction where CMS
# published one. Never use the CMS.gov FY2023 and FY2026 links: they serve the wrong year.
IPPS_FILES: dict[int, tuple[str, str]] = {
    2013: (
        f"{_IPPS_DOWNLOADS}/fy_2013_fr_readmissions_file.zip",
        "Readmissions PUF-FY 2013 IPPS Correction-March 2013.txt",
    ),
    2014: (
        f"{_IPPS_DOWNLOADS}/readmissions-supplemental-data-puf.zip",
        "FY 2014 Readmissions Supplemental Data Corection - Sept 2013.txt",
    ),
    2015: (f"{_IPPS_DOWNLOADS}/fy2015-fr-readmit-supp-data-file.zip", "Final FY15-CN*.txt"),
    2016: (f"{_IPPS_DOWNLOADS}/fy2016-cms-1632-fr-readmissions.zip", "*PUF_readmadj*.txt"),
    2017: (f"{_IPPS_DOWNLOADS}/fy2017-cms-1655-fr-hospital-readmissions.zip", "*(FY17 data).txt"),
    2018: (f"{_IPPS_DOWNLOADS}/fy2018-fr-hospital-readmissions.zip", "*(FY18 data).txt"),
    2027: (
        "/files/zip/fy-2027-hospital-readmissions-reduction-program-supplemental-data-file.zip",
        "*FR FY 2027 Tab.txt",
    ),
}
# www.cms.gov answers 403 to clients without a browser User-Agent.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
IPPS_CCN_HEADERS = ("PROV", "Provider", "Hospital CCN")

RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Snapshot:
    date: str
    url: str


@dataclass(frozen=True)
class LandedFile:
    source: str
    fiscal_year: int
    url: str
    member: str
    name: str
    path: str
    snapshot_date: str | None = None

    @property
    def source_ref(self) -> str:
        return f"{self.url}#{self.member}"


def request_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = 5,
    backoff: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """GET with exponential backoff on 429, 5xx and transport errors (timeouts included)."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = client.get(url, headers=headers)
        except httpx.TransportError:
            if last:
                raise
        else:
            if response.status_code not in RETRY_STATUSES or last:
                return response.raise_for_status()
        sleep(backoff * 2**attempt)
    raise AssertionError("unreachable")


def make_client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": BROWSER_USER_AGENT},
    )


def find_member(names: Iterable[str], patterns: Iterable[str]) -> str | None:
    """Match on the base name, so nested folder prefixes do not matter.

    macOS-built zips (2026-08-13) carry `__MACOSX/._<name>` resource forks that match the same
    pattern; they are not data.
    """
    patterns = tuple(patterns)
    hits = [
        name
        for name in names
        if not name.startswith("__MACOSX/")
        and not name.endswith("/")
        and any(fnmatch.fnmatch(name.rsplit("/", 1)[-1], p) for p in patterns)
    ]
    if len(hits) > 1:
        raise ValueError(f"more than one member matches {patterns}: {hits}")
    return hits[0] if hits else None


def theme_snapshots(index: dict) -> list[Snapshot]:
    """Dated snapshots from the archive index, newest first. Annual bundles are skipped."""
    entries = [e for e in index["data"] if e.get("type") == "theme"]
    snapshots = [Snapshot(e["date"], PDC_BASE + e["url"]) for e in entries]
    return sorted(snapshots, key=lambda s: s.date, reverse=True)


def fiscal_year_from_period_start(start_date: str) -> int:
    """HRRP fiscal year N measures discharges from July 1 of year N-5 (07/01/2014 is FY2019).

    The start holds for every year on PDC; COVID changed only the end of FY2022's window and the
    middle of FY2024's, so the start date is the reliable year marker the file itself reports.
    """
    start = datetime.strptime(start_date, "%m/%d/%Y").date()
    if (start.month, start.day) != (7, 1):
        raise ValueError(f"unexpected HRRP period start {start_date}")
    return start.year + 5


def select_pdc_snapshots(
    snapshots: list[Snapshot],
    fiscal_year_of: Callable[[Snapshot], int | None],
    fiscal_years: Iterable[int] = PDC_FISCAL_YEARS,
) -> dict[int, Snapshot]:
    """Pick, per fiscal year, the latest snapshot whose HRRP file reports that fiscal year.

    CMS republishes the same HRRP file in every quarterly snapshot until the next cycle replaces
    it, and the replacement lands in a different month each year (2019-10-31 for FY2019 data,
    2026-02-25 for FY2026), so neither a fixed date nor a month rule finds the right one. The
    file's own performance period does. Taking the latest copy keeps any mid-year correction;
    checked on 2026-10-21, every copy within a fiscal year had identical rows. Snapshots without
    an HRRP file (2020-07-04, 2026-09-30) return None and are skipped.
    """
    wanted = set(fiscal_years)
    selected: dict[int, Snapshot] = {}
    for snapshot in snapshots:
        if wanted <= selected.keys():
            break
        fiscal_year = fiscal_year_of(snapshot)
        if fiscal_year in wanted and fiscal_year not in selected:
            selected[fiscal_year] = snapshot
    return selected


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only view of a remote file through HTTP Range requests.

    zipfile only needs the central directory at the end of a zip and the bytes of one member,
    so peeking at a 15 MB snapshot costs well under 1 MB.
    """

    def __init__(self, client: httpx.Client, url: str, size: int):
        self.client, self.url, self.size, self.pos = client, url, size, 0

    @classmethod
    def open(cls, client: httpx.Client, url: str) -> io.BufferedReader:
        response = request_with_retry(client, url, headers={"Range": "bytes=0-0"})
        if response.status_code != 206:
            raise RuntimeError(f"{url} ignored the Range header")
        size = int(response.headers["Content-Range"].rsplit("/", 1)[1])
        return io.BufferedReader(cls(client, url, size), buffer_size=256 * 1024)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def readinto(self, buffer) -> int:
        end = min(self.pos + len(buffer), self.size)
        if end <= self.pos:
            return 0
        response = request_with_retry(
            self.client, self.url, headers={"Range": f"bytes={self.pos}-{end - 1}"}
        )
        if response.status_code != 206:
            raise RuntimeError(f"{self.url} ignored the Range header")
        data = response.content
        buffer[: len(data)] = data
        self.pos += len(data)
        return len(data)


def pdc_fiscal_year(zip_file: zipfile.ZipFile) -> int | None:
    member = find_member(zip_file.namelist(), PDC_MEMBER_PATTERNS)
    if member is None:
        return None
    with zip_file.open(member) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        return fiscal_year_from_period_start(next(reader)["Start Date"])


def peek_pdc_fiscal_year(client: httpx.Client, snapshot: Snapshot) -> int | None:
    with HttpRangeFile.open(client, snapshot.url) as remote:
        return pdc_fiscal_year(zipfile.ZipFile(remote))


def download(client: httpx.Client, url: str, dest: Path, attempts: int = 3) -> Path:
    """Download a zip unless a valid copy is already there.

    data.cms.gov sometimes ends a 200 response early under parallel load, leaving a truncated
    zip, so the length is checked against Content-Length and the zip structure is verified.
    """
    if dest.exists() and zipfile.is_zipfile(dest):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(attempts):
        response = request_with_retry(client, url)
        expected = response.headers.get("Content-Length")
        complete = expected is None or int(expected) == len(response.content)
        if complete and zipfile.is_zipfile(io.BytesIO(response.content)):
            dest.write_bytes(response.content)
            return dest
        if attempt == attempts - 1:
            raise RuntimeError(f"{url}: incomplete download after {attempts} attempts")
    raise AssertionError("unreachable")


def land_member(
    zip_path: Path, patterns: Iterable[str], lander: Lander, batch_id: str, name: str
) -> tuple[str, str]:
    """Land the one matching file, bytes unchanged, under a flat name. Returns (member, path)."""
    with zipfile.ZipFile(zip_path) as zip_file:
        member = find_member(zip_file.namelist(), patterns)
        if member is None:
            raise ValueError(f"{zip_path}: no member matches {tuple(patterns)}")
        path = lander.land_file(LANDING_SOURCE, batch_id, name, zip_file.read(member))
    return member, path


def fetch_pdc(
    client: httpx.Client,
    lander: Lander,
    batch_id: str,
    work_dir: Path,
    fiscal_years: Iterable[int] = PDC_FISCAL_YEARS,
) -> list[LandedFile]:
    index = request_with_retry(client, PDC_INDEX_URL).json()
    snapshots = theme_snapshots(index)
    selected = select_pdc_snapshots(
        snapshots, lambda s: peek_pdc_fiscal_year(client, s), fiscal_years
    )
    missing = set(fiscal_years) - selected.keys()
    if missing:
        raise RuntimeError(f"no PDC snapshot reports fiscal years {sorted(missing)}")
    landed = []
    for fiscal_year, snapshot in sorted(selected.items()):
        zip_path = download(client, snapshot.url, work_dir / "pdc" / Path(snapshot.url).name)
        name = f"pdc_fy{fiscal_year}.csv"
        member, path = land_member(zip_path, PDC_MEMBER_PATTERNS, lander, batch_id, name)
        landed.append(
            LandedFile("pdc", fiscal_year, snapshot.url, member, name, path, snapshot.date)
        )
    return landed


def fetch_ipps(
    client: httpx.Client,
    lander: Lander,
    batch_id: str,
    work_dir: Path,
    fiscal_years: Iterable[int] = IPPS_FILES.keys(),
) -> list[LandedFile]:
    landed = []
    for fiscal_year in sorted(fiscal_years):
        url_path, pattern = IPPS_FILES[fiscal_year]
        url = IPPS_BASE + url_path
        zip_path = download(client, url, work_dir / "ipps" / f"fy{fiscal_year}.zip")
        name = f"ipps_fy{fiscal_year}.txt"
        member, path = land_member(zip_path, [pattern], lander, batch_id, name)
        landed.append(LandedFile("ipps", fiscal_year, url, member, name, path))
    return landed


def fetch_hrrp_history(
    lander: Lander,
    work_dir: Path,
    batch_id: str | None = None,
    client: httpx.Client | None = None,
) -> list[LandedFile]:
    batch_id = batch_id or new_batch_id()
    with client or make_client() as http:
        landed = fetch_pdc(http, lander, batch_id, work_dir)
        landed += fetch_ipps(http, lander, batch_id, work_dir)
    entries = [asdict(f) | {"source_ref": f.source_ref} for f in landed]
    land_manifest(lander, LANDING_SOURCE, batch_id, entries)
    return landed


def read_pdc_rows(path: Path | str) -> list[dict[str, str]]:
    """PDC files are plain CSV with one header row; values stay strings as received."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_ipps_rows(path: Path | str) -> list[dict[str, str]]:
    """IPPS files are tab-separated with title rows above the header and notes below the data.

    The header is the first row starting with a CCN column name; data rows are those whose first
    cell is a CCN. Header names are trimmed (FY2027 pads them with spaces) and blank trailing
    columns dropped; values stay strings as received.
    """
    with open(path, encoding="cp1252", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    start = next(i for i, row in enumerate(rows) if row and row[0].strip() in IPPS_CCN_HEADERS)
    header = [h.strip() for h in rows[start]]
    records = []
    for row in rows[start + 1 :]:
        if not row or not re.fullmatch(r"\d{1,6}", row[0].strip()):
            continue
        records.append({h: v for h, v in zip(header, row, strict=False) if h})
    return records


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work-dir", type=Path, default=Path("data/hrrp_history"))
    parser.add_argument(
        "--volume",
        action="store_true",
        help="land in the Databricks landing volume instead of <work-dir>/landing",
    )
    parser.add_argument("--batch-id")
    args = parser.parse_args(argv)
    if args.volume:
        from databricks.sdk import WorkspaceClient

        lander: Lander = VolumeLander(WorkspaceClient())
    else:
        lander = LocalLander(str(args.work_dir / "landing"))
    landed = fetch_hrrp_history(lander, args.work_dir, args.batch_id)
    for f in landed:
        print(f"{f.source} FY{f.fiscal_year} {f.snapshot_date or ''} {f.member} -> {f.path}")


if __name__ == "__main__":
    main()
