from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.cli import main, serve

from scripts.make_test_data import generate_test_data

TOKEN = "test-only-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("dataserver-data")
    generate_test_data(root)
    (root / "ordinary").mkdir()
    (root / "notes.txt").write_text("not a dataset\n", encoding="utf-8")
    return root


@pytest.fixture
def client_factory(
    data_root: Path, tmp_path: Path
) -> Callable[[Path | None], TestClient]:
    def make_client(state_dir: Path | None = None) -> TestClient:
        settings = ServerSettings(
            data_roots=(data_root,),
            state_dir=state_dir or tmp_path / "state",
            environment="local",
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


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


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
        assert payload["capabilities"] == {"gpu": False}

        roots = client.get("/api/fs/list", headers=AUTH)
        assert roots.status_code == 200
        assert roots.json()["entries"][0]["path"] == str(data_root.resolve())

        listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(data_root)})
        assert listing.status_code == 200
        entries = {entry["name"]: entry for entry in listing.json()["entries"]}
        assert entries["test_plate.zarr"]["ome_zarr"] == {
            "kind": "plate",
            "version": "0.5",
            "zarr_format": 3,
        }
        assert entries["test_image_v04.zarr"]["ome_zarr"]["kind"] == "image"
        assert entries["test_image_v04.zarr"]["ome_zarr"]["version"] == "0.4"
        assert entries["test_image_v04.zarr"]["ome_zarr"]["zarr_format"] == 2
        assert entries["test_image_v05.zarr"]["ome_zarr"]["kind"] == "image"
        assert entries["test_image_v05.zarr"]["ome_zarr"]["version"] == "0.5"
        assert entries["test_image_v05.zarr"]["ome_zarr"]["zarr_format"] == 3
        assert entries["ordinary"]["ome_zarr"] is None
        assert entries["notes.txt"]["kind"] == "file"

        inside = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(data_root / "test_plate.zarr")},
        )
        assert inside.status_code == 400


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
            response = client.get(
                "/api/fs/list", headers=AUTH, params={"path": str(symlink)}
            )
            assert response.status_code == 403
        finally:
            symlink.unlink(missing_ok=True)


def test_registry_link_list_unlink_persists_across_apps(
    data_root: Path, tmp_path: Path
) -> None:
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
        assert summary["kind"] == "plate"
        assert summary["rows"] == ["A", "B"]
        assert summary["columns"] == ["1", "2"]
        assert {(well["row"], well["column"]) for well in summary["wells"]} == {
            ("A", "1"),
            ("A", "2"),
            ("B", "1"),
        }
        image = summary["first_image"]
        assert [axis["name"] for axis in image["axes"]] == ["t", "c", "y", "x"]
        assert image["levels"][0]["shape"] == [6, 3, 512, 512]
        assert image["levels"][0]["chunks"] == [1, 1, 128, 128]
        assert image["levels"][0]["shard_shape"] == [6, 3, 512, 512]
        assert [channel["name"] for channel in image["channels"]] == [
            "405 nm",
            "505 nm",
            "625 nm",
        ]

        well = client.get(
            f"/api/datasets/{dataset_id}/wells/A01",
            headers=AUTH,
        )
        assert well.status_code == 200
        assert well.json()["fields"] == [
            {"path": "A01_s1.zarr", "acquisition": None},
            {"path": "A01_s2.zarr", "acquisition": None},
        ]

        group_metadata = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/zarr.json", headers=AUTH
        )
        assert group_metadata.status_code == 200
        assert group_metadata.json()["node_type"] == "group"
        assert group_metadata.json()["attributes"]["ome"]["version"] == "0.5"
        facade_axes = group_metadata.json()["attributes"]["ome"]["multiscales"][0]["axes"]
        assert facade_axes[0] == {"name": "t", "type": "time", "unit": "hour"}
        assert facade_axes[1] == {"name": "c", "type": "channel"}

        array_metadata = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/zarr.json", headers=AUTH
        )
        assert array_metadata.status_code == 200
        assert array_metadata.json()["node_type"] == "array"
        assert array_metadata.json()["dimension_names"] == ["t", "c", "y", "x"]

        ranged = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0/0",
            headers={**AUTH, "Range": "bytes=10-29"},
        )
        assert ranged.status_code == 206
        assert ranged.content == shard.read_bytes()[10:30]
        assert ranged.headers["content-range"] == f"bytes 10-29/{shard.stat().st_size}"
        assert ranged.headers["accept-ranges"] == "bytes"

        missing = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/9/9/9/9", headers=AUTH
        )
        assert missing.status_code == 404


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
    assert summary["ome_zarr_version"] == version
    assert summary["zarr_format"] == zarr_format
    assert [axis["name"] for axis in summary["axes"]] == ["t", "c", "y", "x"]
    assert summary["levels"][0]["shape"] == [6, 3, 512, 512]
    assert summary["levels"][0]["chunks"] == [1, 1, 128, 128]
    assert (summary["levels"][0]["shard_shape"] is not None) is sharded
    assert summary["channels"][0]["color"] == "0000FF"
    assert summary["channels"][0]["window"]["end"] == 8000


def test_native_plate_and_well_metadata_are_read_lazily(
    client_factory: Callable[[Path | None], TestClient], data_root: Path
) -> None:
    plate = data_root / "native_plate.zarr"
    _write_json(plate / ".zgroup", {"zarr_format": 2})
    _write_json(
        plate / ".zattrs",
        {
            "plate": {
                "version": "0.4",
                "name": "Native test plate",
                "rows": [{"name": "A"}],
                "columns": [{"name": "1"}],
                "wells": [{"path": "A/1", "rowIndex": 0, "columnIndex": 0}],
                "acquisitions": [{"id": 7, "name": "hourly"}],
            }
        },
    )
    _write_json(plate / "A" / "1" / ".zgroup", {"zarr_format": 2})
    _write_json(
        plate / "A" / "1" / ".zattrs",
        {"well": {"version": "0.4", "images": [{"path": "0", "acquisition": 7}]}},
    )
    _write_json(plate / "A" / "1" / "0" / ".zgroup", {"zarr_format": 2})
    _write_json(
        plate / "A" / "1" / "0" / ".zattrs",
        {
            "multiscales": [
                {
                    "version": "0.4",
                    "name": "field zero",
                    "axes": [
                        {"name": "t", "type": "time"},
                        {"name": "c", "type": "channel"},
                        {"name": "y", "type": "space"},
                        {"name": "x", "type": "space"},
                    ],
                    "datasets": [{"path": "0"}],
                }
            ]
        },
    )
    _write_json(
        plate / "A" / "1" / "0" / "0" / ".zarray",
        {
            "zarr_format": 2,
            "shape": [2, 1, 32, 32],
            "chunks": [1, 1, 16, 16],
            "dtype": "<u2",
        },
    )

    with client_factory() as client:
        record = _link(client, plate)
        summary_response = client.get(
            f"/api/datasets/{record['id']}/summary", headers=AUTH
        )
        well_response = client.get(
            f"/api/datasets/{record['id']}/wells/A/1", headers=AUTH
        )

    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()
    assert summary["kind"] == "plate"
    assert summary["name"] == "Native test plate"
    assert summary["acquisitions"] == [{"id": 7, "name": "hourly"}]
    assert summary["wells"] == [{"row": "A", "column": "1", "path": "A/1"}]
    assert summary["first_image"]["path"] == "A/1/0"
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


def test_export_openapi(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export-openapi"]) == 0
    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["info"]["title"] == "TIMED data server"
    assert document["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "/api/handshake" in document["paths"]
    assert "/api/datasets/{dataset_id}/summary" in document["paths"]
    assert "/zarr/{dataset_id}/{resource_path}" in document["paths"]
    assert "openapi-placeholder" not in output
