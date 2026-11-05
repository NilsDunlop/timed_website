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
