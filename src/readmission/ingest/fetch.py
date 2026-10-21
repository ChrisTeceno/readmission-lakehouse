"""Fetch CLI: download one source and land its raw files with manifests.

    uv run python -m readmission.ingest.fetch <source> [--local DIR] [--batch-id ID]

By default files land in the Databricks landing volume through the Files API (credentials from
DATABRICKS_HOST/DATABRICKS_TOKEN or a CLI profile); `--local` lands in a directory instead.
A source is a function taking (client, lander, batch_id, work_dir) and returning the manifest
entries it landed; add new ones to SOURCES.
"""

import argparse
from collections.abc import Callable
from pathlib import Path

import httpx

from readmission.ingest import hrrp_history
from readmission.ingest.http import make_client
from readmission.ingest.landing import Lander, LocalLander, VolumeLander, new_batch_id

Fetcher = Callable[[httpx.Client, Lander, str, Path], list[dict]]

SOURCES: dict[str, Fetcher] = {
    hrrp_history.PDC_SOURCE: hrrp_history.fetch_pdc,
    hrrp_history.IPPS_SOURCE: hrrp_history.fetch_ipps,
}


def run(
    source: str,
    lander: Lander,
    work_dir: Path,
    batch_id: str | None = None,
    client: httpx.Client | None = None,
) -> list[dict]:
    fetcher = SOURCES[source]
    with client or make_client() as http:
        return fetcher(http, lander, batch_id or new_batch_id(), work_dir)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Download a source into the landing zone.")
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("--local", type=Path, help="land in this directory, not the volume")
    parser.add_argument("--work-dir", type=Path, default=Path("data/fetch"))
    parser.add_argument("--batch-id")
    parser.add_argument("--profile", help="Databricks CLI profile for the Files API")
    args = parser.parse_args(argv)
    if args.local:
        lander: Lander = LocalLander(str(args.local))
    else:
        from databricks.sdk import WorkspaceClient

        lander = VolumeLander(WorkspaceClient(profile=args.profile))
    for entry in run(args.source, lander, args.work_dir / args.source, args.batch_id):
        print(f"{entry['path']} sha256={entry['sha256'][:12]} rows={entry.get('row_count')}")


if __name__ == "__main__":
    main()
