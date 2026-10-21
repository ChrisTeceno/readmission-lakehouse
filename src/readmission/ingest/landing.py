"""Landing zone for raw source files, shared by every fetch step.

Fetch steps download from the internet and hand the raw bytes to a lander; load steps read
`<root>/<source>/<batch_id>/` into Bronze and never call the internet. On Databricks the root
is the managed volume `workspace.readmission.landing`; locally it is a plain directory.
"""

import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Protocol

LANDING_VOLUME = "/Volumes/workspace/readmission/landing"
MANIFEST_NAME = "_manifest.json"


def new_batch_id(now: datetime | None = None) -> str:
    """UTC timestamp, so batch directories sort in load order."""
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def landing_path(root: str, source: str, batch_id: str, name: str) -> str:
    for part in (source, batch_id, name):
        if not part or "/" in part or part in (".", ".."):
            raise ValueError(f"invalid landing path component: {part!r}")
    return f"{root.rstrip('/')}/{source}/{batch_id}/{name}"


def _as_stream(data: bytes | Path) -> BinaryIO:
    return io.BytesIO(data) if isinstance(data, bytes) else data.open("rb")


class Lander(Protocol):
    root: str

    def land_file(self, source: str, batch_id: str, name: str, data: bytes | Path) -> str:
        """Store one raw file and return the path it landed at."""
        ...


@dataclass
class LocalLander:
    root: str

    def land_file(self, source: str, batch_id: str, name: str, data: bytes | Path) -> str:
        target = Path(landing_path(self.root, source, batch_id, name))
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

    def land_file(self, source: str, batch_id: str, name: str, data: bytes | Path) -> str:
        target = landing_path(self.root, source, batch_id, name)
        with _as_stream(data) as stream:
            self.workspace.files.upload(target, stream, overwrite=True)
        return target


def land_manifest(lander: Lander, source: str, batch_id: str, entries: list[dict]) -> str:
    """One JSON manifest per batch tells the load step what each landed file is."""
    body = json.dumps({"source": source, "batch_id": batch_id, "files": entries}, indent=2)
    return lander.land_file(source, batch_id, MANIFEST_NAME, body.encode())


def read_manifest(root: str, source: str, batch_id: str) -> dict:
    """Volume paths are ordinary file paths on Databricks compute, so plain open() works."""
    return json.loads(Path(landing_path(root, source, batch_id, MANIFEST_NAME)).read_text())
