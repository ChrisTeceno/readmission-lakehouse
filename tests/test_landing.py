import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from readmission.ingest.landing import (
    LANDING_VOLUME,
    LocalLander,
    VolumeLander,
    land_manifest,
    landing_path,
    new_batch_id,
    read_manifest,
)


def test_batch_id_is_sortable_utc_timestamp():
    assert new_batch_id(datetime(2026, 10, 21, 8, 5, 9, tzinfo=UTC)) == "20261021T080509Z"


def test_landing_path_layout():
    assert (
        landing_path(LANDING_VOLUME, "hrrp_history", "20261021T080509Z", "pdc_fy2019.csv")
        == "/Volumes/workspace/readmission/landing/hrrp_history/20261021T080509Z/pdc_fy2019.csv"
    )


@pytest.mark.parametrize("bad", ["", "a/b", "..", "."])
def test_landing_path_rejects_unsafe_components(bad):
    with pytest.raises(ValueError):
        landing_path(LANDING_VOLUME, "hrrp_history", "b1", bad)


def test_local_lander_writes_bytes_and_files(tmp_path):
    lander = LocalLander(str(tmp_path / "landing"))
    source_file = tmp_path / "raw.csv"
    source_file.write_bytes(b"a,b\n1,2\n")

    from_bytes = lander.land_file("cms", "b1", "one.csv", b"x\n")
    from_path = lander.land_file("cms", "b1", "two.csv", source_file)

    assert from_bytes == str(tmp_path / "landing" / "cms" / "b1" / "one.csv")
    assert Path(from_bytes).read_bytes() == b"x\n"
    assert Path(from_path).read_bytes() == b"a,b\n1,2\n"


class FakeFiles:
    def __init__(self):
        self.uploads = {}

    def upload(self, file_path, contents: io.BufferedIOBase, *, overwrite=None):
        self.uploads[file_path] = (contents.read(), overwrite)


class FakeWorkspace:
    def __init__(self):
        self.files = FakeFiles()


def test_volume_lander_uploads_through_files_api():
    workspace = FakeWorkspace()

    path = VolumeLander(workspace).land_file("cms", "b1", "one.csv", b"x\n")

    assert path == f"{LANDING_VOLUME}/cms/b1/one.csv"
    assert workspace.files.uploads == {path: (b"x\n", True)}


def test_manifest_round_trip(tmp_path):
    lander = LocalLander(str(tmp_path))
    entries = [{"name": "pdc_fy2019.csv", "fiscal_year": 2019}]

    land_manifest(lander, "hrrp_history", "b1", entries)

    assert read_manifest(str(tmp_path), "hrrp_history", "b1") == {
        "source": "hrrp_history",
        "batch_id": "b1",
        "files": entries,
    }
