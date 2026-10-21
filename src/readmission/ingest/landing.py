"""Landing zone for raw source files, shared by every fetch step.

Fetch steps download from the internet and hand the raw bytes to a lander; load steps read the
landing root into Bronze and never call the internet. On Databricks the root is the managed
volume `workspace.readmission.landing` (defined in resources/landing.yml); locally it is a plain
directory. Layout: `<root>/<source>/[<partition>/]<batch_id>/<file>`, with one `_manifest.json`
per batch describing its files.
"""

import hashlib
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Protocol

LANDING_VOLUME = "/Volumes/workspace/readmission/landing"
MANIFEST_NAME = "_manifest.json"


def new_batch_id(now: datetime | None = None) -> str:
    """UTC timestamp, so batch directories sort in load order."""
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def landing_path(
    root: str, source: str, batch_id: str, name: str, partition: str | None = None
) -> str:
    parts = [source, *([partition] if partition is not None else []), batch_id, name]
    for part in parts:
        if not part or "/" in part or part in (".", ".."):
            raise ValueError(f"invalid landing path component: {part!r}")
    return "/".join([root.rstrip("/"), *parts])


def _as_stream(data: bytes | Path) -> BinaryIO:
    return io.BytesIO(data) if isinstance(data, bytes) else data.open("rb")


class Lander(Protocol):
    root: str

    def land_file(
        self,
        source: str,
        batch_id: str,
        name: str,
        data: bytes | Path,
        partition: str | None = None,
    ) -> str:
        """Store one raw file and return the path it landed at."""
        ...


@dataclass
class LocalLander:
    root: str

    def land_file(
        self,
        source: str,
        batch_id: str,
        name: str,
        data: bytes | Path,
        partition: str | None = None,
    ) -> str:
        target = Path(landing_path(self.root, source, batch_id, name, partition))
        target.parent.mkdir(parents=True, exist_ok=True)
        with _as_stream(data) as stream, target.open("wb") as out:
            while chunk := stream.read(1 << 20):
                out.write(chunk)
        return str(target)


@dataclass
class VolumeLander:
    """Uploads through the Databricks Files API (a `databricks.sdk.WorkspaceClient`)."""

    workspace: Any
    root: str = LANDING_VOLUME

    def land_file(
        self,
        source: str,
        batch_id: str,
        name: str,
        data: bytes | Path,
        partition: str | None = None,
    ) -> str:
        target = landing_path(self.root, source, batch_id, name, partition)
        with _as_stream(data) as stream:
            self.workspace.files.upload(target, stream, overwrite=True)
        return target


def land_manifest(
    lander: Lander,
    source: str,
    batch_id: str,
    files: list[dict],
    partition: str | None = None,
) -> str:
    """Written after the files, so a batch with a manifest is complete.

    Each file entry should carry at least `name`, `url` and `sha256`; sources add what they know
    (dataset id, CMS `modified` or snapshot date, inner file name, row count).
    """
    manifest = {"source": source, "partition": partition, "batch_id": batch_id, "files": files}
    body = json.dumps(manifest, indent=2).encode()
    return lander.land_file(source, batch_id, MANIFEST_NAME, body, partition)


def list_manifests(root: str, source: str) -> list[dict]:
    """Every complete batch of a source, oldest first. Volume paths are plain file paths on
    Databricks compute, so this needs no SDK; each manifest gains its `dir`."""
    manifests = []
    for path in Path(root, source).rglob(MANIFEST_NAME):
        manifests.append(json.loads(path.read_text()) | {"dir": str(path.parent)})
    return sorted(manifests, key=lambda m: (m["batch_id"], m["partition"] or ""))


def batches_to_load(
    manifests: Iterable[dict], loaded_batch_ids: set[str], loaded_sha256s: set[str]
) -> list[dict]:
    """Load is idempotent: skip a batch already loaded, and a batch whose every file has the
    same sha256 as something already loaded (a re-fetch of unchanged data)."""
    pending = []
    for manifest in manifests:
        hashes = {f["sha256"] for f in manifest["files"]}
        if manifest["batch_id"] in loaded_batch_ids or hashes <= loaded_sha256s:
            continue
        pending.append(manifest)
        loaded_sha256s = loaded_sha256s | hashes
    return pending
