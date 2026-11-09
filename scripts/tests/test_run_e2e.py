from pathlib import Path

import yaml

from scripts.run_e2e import _profile, _real_timed_profile


def test_browser_runner_writes_legacy_and_collection_profiles(tmp_path: Path) -> None:
    profiles_path = tmp_path / "profiles.yaml"
    known_hosts = tmp_path / "known_hosts"
    timed_root = tmp_path / "timed"
    idr0080_root = tmp_path / "idr0080-like"
    idr0080_index = idr0080_root / "idr0080-raw.index.parquet"
    qc_export = tmp_path / "qc-export"
    local_qc_annotations = tmp_path / "local-qc-annotations"
    timed_root.mkdir()
    idr0080_root.mkdir()
    idr0080_index.touch()

    _profile(
        profiles_path,
        "/cluster/timed",
        str(timed_root),
        "/cluster/idr0080-like/idr0080-raw.index.parquet",
        str(idr0080_index),
        known_hosts,
        cluster_qc_export="/cluster/qc-export",
        local_qc_export=str(qc_export),
        cluster_qc_annotations="/cluster/qc-annotations",
        local_qc_annotations=str(local_qc_annotations),
    )

    document = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
    profiles = {profile["id"]: profile for profile in document["profiles"]}
    assert list(profiles) == [
        "test-cluster",
        "local",
        "test-cluster-datasets",
        "local-datasets",
    ]
    assert "collections" not in profiles["test-cluster"]
    assert "collections" not in profiles["local"]
    assert profiles["test-cluster-datasets"]["data_roots"] == [
        "/cluster/timed",
        "/cluster/idr0080-like",
    ]
    assert profiles["local-datasets"]["data_roots"] == [
        str(timed_root),
        str(idr0080_root),
    ]
    assert profiles["local-datasets"]["collections"] == [
        {
            "id": "timed",
            "title": "TIMED",
            "description": "Live-cell time-lapse · test fixture · 3 channels",
            "default": True,
            "path": str(timed_root),
            "masks_root": str(timed_root / "timed-masks"),
            "segmentation_root": str(timed_root / "timed-seg"),
        },
        {
            "id": "idr0080",
            "title": "Cell Health",
            "description": "idr0080-like CRISPR Cell Painting test collection",
            "path": str(idr0080_index),
            "qc_export": str(qc_export),
            "qc_annotations": str(local_qc_annotations),
        },
    ]
    cluster_timed = profiles["test-cluster-datasets"]["collections"][0]
    assert cluster_timed["masks_root"] == "/cluster/timed/timed-masks"
    assert cluster_timed["segmentation_root"] == "/cluster/timed/timed-seg"
    assert profiles["test-cluster-datasets"]["collections"][1] == {
        "id": "idr0080",
        "title": "Cell Health",
        "description": "idr0080-like CRISPR Cell Painting test collection",
        "path": "/cluster/idr0080-like/idr0080-raw.index.parquet",
        "qc_export": "/cluster/qc-export",
        "qc_annotations": "/cluster/qc-annotations",
    }


def test_browser_runner_writes_real_timed_copy_profile(tmp_path: Path) -> None:
    profiles_path = tmp_path / "profiles.yaml"
    copy_root = tmp_path / "TIMED"

    _real_timed_profile(profiles_path, copy_root)

    document = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
    assert document == {
        "profiles": [
            {
                "id": "local-timed",
                "name": "Local TIMED (external drive)",
                "kind": "local",
                "data_roots": [str(copy_root / "zarr")],
                "metadata_root": str(copy_root / "metadata"),
                "collections": [
                    {
                        "id": "timed",
                        "title": "TIMED",
                        "description": (
                            "Live-cell time-lapse · PB100140_P2.1 · "
                            "48 h, hourly · 3 channels"
                        ),
                        "path": str(copy_root / "zarr"),
                        "default": True,
                    }
                ],
            }
        ]
    }


def test_real_timed_copy_profile_maps_its_masks_when_the_copy_has_them(tmp_path: Path) -> None:
    profiles_path = tmp_path / "profiles.yaml"
    copy_root = tmp_path / "TIMED"
    (copy_root / "corrected").mkdir(parents=True)

    _real_timed_profile(profiles_path, copy_root)

    document = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
    collection = document["profiles"][0]["collections"][0]
    assert collection["path"] == str(copy_root / "zarr")
    assert collection["masks_root"] == str(copy_root / "corrected")
    assert "segmentation_root" not in collection

    (copy_root / "seg").mkdir()
    _real_timed_profile(profiles_path, copy_root)
    document = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
    collection = document["profiles"][0]["collections"][0]
    assert collection["masks_root"] == str(copy_root / "corrected")
    assert collection["segmentation_root"] == str(copy_root / "seg")


def test_local_only_runner_writes_only_the_local_profiles(tmp_path: Path) -> None:
    profiles_path = tmp_path / "profiles.yaml"
    timed_root = tmp_path / "timed"
    idr0080_index = tmp_path / "idr0080-like" / "idr0080-raw.index.parquet"

    _profile(
        profiles_path,
        "/unused",
        str(timed_root),
        "/unused/idr0080-raw.index.parquet",
        str(idr0080_index),
        tmp_path / "known_hosts",
        cluster_qc_export="/unused",
        local_qc_export=str(tmp_path / "qc-export"),
        cluster_qc_annotations="/unused",
        local_qc_annotations=str(tmp_path / "local-qc-annotations"),
        include_cluster=False,
    )

    document = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
    assert [profile["id"] for profile in document["profiles"]] == ["local", "local-datasets"]
    assert "/unused" not in profiles_path.read_text(encoding="utf-8")
