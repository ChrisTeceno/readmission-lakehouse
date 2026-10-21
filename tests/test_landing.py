import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from readmission.ingest.landing import (
    LANDING_VOLUME,
    LocalLander,
    VolumeLander,
    batches_to_load,
    land_manifest,
    landing_path,
    list_manifests,
    new_batch_id,
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


def test_partition_sits_between_source_and_batch():
    assert (
        landing_path("/v", "hrrp_history_pdc", "b1", "pdc_fy2019.csv", partition="2019")
        == "/v/hrrp_history_pdc/2019/b1/pdc_fy2019.csv"
    )


def test_manifests_listed_oldest_first(tmp_path):
    lander = LocalLander(str(tmp_path))
    land_manifest(lander, "hrrp_history_pdc", "b2", [{"name": "a", "sha256": "h2"}], "2019")
    land_manifest(lander, "hrrp_history_pdc", "b1", [{"name": "a", "sha256": "h1"}], "2020")
    land_manifest(lander, "hrrp_history_pdc", "b1", [{"name": "a", "sha256": "h1b"}], "2019")

    manifests = list_manifests(str(tmp_path), "hrrp_history_pdc")

    assert [(m["batch_id"], m["partition"]) for m in manifests] == [
        ("b1", "2019"),
        ("b1", "2020"),
        ("b2", "2019"),
    ]
    assert manifests[0]["dir"] == str(tmp_path / "hrrp_history_pdc" / "2019" / "b1")
    assert list_manifests(str(tmp_path), "missing_source") == []


def test_batches_to_load_skips_loaded_batches_and_unchanged_files():
    manifests = [
        {"batch_id": "b1", "files": [{"sha256": "h1"}]},
        {"batch_id": "b2", "files": [{"sha256": "h1"}]},
        {"batch_id": "b3", "files": [{"sha256": "h3"}]},
        {"batch_id": "b4", "files": [{"sha256": "h3"}]},
        {"batch_id": "b5", "files": [{"sha256": "h5"}]},
    ]

    pending = batches_to_load(manifests, loaded_batch_ids={"b5"}, loaded_sha256s={"h1"})

    # b2 re-fetched unchanged data, b4 repeats b3 within the same run, b5 is already loaded.
    assert [m["batch_id"] for m in pending] == ["b3"]


def test_manifest_round_trip(tmp_path):
    lander = LocalLander(str(tmp_path))
    entries = [{"name": "pdc_fy2019.csv", "fiscal_year": 2019}]

    land_manifest(lander, "hrrp_history", "b1", entries)

    assert list_manifests(str(tmp_path), "hrrp_history") == [
        {
            "source": "hrrp_history",
            "partition": None,
            "batch_id": "b1",
            "files": entries,
            "dir": str(tmp_path / "hrrp_history" / "b1"),
        }
    ]
