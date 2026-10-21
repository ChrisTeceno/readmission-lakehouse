"""Fetch Census ACS 5-year county socioeconomics and parse them for Bronze.

Source `acs_county` (see docs/data-sources.md). Per vintage, two Census API responses land
unchanged under `acs_county/<vintage>/<batch_id>/`:
- `county_<vintage>.json`: the six measures for every county from the data profile tables
  (`/data/<vintage>/acs/acs5/profile`, `for=county:*`), plus NAME, which is the geography list
  hospitals are matched against.
- `ct_county_subdivision_<vintage>.json`: Connecticut's towns and the county each belongs to.
  From the 2022 vintage Census publishes Connecticut by planning region, which CMS's county
  names do not use, so Connecticut hospitals are matched through their town instead.

A published ACS vintage does not change, so fetch skips a vintage that already has a landed
manifest. The API key comes from `get_secret` and never reaches a manifest, a URL in a log line
or an error message.
"""

import json
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

from readmission import secrets
from readmission.ingest.http import request_with_retry
from readmission.ingest.landing import Lander, land_manifest, list_manifests, sha256_hex

SOURCE = "acs_county"
SECRET_KEY = "census_api_key"

API_BASE = "https://api.census.gov/data"
# The 2025 vintage is due in December 2026; add it here, with its variable codes, once
# published. Fiscal years map to vintages in readmission.silver.acs_county.
LATEST_VINTAGE = 2024
VINTAGES = range(2011, LATEST_VINTAGE + 1)

# Data profile variable codes per vintage, read from each vintage's
# /acs/acs5/profile/variables.json (checked 2026-10-21). Codes move when Census reorders a
# table: 65 and over moved from DP05_0021PE to DP05_0024PE in 2017, and disability from
# DP02_0071PE to DP02_0072PE in 2019. Puerto Rico's disability percent is only in the DP02PR
# table, so both codes are requested and Silver takes whichever is present. The 2011 vintage
# answers -888888888 (not applicable) for uninsured and disability in every county.
MEASURES = (
    "population",
    "pct_age_65_plus",
    "median_household_income",
    "pct_below_poverty",
    "pct_uninsured",
    "pct_disability",
    "pct_disability_pr",
)
_CODES_2011 = (
    "DP05_0001E",
    "DP05_0021PE",
    "DP03_0062E",
    "DP03_0128PE",
    "DP03_0099PE",
    "DP02_0071PE",
    "DP02PR_0071PE",
)
_CODES_2017 = (
    "DP05_0001E",
    "DP05_0024PE",
    "DP03_0062E",
    "DP03_0128PE",
    "DP03_0099PE",
    "DP02_0071PE",
    "DP02PR_0071PE",
)
_CODES_2019 = (
    "DP05_0001E",
    "DP05_0024PE",
    "DP03_0062E",
    "DP03_0128PE",
    "DP03_0099PE",
    "DP02_0072PE",
    "DP02PR_0072PE",
)


def variables(vintage: int) -> dict[str, str]:
    """Measure name to the vintage's profile variable code."""
    if vintage not in VINTAGES:
        raise ValueError(f"no variable codes recorded for ACS vintage {vintage}")
    codes = _CODES_2011 if vintage < 2017 else _CODES_2017 if vintage < 2019 else _CODES_2019
    return dict(zip(MEASURES, codes, strict=True))


def county_url(vintage: int) -> str:
    codes = ",".join(variables(vintage).values())
    return f"{API_BASE}/{vintage}/acs/acs5/profile?get=NAME,{codes}&for=county:*"


def ct_county_subdivision_url(vintage: int) -> str:
    return f"{API_BASE}/{vintage}/acs/acs5?get=NAME&for=county%20subdivision:*&in=state:09"


class MissingCensusKey(RuntimeError):
    pass


def census_get(
    client: httpx.Client, url: str, api_key: str | None, attempts: int = 3
) -> httpx.Response:
    """GET a Census API query. Census answers a query without a usable key with a redirect to
    /data/missing_key.html, which it also does now and then for a valid key, so that redirect is
    retried when a key was sent and fatal when none was."""
    request_url = f"{url}&key={api_key}" if api_key else url
    for attempt in range(attempts):
        try:
            response = request_with_retry(client, request_url)
        except httpx.HTTPStatusError as error:
            # The message would carry the request URL, key included.
            raise RuntimeError(f"{url}: HTTP {error.response.status_code}") from None
        if not response.url.path.endswith("missing_key.html"):
            if "json" not in response.headers.get("content-type", ""):
                # An invalid key lands on another HTML page; its path carries no query string.
                raise RuntimeError(f"{url}: not a JSON response, ended at {response.url.path}")
            return response
        if api_key is None:
            raise MissingCensusKey(
                f"api.census.gov refused a keyless request ({url}). Store a Census API key in "
                f"the Databricks secret scope '{secrets.SCOPE}', key '{SECRET_KEY}'."
            )
    raise RuntimeError(f"{url}: Census rejected the API key {attempts} times")


def parse_census_rows(payload: bytes | str) -> list[dict[str, str | None]]:
    """A Census API response is a JSON array of arrays, header row first. Values stay strings as
    received (null where Census has no value); the geography codes come last."""
    header, *rows = json.loads(payload)
    for row in rows:
        if len(row) != len(header):
            raise ValueError(f"row has {len(row)} values, header has {len(header)}: {row}")
    return [dict(zip(header, row, strict=True)) for row in rows]


def read_census_rows(path: Path | str) -> list[dict[str, str | None]]:
    return parse_census_rows(Path(path).read_bytes())


def landed_vintages(root: str) -> set[int]:
    return {int(m["partition"]) for m in list_manifests(root, SOURCE)}


def fetch_acs_county(
    client: httpx.Client,
    lander: Lander,
    batch_id: str,
    work_dir: Path,
    vintages: Iterable[int] = VINTAGES,
    read_secret: Callable[[str, str], str | None] = secrets.get_secret,
) -> list[dict]:
    """Land every vintage not landed yet: both responses, then the vintage's manifest."""
    pending = sorted(set(vintages) - landed_vintages(lander.root))
    if not pending:
        return []
    api_key = read_secret(secrets.SCOPE, SECRET_KEY)
    if api_key is None:
        print(f"{SOURCE}: no secret {secrets.SCOPE}/{SECRET_KEY}, calling the Census API keyless")
    landed = []
    for vintage in pending:
        partition = str(vintage)
        entries = []
        for geography, name, url in (
            ("county", f"county_{vintage}.json", county_url(vintage)),
            (
                "county subdivision",
                f"ct_county_subdivision_{vintage}.json",
                ct_county_subdivision_url(vintage),
            ),
        ):
            data = census_get(client, url, api_key).content
            path = lander.land_file(SOURCE, batch_id, name, data, partition)
            entries.append(
                {
                    "name": name,
                    "url": url,
                    "vintage": vintage,
                    "geography": geography,
                    "variables": variables(vintage) if geography == "county" else {},
                    "sha256": sha256_hex(data),
                    "row_count": len(parse_census_rows(data)),
                    "path": path,
                }
            )
        land_manifest(lander, SOURCE, batch_id, entries, partition)
        landed.extend(entries)
    return landed
