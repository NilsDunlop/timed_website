from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app

from scripts import make_test_data

TOKEN = "packed-route-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def packed_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate packed data separately from the frozen TIMED server fixture."""
    output = tmp_path_factory.mktemp("packed-route-data")
    return make_test_data._write_packed_fixture(output)


def _settings(root: Path, state_dir: Path) -> ServerSettings:
    return ServerSettings(
        data_roots=(root,),
        state_dir=state_dir,
        environment="local",
    )


def _entry(
    name: str,
    path: Path,
    kind: str,
    *,
    detail: str | None = None,
    ome_zarr: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "name": name,
        "path": str(path),
        "kind": kind,
        "ome_zarr": ome_zarr,
    }
    if detail is not None:
        result["detail"] = detail
    return result


def test_parent_index_and_experiment_listings_are_exact(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    root = packed_root.resolve()
    index = (root / make_test_data.PACKED_INDEX_NAME).resolve()
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        parent = client.get("/api/fs/list", headers=AUTH, params={"path": str(root)})
        assert parent.status_code == 200, parent.text
        assert parent.json() == {
            "path": str(root),
            "entries": [
                _entry(
                    f"idr0080-raw-SQ000{plate}.tar",
                    root / f"idr0080-raw-SQ000{plate}.tar",
                    "file",
                )
                for plate in (14610, 14611, 14613)
            ]
            + [
                _entry(
                    make_test_data.PACKED_INDEX_NAME,
                    index,
                    "dir",
                    detail="2 cell lines \u00b7 3 plates",
                ),
                _entry("index_fragments", root / "index_fragments", "dir"),
                _entry("masks", root / "masks", "dir"),
                _entry("metadata", root / "metadata", "dir"),
            ],
        }

        index_listing = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(index)},
        )
        assert index_listing.status_code == 200, index_listing.text
        assert index_listing.json() == {
            "path": str(index),
            "entries": [
                _entry(
                    "idr0080-A549",
                    index / "idr0080-A549",
                    "dir",
                    detail="2 plates",
                ),
                _entry(
                    "idr0080-ES2",
                    index / "idr0080-ES2",
                    "dir",
                    detail="1 plate",
                ),
            ],
        }

        experiment = index / "idr0080-A549"
        experiment_listing = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(experiment)},
        )
        assert experiment_listing.status_code == 200, experiment_listing.text
        plate_info = {"kind": "plate", "version": "0.5", "zarr_format": 3}
        assert experiment_listing.json() == {
            "path": str(experiment),
            "entries": [
                _entry(
                    f"SQ000{plate}",
                    experiment / str(plate),
                    "dir",
                    detail="5 wells \u00b7 2 sites \u00b7 5 channels",
                    ome_zarr=plate_info,
                )
                for plate in (14610, 14611)
            ],
        }


def test_plate_link_is_idempotent_and_persists_across_apps(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    plate = index / "idr0080-A549" / "14610"
    state_dir = tmp_path / "persistent-state"
    settings = _settings(packed_root, state_dir)

    with TestClient(create_app(settings, TOKEN)) as first_client:
        first = first_client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(plate)},
        )
        assert first.status_code == 201, first.text
        record = first.json()
        assert {key: record[key] for key in ("path", "name", "ome_zarr")} == {
            "path": str(plate),
            "name": "SQ00014610",
            "ome_zarr": {"kind": "plate", "version": "0.5", "zarr_format": 3},
        }
        assert datetime.fromisoformat(record["linked_at"]).tzinfo is not None

        repeated = first_client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(plate)},
        )
        assert repeated.status_code == 201, repeated.text
        assert repeated.json() == record
        assert first_client.get("/api/datasets", headers=AUTH).json() == {"datasets": [record]}

    with TestClient(create_app(settings, TOKEN)) as restarted_client:
        assert restarted_client.get("/api/datasets", headers=AUTH).json() == {"datasets": [record]}
        reopened = restarted_client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(plate)},
        )
        assert reopened.status_code == 201, reopened.text
        assert reopened.json() == record


@pytest.mark.parametrize("suffix", [(), ("idr0080-A549",)])
def test_index_and_experiment_cannot_be_linked(
    packed_root: Path,
    tmp_path: Path,
    suffix: tuple[str, ...],
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    path = index.joinpath(*suffix)
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        response = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(path)},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "Link a packed plate, not an index or experiment"}


def test_parent_segments_are_forbidden_for_listing_and_linking(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    unsafe = f"{index}{os.sep}..{os.sep}idr0080-A549"
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        listing = client.get("/api/fs/list", headers=AUTH, params={"path": unsafe})
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": unsafe},
        )
    for response in (listing, linking):
        assert response.status_code == 403
        assert response.json() == {"detail": "Parent path segments are not allowed"}


@pytest.mark.parametrize(
    ("suffix", "message"),
    [
        (("unknown",), "Unknown packed experiment"),
        (("idr0080-A549", "99999"), "Unknown packed plate"),
        (("idr0080-A549", "14610", "extra"), "Unknown packed dataset path"),
    ],
)
def test_unknown_virtual_paths_return_not_found_for_listing_and_linking(
    packed_root: Path,
    tmp_path: Path,
    suffix: tuple[str, ...],
    message: str,
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    unknown = index.joinpath(*suffix)
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(unknown)})
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(unknown)},
        )
    for response in (listing, linking):
        assert response.status_code == 404
        assert message in response.json()["detail"]


def test_fragment_and_mask_indexes_are_not_packed_routes(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    decoys = (
        packed_root / "index_fragments" / "SQ00014610.index.parquet",
        packed_root / "masks" / "fixture" / "Plate14610.index.parquet",
    )
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        for decoy in decoys:
            parent = client.get(
                "/api/fs/list",
                headers=AUTH,
                params={"path": str(decoy.parent)},
            )
            assert parent.status_code == 200, parent.text
            entry = next(item for item in parent.json()["entries"] if item["name"] == decoy.name)
            assert entry == _entry(decoy.name, decoy.resolve(), "file")

            listing = client.get(
                "/api/fs/list",
                headers=AUTH,
                params={"path": str(decoy)},
            )
            linking = client.post(
                "/api/datasets",
                headers=AUTH,
                json={"path": str(decoy)},
            )
            assert listing.status_code == 404
            assert linking.status_code == 404


def test_malformed_detected_catalogue_returns_unprocessable(tmp_path: Path) -> None:
    root = make_test_data._write_packed_fixture(tmp_path / "generated")
    source = pq.read_table(root / make_test_data.PACKED_INDEX_NAME)
    malformed = root / "malformed.index.parquet"
    pq.write_table(pa.concat_tables([source.slice(0, 1), source.slice(0, 1)]), malformed)

    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        listing = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(malformed)},
        )
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(malformed / "idr0080-A549" / "14610")},
        )
    for response in (listing, linking):
        assert response.status_code == 422
        assert "duplicate field/channel key" in response.json()["detail"]


def test_symlinked_index_outside_root_is_forbidden(tmp_path: Path) -> None:
    outside = make_test_data._write_packed_fixture(tmp_path / "outside-generated")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / make_test_data.PACKED_INDEX_NAME
    try:
        link.symlink_to(outside / make_test_data.PACKED_INDEX_NAME)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Creating a file symlink is unavailable: {exc}")

    virtual_plate = link / "idr0080-A549" / "14610"
    with TestClient(create_app(_settings(allowed, tmp_path / "state"), TOKEN)) as client:
        listing = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(link)},
        )
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(virtual_plate)},
        )
    for response in (listing, linking):
        assert response.status_code == 403
        assert response.json() == {"detail": "Path is outside the configured data roots"}
