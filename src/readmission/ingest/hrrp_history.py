"""Fetch historical HRRP files, FY2013 to FY2027, and parse them for Bronze.

Two sources (see docs/data-sources.md):
- `hrrp_history_pdc`: PDC archive, FY2019 to FY2026, quarterly zip snapshots of the whole
  hospitals theme.
- `hrrp_history_ipps`: CMS.gov IPPS final rule supplemental zips, FY2013 to FY2027. FY2019 to
  FY2026 are on PDC too; their supplemental files land for what PDC lacks: the peer group, peer
  median ERR, payment adjustment factor and DRG payment ratios (readmission.silver.hrrp_payment).

Fetch downloads each zip into a local work dir, extracts only the HRRP file and lands it
unchanged under `<source>/<fiscal_year>/<batch_id>/` with a manifest. Parsing works on landed
files, so the load step never calls the internet.
"""

import csv
import fnmatch
import io
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from readmission.ingest.http import HttpRangeFile, download_zip, request_with_retry
from readmission.ingest.landing import Lander, land_manifest, sha256_hex

PDC_SOURCE = "hrrp_history_pdc"
IPPS_SOURCE = "hrrp_history_ipps"

PDC_BASE = "https://data.cms.gov"
PDC_INDEX_URL = f"{PDC_BASE}/provider-data/api/1/archive/aggregate/theme/hospitals/relative"
PDC_DATASET_ID = "9n3s-kdb3"
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


def _supplemental_zip(fiscal_year: int) -> str:
    return (
        f"/files/zip/fy-{fiscal_year}-ipps-final-rule-hospital-readmissions-reduction-program-"
        "supplemental-data-file.zip"
    )


# Each zip holds several tab-separated files (DRG info, claim trims, earlier drafts). The
# pattern picks the hospital-level readmissions file, and the latest correction where CMS
# published one. The FY2026 link on the archived supplemental files page serves the FY2025 zip;
# the FY2026 final rule home page links the right file (checked 2026-10-26), which is the one
# below. The archived page's FY2023 entry has no zip link at all; its URL follows the FY2020 to
# FY2025 pattern. From FY2019 each zip also holds an xlsx workbook; the text copy CMS publishes
# beside it ("Section 508" or "508 version", comma-separated for FY2022 and FY2025) is landed
# instead. Checked 2026-10-27: every value in the text copies matched the workbooks to the
# four decimals CMS prints.
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
    2019: (
        f"{_IPPS_DOWNLOADS}/fy2019-cms-1694-fr-hospital-readmissions.zip",
        "Section 508 - FR FY 19 - tab - *.txt",
    ),
    2020: (_supplemental_zip(2020), "*Supplemental_File tab2.txt"),
    2021: (_supplemental_zip(2021), "*_FR FY 2021.txt"),
    2022: (_supplemental_zip(2022), "508_Compliant_Version_of_FR FY 2022.csv"),
    2023: (_supplemental_zip(2023), "*FR FY 2023.txt"),
    2024: (_supplemental_zip(2024), "*Variations FR FY 2024 tab .txt"),
    2025: (_supplemental_zip(2025), "508-Version-FR-FY-2025.csv"),
    2026: (
        "/files/zip/fy2026-hospital-readmissions-reduction-program-supplemental-data-file.zip",
        "FR FY 2026.txt",
    ),
    2027: (
        "/files/zip/fy-2027-hospital-readmissions-reduction-program-supplemental-data-file.zip",
        "*FR FY 2027 Tab.txt",
    ),
}
IPPS_CCN_HEADERS = ("PROV", "Provider", "Hospital CCN")
PDC_ENCODING = "utf-8-sig"
IPPS_ENCODING = "cp1252"
FIRST_PEER_GROUP_YEAR = 2019
# Columns every supplemental file from FY2019 carries, by normalized name (lower case, single
# spaces): capitalization changes by year ("Peer Group Assignment" in FY2019 and FY2020, "Peer
# group assignment" later). Per condition, each `err for <condition>` needs its three partners.
IPPS_PEER_COLUMNS = (
    "hospital ccn",
    "payment adjustment factor",
    "dual proportion",
    "peer group assignment",
    "neutrality modifier",
)
IPPS_CONDITION_COLUMNS = (
    "number of eligible discharges for",
    "peer group median err for",
    "drg payment ratio for",
)
# FY2023 dropped pneumonia (COVID), so five is the fewest conditions a peer-era file carries.
IPPS_MIN_CONDITIONS = 5


@dataclass(frozen=True)
class Snapshot:
    date: str
    url: str


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
    it, and the replacement lands in a different month each year (by 2020-01-04 for FY2020, not
    until 2026-02-25 for FY2026), so neither a fixed date nor a month rule finds the right one.
    The file's own performance period does. Taking the latest copy keeps any mid-year
    correction; checked on 2026-10-21, every copy within a fiscal year had identical rows.
    Snapshots without an HRRP file (2020-07-04, 2026-09-30) return None and are skipped.
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


def pdc_fiscal_year(zip_file: zipfile.ZipFile) -> int | None:
    member = find_member(zip_file.namelist(), PDC_MEMBER_PATTERNS)
    if member is None:
        return None
    with zip_file.open(member) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding=PDC_ENCODING))
        return fiscal_year_from_period_start(next(reader)["Start Date"])


def peek_pdc_fiscal_year(client: httpx.Client, snapshot: Snapshot) -> int | None:
    with HttpRangeFile.open(client, snapshot.url) as remote:
        return pdc_fiscal_year(zipfile.ZipFile(remote))


def parse_pdc_rows(text: str) -> list[dict[str, str]]:
    """PDC files are plain CSV with one header row; values stay strings as received."""
    return list(csv.DictReader(io.StringIO(text, newline="")))


def parse_ipps_rows(
    text: str, ccn_headers: Iterable[str] = IPPS_CCN_HEADERS, delimiter: str = "\t"
) -> list[dict[str, str]]:
    """IPPS files are tab-separated with title rows above the header and notes below the data.

    The header is the first row starting with a CCN column name; data rows are those whose first
    cell is a CCN. Header names are trimmed (FY2027 pads them with spaces) and blank trailing
    columns dropped; values stay strings as received. The IPPS impact files share the layout
    under a different CCN header (readmission.ingest.ipps_impact), and the FY2022 and FY2025
    supplemental files under a comma delimiter.
    """
    ccn_headers = tuple(ccn_headers)
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    start = next(i for i, row in enumerate(rows) if row and row[0].strip() in ccn_headers)
    header = [h.strip() for h in rows[start]]
    records = []
    for row in rows[start + 1 :]:
        if not row or not re.fullmatch(r"\d{1,6}", row[0].strip()):
            continue
        records.append({h: v for h, v in zip(header, row, strict=False) if h})
    return records


def read_pdc_rows(path: Path | str) -> list[dict[str, str]]:
    return parse_pdc_rows(Path(path).read_text(encoding=PDC_ENCODING))


def ipps_delimiter(name: str) -> str:
    return "," if name.lower().endswith(".csv") else "\t"


def read_ipps_rows(path: Path | str) -> list[dict[str, str]]:
    path = Path(path)
    return parse_ipps_rows(
        path.read_text(encoding=IPPS_ENCODING), delimiter=ipps_delimiter(path.name)
    )


def normalized_header(header: str) -> str:
    return " ".join(header.split()).lower()


def check_ipps_columns(rows: list[dict[str, str]], fiscal_year: int) -> None:
    """Fail when a peer-era supplemental file lacks a column the Silver tables read, rather than
    land a year whose peer medians or payment terms would silently come out null."""
    if fiscal_year < FIRST_PEER_GROUP_YEAR:
        return
    if not rows:
        raise ValueError(f"FY{fiscal_year} IPPS file: no data rows")
    headers = {normalized_header(h) for h in rows[0]}
    missing = [c for c in IPPS_PEER_COLUMNS if c not in headers]
    conditions = [h.removeprefix("err for ") for h in headers if h.startswith("err for ")]
    missing += [
        f"{prefix} {condition}"
        for condition in conditions
        for prefix in IPPS_CONDITION_COLUMNS
        if f"{prefix} {condition}" not in headers
    ]
    if missing:
        raise ValueError(f"FY{fiscal_year} IPPS file: missing columns {missing}")
    if len(conditions) < IPPS_MIN_CONDITIONS:
        raise ValueError(f"FY{fiscal_year} IPPS file: only conditions {sorted(conditions)}")


def land_member(
    zip_path: Path,
    patterns: Iterable[str],
    lander: Lander,
    source: str,
    batch_id: str,
    fiscal_year: int,
    entry: dict,
) -> dict:
    """Land the one matching file, bytes unchanged, then its manifest. Returns the file entry."""
    with zipfile.ZipFile(zip_path) as zip_file:
        member = find_member(zip_file.namelist(), patterns)
        if member is None:
            raise ValueError(f"{zip_path}: no member matches {tuple(patterns)}")
        data = zip_file.read(member)
        member_date = datetime(*zip_file.getinfo(member).date_time).date().isoformat()
    if source == PDC_SOURCE:
        name, rows = f"pdc_fy{fiscal_year}.csv", parse_pdc_rows(data.decode(PDC_ENCODING))
    else:
        name = f"ipps_fy{fiscal_year}{Path(member).suffix.lower()}"
        rows = parse_ipps_rows(data.decode(IPPS_ENCODING), delimiter=ipps_delimiter(name))
        check_ipps_columns(rows, fiscal_year)
    partition = str(fiscal_year)
    path = lander.land_file(source, batch_id, name, data, partition)
    entry = entry | {
        "name": name,
        "fiscal_year": fiscal_year,
        "member": member,
        "member_modified": member_date,
        "source_ref": f"{entry['url']}#{member}",
        "sha256": sha256_hex(data),
        "row_count": len(rows),
        "path": path,
    }
    land_manifest(lander, source, batch_id, [entry], partition)
    return entry


def fetch_pdc(
    client: httpx.Client,
    lander: Lander,
    batch_id: str,
    work_dir: Path,
    fiscal_years: Iterable[int] = PDC_FISCAL_YEARS,
) -> list[dict]:
    fiscal_years = list(fiscal_years)
    index = request_with_retry(client, PDC_INDEX_URL).json()
    selected = select_pdc_snapshots(
        theme_snapshots(index), lambda s: peek_pdc_fiscal_year(client, s), fiscal_years
    )
    missing = set(fiscal_years) - selected.keys()
    if missing:
        raise RuntimeError(f"no PDC snapshot reports fiscal years {sorted(missing)}")
    landed = []
    for fiscal_year, snapshot in sorted(selected.items()):
        zip_path = download_zip(client, snapshot.url, work_dir / "pdc" / Path(snapshot.url).name)
        entry = {"url": snapshot.url, "dataset_id": PDC_DATASET_ID, "snapshot_date": snapshot.date}
        landed.append(
            land_member(
                zip_path, PDC_MEMBER_PATTERNS, lander, PDC_SOURCE, batch_id, fiscal_year, entry
            )
        )
    return landed


def fetch_ipps(
    client: httpx.Client,
    lander: Lander,
    batch_id: str,
    work_dir: Path,
    fiscal_years: Iterable[int] = IPPS_FILES.keys(),
) -> list[dict]:
    landed = []
    for fiscal_year in sorted(fiscal_years):
        url_path, pattern = IPPS_FILES[fiscal_year]
        url = IPPS_BASE + url_path
        zip_path = download_zip(client, url, work_dir / "ipps" / f"fy{fiscal_year}.zip")
        entry = {"url": url, "dataset_id": None, "snapshot_date": None}
        landed.append(
            land_member(zip_path, [pattern], lander, IPPS_SOURCE, batch_id, fiscal_year, entry)
        )
    return landed
