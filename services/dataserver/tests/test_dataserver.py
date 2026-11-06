from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import httpx
import numpy as np
import pytest
import zarr
from fastapi.testclient import TestClient
from served_stores import write_berzelius_plate, write_json, write_native_plate
from timed_dataserver import __version__
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.cli import main, serve
from timed_dataserver.omezarr import OmeZarrError, _channels, _timed_field_metadata
from zarr.storage import FsspecStore

from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "test-only-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory, write_audit: WriteAudit) -> Path:
    root = tmp_path_factory.mktemp("dataserver-data")
    generate_test_data(root, include_packed=False)
    (root / "ordinary").mkdir()
    (root / "notes.txt").write_text("not a dataset\n", encoding="utf-8")
    # The native-plate test adds its own store to this shared root.
    write_audit.watch(root, allow=(root / "native_plate.zarr",))
    return root


@pytest.fixture
def client_factory(data_root: Path, tmp_path: Path) -> Callable[[Path | None], TestClient]:
    def make_client(state_dir: Path | None = None) -> TestClient:
        settings = ServerSettings(
            data_roots=(data_root,),
            state_dir=state_dir or tmp_path / "state",
            environment="local",
            metadata_root=data_root / "metadata",
        )
        return TestClient(create_app(settings, TOKEN))

    return make_client


def _link(client: TestClient, path: Path, name: str | None = None) -> dict[str, object]:
    body: dict[str, str] = {"path": str(path)}
    if name is not None:
        body["name"] = name
    response = client.post("/api/datasets", headers=AUTH, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _omit_channel_window_ends(channels: object) -> None:
    assert isinstance(channels, list)
    for channel in channels:
        assert isinstance(channel, dict)
        window = channel.get("window")
        assert isinstance(window, dict)
        assert "end" in window
        del window["end"]


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong-token"}, 401),
        (AUTH, 200),
    ],
)
def test_bearer_authentication(
    client_factory: Callable[[Path | None], TestClient],
    headers: dict[str, str],
    expected: int,
) -> None:
    with client_factory() as client:
        response = client.get("/api/handshake", headers=headers)
    assert response.status_code == expected
    if expected == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def test_handshake_and_listing_detect_every_fixture(
    client_factory: Callable[[Path | None], TestClient], data_root: Path
) -> None:
    with client_factory() as client:
        handshake = client.get("/api/handshake", headers=AUTH)
        assert handshake.status_code == 200
        payload = handshake.json()
        assert payload["api_version"] == "1.0"
        assert payload["environment"] == "local"
        assert payload["data_roots"] == [str(data_root.resolve())]
        assert payload["capabilities"] == {
            "gpu": False,
            "plate_metadata": True,
            "packed_datasets": True,
            "qc_review": False,
            "qc_annotations_writable": False,
        }

        roots = client.get("/api/fs/list", headers=AUTH)
        assert roots.status_code == 200
        assert roots.json()["entries"][0]["path"] == str(data_root.resolve())

        listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(data_root)})
        assert listing.status_code == 200
        assert listing.json() == {
            "path": str(data_root.resolve()),
            "entries": [
                {
                    "name": "metadata",
                    "path": str((data_root / "metadata").resolve()),
                    "kind": "dir",
                    "ome_zarr": None,
                },
                {
                    "name": "notes.txt",
                    "path": str((data_root / "notes.txt").resolve()),
                    "kind": "file",
                    "ome_zarr": None,
                },
                {
                    "name": "ordinary",
                    "path": str((data_root / "ordinary").resolve()),
                    "kind": "dir",
                    "ome_zarr": None,
                },
                {
                    "name": "test_image_v04.zarr",
                    "path": str((data_root / "test_image_v04.zarr").resolve()),
                    "kind": "dir",
                    "ome_zarr": {
                        "kind": "image",
                        "version": "0.4",
                        "zarr_format": 2,
                    },
                },
                {
                    "name": "test_image_v05.zarr",
                    "path": str((data_root / "test_image_v05.zarr").resolve()),
                    "kind": "dir",
                    "ome_zarr": {
                        "kind": "image",
                        "version": "0.5",
                        "zarr_format": 3,
                    },
                },
                {
                    "name": "test_plate.zarr",
                    "path": str((data_root / "test_plate.zarr").resolve()),
                    "kind": "dir",
                    "ome_zarr": {
                        "kind": "plate",
                        "version": "0.5",
                        "zarr_format": 3,
                    },
                },
                {
                    "name": "test_plate_2.zarr",
                    "path": str((data_root / "test_plate_2.zarr").resolve()),
                    "kind": "dir",
                    "ome_zarr": {
                        "kind": "plate",
                        "version": "0.5",
                        "zarr_format": 3,
                    },
                },
                {
                    "name": "test_plate_3.zarr",
                    "path": str((data_root / "test_plate_3.zarr").resolve()),
                    "kind": "dir",
                    "ome_zarr": {
                        "kind": "plate",
                        "version": "0.5",
                        "zarr_format": 3,
                    },
                },
                {
                    "name": "test_plate_4.zarr",
                    "path": str((data_root / "test_plate_4.zarr").resolve()),
                    "kind": "dir",
                    "ome_zarr": {
                        "kind": "plate",
                        "version": "0.5",
                        "zarr_format": 3,
                    },
                },
            ],
        }

        inside = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(data_root / "test_plate.zarr")},
        )
        assert inside.status_code == 400


@pytest.mark.needs_symlinks
def test_path_guard_rejects_parent_outside_and_symlink_escape(
    client_factory: Callable[[Path | None], TestClient], data_root: Path
) -> None:
    outside = data_root.parent / "outside-data"
    outside.mkdir(exist_ok=True)
    with client_factory() as client:
        parent_path = str(data_root / ".." / outside.name)
        assert (
            client.get("/api/fs/list", headers=AUTH, params={"path": parent_path}).status_code
            == 403
        )
        assert (
            client.get("/api/fs/list", headers=AUTH, params={"path": str(outside)}).status_code
            == 403
        )

        symlink = data_root / "escaping-link"
        try:
            symlink.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"Creating a directory symlink is unavailable: {exc}")
        try:
            response = client.get("/api/fs/list", headers=AUTH, params={"path": str(symlink)})
            assert response.status_code == 403
        finally:
            symlink.unlink(missing_ok=True)


def test_registry_link_list_unlink_persists_across_apps(data_root: Path, tmp_path: Path) -> None:
    state_dir = tmp_path / "persistent-state"
    settings = ServerSettings(
        data_roots=(data_root,),
        state_dir=state_dir,
        environment="local",
    )
    with TestClient(create_app(settings, TOKEN)) as first_client:
        record = _link(first_client, data_root / "test_image_v04.zarr", "v0.4 image")
        assert record["name"] == "v0.4 image"
        assert len(first_client.get("/api/datasets", headers=AUTH).json()["datasets"]) == 1

    with TestClient(create_app(settings, TOKEN)) as second_client:
        datasets = second_client.get("/api/datasets", headers=AUTH).json()["datasets"]
        assert [dataset["id"] for dataset in datasets] == [record["id"]]
        response = second_client.delete(f"/api/datasets/{record['id']}", headers=AUTH)
        assert response.status_code == 204

    with TestClient(create_app(settings, TOKEN)) as third_client:
        assert third_client.get("/api/datasets", headers=AUTH).json() == {"datasets": []}


def test_matches_route_returns_not_found_for_non_packed_dataset(
    client_factory: Callable[[Path | None], TestClient],
    data_root: Path,
) -> None:
    with client_factory() as client:
        record = _link(client, data_root / "test_image_v04.zarr")
        response = client.get(
            f"/api/datasets/{record['id']}/matches",
            headers=AUTH,
            params={"well": "A01", "by": "well"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Dataset matches are available only for packed plates"
    }


def test_collection_plate_well_summary_and_ome_facade(
    client_factory: Callable[[Path | None], TestClient], data_root: Path
) -> None:
    shard = data_root / "test_plate.zarr" / "A01_s1.zarr" / "c" / "0" / "0" / "0" / "0"
    with client_factory() as client:
        record = _link(client, data_root / "test_plate.zarr")
        dataset_id = record["id"]
        response = client.get(f"/api/datasets/{dataset_id}/summary", headers=AUTH)
        assert response.status_code == 200, response.text
        summary = response.json()
        _omit_channel_window_ends(summary["first_image"]["channels"])
        assert summary == {
            "kind": "plate",
            "name": "test_plate.zarr",
            "path": "",
            "rows": ["A", "B"],
            "columns": ["1", "2"],
            "acquisitions": [],
            "wells": [
                {"row": "A", "column": "1", "path": "A01"},
                {"row": "A", "column": "2", "path": "A02"},
                {"row": "B", "column": "1", "path": "B01"},
            ],
            "first_image": {
                "kind": "image",
                "name": "A01_s1",
                "path": "A01_s1.zarr",
                "axes": [
                    {"name": "t", "type": "time", "unit": "hour"},
                    {"name": "c", "type": "channel", "unit": None},
                    {"name": "y", "type": "space", "unit": "micrometer"},
                    {"name": "x", "type": "space", "unit": "micrometer"},
                ],
                "levels": [
                    {
                        "path": "s0",
                        "shape": [6, 3, 512, 512],
                        "chunks": [1, 1, 128, 128],
                        "shard_shape": [6, 3, 512, 512],
                        "dtype": "uint16",
                        "coordinate_transformations": [
                            {
                                "type": "scale",
                                "scale": [1.0, 1.0, 0.3, 0.3],
                            }
                        ],
                    }
                ],
                "channels": [
                    {
                        "name": "405 nm",
                        "color": "0000FF",
                        "window": {"min": 0.0, "max": 65535.0, "start": 100.0},
                    },
                    {
                        "name": "505 nm",
                        "color": "00FF00",
                        "window": {"min": 0.0, "max": 65535.0, "start": 100.0},
                    },
                    {
                        "name": "625 nm",
                        "color": "FF0000",
                        "window": {"min": 0.0, "max": 65535.0, "start": 100.0},
                    },
                ],
                "ome_zarr_version": "0.5",
                "zarr_format": 3,
            },
            "ome_zarr_version": "0.5",
            "zarr_format": 3,
        }

        well = client.get(
            f"/api/datasets/{dataset_id}/wells/A01",
            headers=AUTH,
        )
        assert well.status_code == 200
        assert well.json()["fields"] == [
            {"path": "A01_s1.zarr", "acquisition": None},
            {"path": "A01_s2.zarr", "acquisition": None},
        ]

        group_metadata = client.get(f"/zarr/{dataset_id}/A01_s1.zarr/zarr.json", headers=AUTH)
        assert group_metadata.status_code == 200
        facade_metadata = group_metadata.json()
        _omit_channel_window_ends(facade_metadata["attributes"]["ome"]["omero"]["channels"])
        assert facade_metadata == {
            "attributes": {
                "ome": {
                    "version": "0.5",
                    "multiscales": [
                        {
                            "name": "A01_s1",
                            "axes": [
                                {"name": "t", "type": "time", "unit": "hour"},
                                {"name": "c", "type": "channel"},
                                {"name": "y", "type": "space", "unit": "micrometer"},
                                {"name": "x", "type": "space", "unit": "micrometer"},
                            ],
                            "datasets": [
                                {
                                    "path": "s0",
                                    "coordinateTransformations": [
                                        {
                                            "type": "scale",
                                            "scale": [1.0, 1.0, 0.3, 0.3],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    "omero": {
                        "channels": [
                            {
                                "label": "405 nm",
                                "color": "0000FF",
                                "window": {"min": 0.0, "max": 65535.0, "start": 100.0},
                            },
                            {
                                "label": "505 nm",
                                "color": "00FF00",
                                "window": {"min": 0.0, "max": 65535.0, "start": 100.0},
                            },
                            {
                                "label": "625 nm",
                                "color": "FF0000",
                                "window": {"min": 0.0, "max": 65535.0, "start": 100.0},
                            },
                        ]
                    },
                }
            },
            "zarr_format": 3,
            "node_type": "group",
        }
        assert group_metadata.headers["cache-control"] == "private, max-age=3600"
        metadata_etag = group_metadata.headers["etag"]
        not_modified_metadata = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/zarr.json",
            headers={**AUTH, "If-None-Match": f'W/{metadata_etag}, "unrelated"'},
        )
        assert not_modified_metadata.status_code == 304
        assert not_modified_metadata.content == b""
        assert not_modified_metadata.headers["etag"] == metadata_etag

        array_metadata = client.get(f"/zarr/{dataset_id}/A01_s1.zarr/s0/zarr.json", headers=AUTH)
        assert array_metadata.status_code == 200
        assert array_metadata.json() == {
            "shape": [6, 3, 512, 512],
            "data_type": "uint16",
            "chunk_grid": {
                "name": "regular",
                "configuration": {"chunk_shape": [6, 3, 512, 512]},
            },
            "chunk_key_encoding": {
                "name": "default",
                "configuration": {"separator": "/"},
            },
            "fill_value": 0,
            "codecs": [
                {
                    "name": "sharding_indexed",
                    "configuration": {
                        "chunk_shape": [1, 1, 128, 128],
                        "codecs": [
                            {"name": "bytes", "configuration": {"endian": "little"}},
                            {
                                "name": "blosc",
                                "configuration": {
                                    "typesize": 2,
                                    "cname": "zstd",
                                    "clevel": 5,
                                    "shuffle": "shuffle",
                                    "blocksize": 0,
                                },
                            },
                        ],
                        "index_codecs": [
                            {"name": "bytes", "configuration": {"endian": "little"}},
                            {"name": "crc32c"},
                        ],
                        "index_location": "end",
                    },
                }
            ],
            "attributes": {
                "timed": {
                    "well": "A01",
                    "site": 1,
                    "axes": ["t", "c", "y", "x"],
                    "channels_nm": [405, 505, 625],
                    "t_indices": [0, 1, 2, 3, 4, 5],
                }
            },
            "zarr_format": 3,
            "node_type": "array",
            "storage_transformers": [],
            "dimension_names": ["t", "c", "y", "x"],
        }

        ranged = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0/0",
            headers={**AUTH, "Range": "bytes=10-29"},
        )
        assert ranged.status_code == 206
        assert ranged.content == shard.read_bytes()[10:30]
        assert ranged.headers["content-range"] == f"bytes 10-29/{shard.stat().st_size}"
        assert ranged.headers["accept-ranges"] == "bytes"
        assert ranged.headers["cache-control"] == "private, no-store"
        assert "last-modified" in ranged.headers
        shard_etag = ranged.headers["etag"]

        not_modified_shard = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0/0",
            headers={**AUTH, "If-None-Match": shard_etag},
        )
        assert not_modified_shard.status_code == 304
        assert not_modified_shard.content == b""

        matching_if_range = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0/0",
            headers={
                **AUTH,
                "Range": "bytes=10-29",
                "If-Range": shard_etag,
            },
        )
        assert matching_if_range.status_code == 206
        assert matching_if_range.content == shard.read_bytes()[10:30]
        assert matching_if_range.headers["cache-control"] == "private, no-store"

        stale_if_range = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0/0",
            headers={
                **AUTH,
                "Range": "bytes=10-29",
                "If-Range": '"stale-validator"',
            },
        )
        assert stale_if_range.status_code == 200
        assert stale_if_range.content == shard.read_bytes()
        assert "content-range" not in stale_if_range.headers

        matching_date_if_range = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0/0",
            headers={
                **AUTH,
                "Range": "bytes=10-29",
                "If-Range": ranged.headers["last-modified"],
            },
        )
        assert matching_date_if_range.status_code == 206
        assert matching_date_if_range.headers["cache-control"] == "private, no-store"

        head = client.head(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0/0",
            headers=AUTH,
        )
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == str(shard.stat().st_size)
        assert head.headers["accept-ranges"] == "bytes"

        missing = client.get(f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/9/9/9/9", headers=AUTH)
        assert missing.status_code == 404


def test_timed_facade_declares_the_legacy_window_end(
    client_factory: Callable[[Path | None], TestClient],
    data_root: Path,
) -> None:
    with client_factory() as client:
        record = _link(client, data_root / "test_plate.zarr")
        dataset_id = record["id"]
        summary = client.get(f"/api/datasets/{dataset_id}/summary", headers=AUTH).json()
        group = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/zarr.json",
            headers=AUTH,
        ).json()

    assert [
        channel["window"]["end"] for channel in summary["first_image"]["channels"]
    ] == [65_535.0] * 3
    assert [
        channel["window"]["end"]
        for channel in group["attributes"]["ome"]["omero"]["channels"]
    ] == [65_535.0] * 3


@pytest.mark.asyncio
async def test_berzelius_plate_group_whole_plane_chunks_metadata_and_repair(
    tmp_path: Path,
) -> None:
    plate, expected = write_berzelius_plate(tmp_path)
    connection_file = tmp_path / "runtime" / "connection.json"
    settings = ServerSettings(
        data_roots=(tmp_path,),
        state_dir=tmp_path / "state",
        environment="local",
        metadata_root=tmp_path / "metadata",
    )
    started = asyncio.Event()
    server_holder: list[object] = []

    def on_started(server: object) -> None:
        server_holder.append(server)
        started.set()

    server_task = asyncio.create_task(
        serve(
            settings,
            host="127.0.0.1",
            port=0,
            connection_file=connection_file,
            token=TOKEN,
            on_started=on_started,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        connection = json.loads(connection_file.read_text(encoding="utf-8"))
        origin = f"http://127.0.0.1:{connection['port']}"
        async with httpx.AsyncClient(base_url=origin, timeout=10) as client:
            linked = await client.post(
                "/api/datasets",
                headers=AUTH,
                json={"path": str(plate)},
            )
            assert linked.status_code == 201, linked.text
            dataset_id = linked.json()["id"]
            summary = await client.get(
                f"/api/datasets/{dataset_id}/summary",
                headers=AUTH,
            )
            well = await client.get(
                f"/api/datasets/{dataset_id}/wells/C03",
                headers=AUTH,
            )
            metadata = await client.get(
                f"/api/datasets/{dataset_id}/metadata",
                headers=AUTH,
            )

        assert summary.status_code == 200, summary.text
        assert summary.json()["first_image"]["levels"][0]["chunks"] == [1, 1, 16, 16]
        assert well.status_code == 200, well.text
        assert well.json()["fields"] == [
            {
                "path": "C03_s1.zarr",
                "acquisition": None,
                "site_position_fix": {
                    "grid_x": 1,
                    "repaired_timepoints": [0],
                },
            }
        ]
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["plate"]["cell_line"] == "U2OS_Cleo3"
        assert [row["well"] for row in metadata.json()["wells"]] == ["C03"]

        def read_through_facade() -> np.ndarray:
            store = FsspecStore.from_url(
                f"{origin}/zarr/{dataset_id}/C03_s1.zarr",
                storage_options={"headers": AUTH},
                read_only=True,
            )
            group = zarr.open_group(
                store=store,
                mode="r",
                zarr_format=3,
                use_consolidated=False,
            )
            return np.asarray(group["s0"][:])

        actual = await asyncio.wait_for(
            asyncio.create_task(anyio.to_thread.run_sync(read_through_facade)),
            timeout=20,
        )
        np.testing.assert_array_equal(actual, expected)
    finally:
        if server_holder:
            server_holder[0].should_exit = True  # type: ignore[attr-defined]
        await asyncio.wait_for(server_task, timeout=10)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: value["attributes"]["timed"].pop("axes"), "axes must be"),
        (lambda value: value.__setitem__("shape", [2, 3, 16]), "invalid shape"),
        (lambda value: value.__setitem__("data_type", "float32"), "uint16 pixels"),
        (
            lambda value: value["codecs"][0].__setitem__("name", "bytes"),
            "sharding_indexed codec",
        ),
    ],
)
def test_timed_field_validation_rejects_incompatible_metadata(
    tmp_path: Path,
    change: Callable[[dict[str, object]], object],
    message: str,
) -> None:
    plate, _data = write_berzelius_plate(tmp_path)
    field = plate / "C03_s1.zarr"
    metadata_path = field / "zarr.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    change(metadata)
    write_json(metadata_path, metadata)

    with pytest.raises(OmeZarrError, match=message):
        _timed_field_metadata(field)


def test_collection_plate_metadata_joins_wells_and_acquisition(
    client_factory: Callable[[Path | None], TestClient],
    data_root: Path,
) -> None:
    with client_factory() as client:
        record = _link(client, data_root / "test_plate.zarr")
        response = client.get(
            f"/api/datasets/{record['id']}/metadata",
            headers=AUTH,
        )
        image = _link(client, data_root / "test_image_v05.zarr")
        unavailable = client.get(
            f"/api/datasets/{image['id']}/metadata",
            headers=AUTH,
        )

    assert response.status_code == 200, response.text
    metadata = response.json()
    assert set(metadata) == {"available", "plate", "wells"}
    assert metadata["available"] is True
    assert metadata["plate"] == {
        "plate_id": "S1.1",
        "plate_series": "S1",
        "plate_replicate": 1,
        "plate_barcode": "SYNTH001",
        "project": "TIMED synthetic",
        "experiment_date": "2026-10-05",
        "cell_line": "Synthetic cells",
        "cell_modification": "Reporter",
        "passage": "1",
        "seeding_density_cells_per_well": 1200,
        "dye_program": "Three-channel",
        "acquisition": {
            "project_name": "TIMED synthetic test data",
            "configuration_timestamp": "2026-10-05T12:00:00",
            "autofocus_enabled": True,
            "plate_manufacturer": "TIMED",
            "plate_model": "Synthetic 4-well plate",
            "plate_bottom_offset_mm": 0.5,
            "sites_per_well": 2,
            "site_spacing_x_mm": 0.8,
            "site_spacing_y_mm": 0.8,
            "timepoint_count": 6,
            "interval_minutes": 60.0,
            "channels": [
                {"name": "Fluorescence 405 nm Ex"},
                {"name": "Fluorescence 505 nm Ex"},
                {"name": "Fluorescence 625 nm Ex"},
            ],
        },
    }
    assert metadata["wells"] == [
        {
            "well": "A01",
            "well_type": "compound",
            "compound": "Compound Alpha",
            "dose_um": 1.25,
            "is_control": False,
            "moa": "AKT",
            "target": "AKT1",
            "pathway": "PI3K/Akt/mTOR",
            "mechanism_of_action": "AKT inhibitor",
        },
        {
            "well": "A02",
            "well_type": "compound",
            "compound": "Compound Beta",
            "dose_um": 5.0,
            "is_control": False,
            "moa": "MAPK",
            "target": "MAPK1",
            "pathway": "MAPK/ERK Pathway",
            "mechanism_of_action": "MAPK inhibitor",
        },
        {
            "well": "B01",
            "well_type": "solvent_control",
            "compound": "DMSO",
            "dose_um": 0.0,
            "is_control": True,
            "moa": None,
            "target": None,
            "pathway": None,
            "mechanism_of_action": None,
        },
    ]
    assert unavailable.status_code == 200
    assert unavailable.json() == {"available": False, "plate": None, "wells": []}


@pytest.mark.parametrize(
    ("store_name", "version", "zarr_format", "sharded"),
    [
        ("test_image_v04.zarr", "0.4", 2, False),
        ("test_image_v05.zarr", "0.5", 3, False),
    ],
)
def test_native_image_summaries(
    client_factory: Callable[[Path | None], TestClient],
    data_root: Path,
    store_name: str,
    version: str,
    zarr_format: int,
    sharded: bool,
) -> None:
    with client_factory() as client:
        record = _link(client, data_root / store_name)
        response = client.get(f"/api/datasets/{record['id']}/summary", headers=AUTH)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["kind"] == "image"
    assert "grayscale_available" not in summary
    assert summary["ome_zarr_version"] == version
    assert summary["zarr_format"] == zarr_format
    assert [axis["name"] for axis in summary["axes"]] == ["t", "c", "y", "x"]
    assert summary["levels"][0]["shape"] == [6, 3, 512, 512]
    assert summary["levels"][0]["chunks"] == [1, 1, 128, 128]
    assert (summary["levels"][0]["shard_shape"] is not None) is sharded
    assert summary["channels"][0]["color"] == "0000FF"
    assert summary["channels"][0]["window"]["end"] == 8000


@pytest.mark.parametrize("store_name", ["test_image_v04.zarr", "test_image_v05.zarr"])
def test_native_resources_are_served_byte_for_byte(
    client_factory: Callable[[Path | None], TestClient],
    data_root: Path,
    store_name: str,
) -> None:
    store = data_root / store_name
    files = sorted(path for path in store.rglob("*") if path.is_file())
    assert len(files) > 3

    with client_factory() as client:
        dataset_id = _link(client, store)["id"]
        for path in files:
            relative = path.relative_to(store).as_posix()
            expected = path.read_bytes()
            url = f"/zarr/{dataset_id}/{relative}"
            response = client.get(url, headers=AUTH)
            head = client.head(url, headers=AUTH)
            suffix = client.get(url, headers={**AUTH, "Range": "bytes=-3"})

            assert response.status_code == 200, relative
            assert response.content == expected, relative
            assert response.headers["content-length"] == str(len(expected))
            assert head.status_code == 200, relative
            assert head.headers["content-length"] == str(len(expected))
            assert head.headers["etag"] == response.headers["etag"]
            assert suffix.status_code == 206, relative
            assert suffix.content == expected[-3:]
            start = max(0, len(expected) - 3)
            assert suffix.headers["content-range"] == (
                f"bytes {start}-{len(expected) - 1}/{len(expected)}"
            )


def test_native_omero_channel_active_flags_are_preserved() -> None:
    channels = _channels(
        {
            "channels": [
                {"label": "DNA", "active": True},
                {"label": "RNA", "active": False},
                {"label": "Invalid", "active": "false"},
            ]
        }
    )

    assert [channel.active for channel in channels] == [True, False, None]


def test_native_plate_and_well_metadata_are_read_lazily(
    client_factory: Callable[[Path | None], TestClient], data_root: Path
) -> None:
    plate = write_native_plate(data_root / "native_plate.zarr")

    with client_factory() as client:
        record = _link(client, plate)
        summary_response = client.get(f"/api/datasets/{record['id']}/summary", headers=AUTH)
        well_response = client.get(f"/api/datasets/{record['id']}/wells/A/1", headers=AUTH)

    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()
    assert summary["kind"] == "plate"
    assert summary["name"] == "Native test plate"
    assert summary["acquisitions"] == [{"id": 7, "name": "hourly"}]
    assert summary["wells"] == [{"row": "A", "column": "1", "path": "A/1"}]
    assert summary["first_image"]["path"] == "A/1/0"
    assert "grayscale_available" not in summary["first_image"]
    assert summary["first_image"]["levels"][0]["chunks"] == [1, 1, 16, 16]
    assert well_response.status_code == 200
    assert well_response.json()["fields"] == [{"path": "A/1/0", "acquisition": 7}]


@pytest.mark.asyncio
async def test_connection_file_mode_and_cleanup(data_root: Path, tmp_path: Path) -> None:
    connection_file = tmp_path / "runtime" / "connection.json"
    settings = ServerSettings(
        data_roots=(data_root,),
        state_dir=tmp_path / "runtime-state",
        environment="local",
    )
    started = asyncio.Event()
    server_holder: list[object] = []

    def on_started(server: object) -> None:
        server_holder.append(server)
        started.set()

    task = asyncio.create_task(
        serve(
            settings,
            host="127.0.0.1",
            port=0,
            connection_file=connection_file,
            token=TOKEN,
            on_started=on_started,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        connection = json.loads(connection_file.read_text(encoding="utf-8"))
        assert connection["port"] > 0
        assert connection["token"] == TOKEN
        assert connection["environment"] == "local"
        if os.name != "nt":
            assert stat.S_IMODE(connection_file.stat().st_mode) == 0o600

        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"http://127.0.0.1:{connection['port']}/api/handshake",
                headers=AUTH,
            )
        assert response.status_code == 200
    finally:
        if server_holder:
            server_holder[0].should_exit = True  # type: ignore[attr-defined]
        await asyncio.wait_for(task, timeout=10)
    assert not connection_file.exists()


@pytest.mark.asyncio
async def test_connection_file_is_gone_when_uvicorn_reraises_sigterm(
    data_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uvicorn re-raises a captured SIGTERM after shutdown with the default
    handler restored, which ends the process before serve() reaches its finally
    block. Record what exists at that moment instead of ending the process."""
    connection_file = tmp_path / "runtime" / "connection.json"
    settings = ServerSettings(
        data_roots=(data_root,),
        state_dir=tmp_path / "runtime-state",
        environment="local",
    )
    present_at_reraise: list[bool] = []

    def reraise(signum: int) -> None:
        assert signum == signal.SIGTERM
        present_at_reraise.append(connection_file.exists())

    def stop_with_sigterm(server: object) -> None:
        assert connection_file.exists()
        server.handle_exit(signal.SIGTERM, None)  # type: ignore[attr-defined]

    monkeypatch.setattr(signal, "raise_signal", reraise)
    await asyncio.wait_for(
        serve(
            settings,
            host="127.0.0.1",
            port=0,
            connection_file=connection_file,
            token=TOKEN,
            on_started=stop_with_sigterm,
        ),
        timeout=20,
    )

    assert present_at_reraise == [False]
    assert not connection_file.exists()


@pytest.mark.posix_only
@pytest.mark.skipif(os.name == "nt", reason="SIGTERM delivery to a child process is POSIX-only")
def test_sigterm_removes_the_connection_file_and_still_ends_by_signal(
    data_root: Path,
    tmp_path: Path,
) -> None:
    connection_file = tmp_path / "runtime" / "connection.json"
    log_path = tmp_path / "server.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "timed_dataserver.cli",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--data-root",
                str(data_root),
                "--connection-file",
                str(connection_file),
                "--environment",
                "local",
                "--state-dir",
                str(tmp_path / "state"),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while not connection_file.exists():
                assert process.poll() is None, log_path.read_text(errors="replace")
                assert time.monotonic() < deadline, "the server never wrote its file"
                time.sleep(0.05)
            process.send_signal(signal.SIGTERM)
            returncode = process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert returncode == -signal.SIGTERM
    assert not connection_file.exists()


def test_export_openapi(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export-openapi"]) == 0
    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["info"]["title"] == "TIMED data server"
    assert document["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "/api/handshake" in document["paths"]
    assert "/api/datasets/{dataset_id}/summary" in document["paths"]
    assert "/api/datasets/{dataset_id}/metadata" in document["paths"]
    assert "/api/datasets/{dataset_id}/matches" in document["paths"]
    assert "/zarr/{dataset_id}/{resource_path}" in document["paths"]
    assert "openapi-placeholder" not in output


def test_committed_openapi_matches_export(capsys: pytest.CaptureFixture[str]) -> None:
    committed_path = (
        Path(__file__).resolve().parents[3]
        / "apps"
        / "web"
        / "src"
        / "lib"
        / "api"
        / "dataserver.openapi.json"
    )
    assert main(["export-openapi"]) == 0
    exported = json.loads(capsys.readouterr().out)
    committed = json.loads(committed_path.read_text(encoding="utf-8"))

    regenerate = "Run `pnpm -C apps/web gen:api` and commit the regenerated API files"
    assert exported["info"]["version"] == __version__
    assert committed["info"]["version"] == __version__, regenerate
    for document in (exported, committed):
        document["info"]["version"] = "<server version>"
    assert exported == committed, regenerate


def test_packed_cache_budget_must_be_positive(data_root: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        create_app(
            ServerSettings(
                data_roots=(data_root,),
                state_dir=tmp_path / "state",
                environment="local",
                packed_cache_mb=0,
            ),
            TOKEN,
        )

    with pytest.raises(SystemExit, match="--packed-cache-mb must be greater than zero"):
        main(
            [
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--data-root",
                str(data_root),
                "--connection-file",
                str(tmp_path / "connection.json"),
                "--environment",
                "local",
                "--packed-cache-mb",
                "0",
            ]
        )
