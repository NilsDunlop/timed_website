from pathlib import Path

import yaml

from scripts.run_e2e import _profile


def test_browser_runner_writes_legacy_and_collection_profiles(tmp_path: Path) -> None:
    profiles_path = tmp_path / "profiles.yaml"
    known_hosts = tmp_path / "known_hosts"
    timed_root = tmp_path / "timed"
    idr0080_root = tmp_path / "idr0080-like"
    idr0080_index = idr0080_root / "idr0080-raw.index.parquet"
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
            "title": "idr0080 · Cell Health",
            "description": "CRISPR Cell Painting · test collection",
            "path": str(idr0080_index),
        },
    ]
