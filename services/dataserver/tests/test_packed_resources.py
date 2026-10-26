from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any

import anyio
import httpx
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import tifffile
import zarr
from fastapi.testclient import TestClient
from numcodecs import Blosc
from numpy.typing import NDArray
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.cli import serve
from zarr.storage import FsspecStore

from scripts import make_test_data

TOKEN = "packed-resource-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ROWS = list("ABCDEFGHIJKLMNOP")
COLUMNS = [str(column) for column in range(1, 25)]
WELLS = ["A01", "A02", "B01", "C01", "P24"]

CHANNELS_BY_CELL_LINE = {
    "A549": [
        ("DNA", "1300F9", True, 27_000, 292, 11_552),
        ("ER", "2AFF1F", True, 45_000, 438, 17_554),
        ("RNA", "2DFFFC", False, 26_000, 554, 9_283),
        ("AGP", "FF0019", True, 14_000, 151, 5_035),
        ("Mito", "FA00FD", False, 15_000, 324, 4_066),
    ],
    "ES2": [
        ("DNA", "1300F9", True, 27_000, 234, 12_971),
        ("ER", "2AFF1F", True, 45_000, 309, 18_238),
        ("RNA", "2DFFFC", False, 26_000, 375, 10_793),
        ("AGP", "FF0019", True, 14_000, 108, 5_778),
        ("Mito", "FA00FD", False, 15_000, 334, 7_120),
    ],
}


@pytest.fixture(scope="module")
def packed_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate packed data separately from the frozen TIMED fixture."""
    return make_test_data._write_packed_fixture(tmp_path_factory.mktemp("packed-resource-data"))


def _settings(root: Path, state_dir: Path) -> ServerSettings:
    return ServerSettings(
        data_roots=(root,),
        state_dir=state_dir,
        environment="local",
        packed_cache_mb=16,
    )


def _index(root: Path) -> Path:
    return (root / make_test_data.PACKED_INDEX_NAME).resolve()


def _link(
    client: TestClient,
    root: Path,
    *,
    experiment: str = "idr0080-A549",
    plate: int = 14610,
) -> dict[str, Any]:
    response = client.post(
        "/api/datasets",
        headers=AUTH,
        json={"path": str(_index(root) / experiment / str(plate))},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _index_rows(root: Path) -> list[dict[str, Any]]:
    return pq.read_table(_index(root)).to_pylist()


def _matching_row(
    root: Path,
    *,
    experiment: str = "idr0080-A549",
    plate: int = 14610,
    well: str = "A01",
    site: int = 1,
    channel: int = 1,
) -> dict[str, Any]:
    return next(
        row
        for row in _index_rows(root)
        if (
            row["experiment"],
            row["plate"],
            row["well"],
            row["site"],
            row["channel"],
        )
        == (experiment, plate, well, site, channel)
    )


def _source_plane(root: Path, row: dict[str, Any]) -> NDArray[np.generic]:
    """Read expected pixels independently through tarfile and tifffile."""
    member_name = f"idr0080/raw/{row['relpath']}"
    with tarfile.open(root / row["shard"], mode="r:") as archive:
        extracted = archive.extractfile(member_name)
        assert extracted is not None
        payload = extracted.read()
    return np.asarray(tifffile.imread(io.BytesIO(payload)))


def _summary_channels(cell_line: str) -> list[dict[str, Any]]:
    return [
        {
            "name": label,
            "color": color,
            "window": {
                "min": 0.0,
                "max": float(maximum),
                "start": float(start),
                "end": float(end),
            },
            "active": active,
        }
        for label, color, active, maximum, start, end in CHANNELS_BY_CELL_LINE[cell_line]
    ]


def _omero_channels(cell_line: str) -> list[dict[str, Any]]:
    return [
        {
            "label": label,
            "color": color,
            "active": active,
            "window": {"min": 0, "max": maximum, "start": start, "end": end},
        }
        for label, color, active, maximum, start, end in CHANNELS_BY_CELL_LINE[cell_line]
    ]


def _expected_summary() -> dict[str, Any]:
    return {
        "kind": "plate",
        "name": "SQ00014610",
        "path": "",
        "rows": ROWS,
        "columns": COLUMNS,
        "acquisitions": [],
        "wells": [{"row": well[0], "column": str(int(well[1:])), "path": well} for well in WELLS],
        "first_image": {
            "kind": "image",
            "name": "A01_s1",
            "path": "A01_s1.zarr",
            "axes": [
                {"name": "c", "type": "channel", "unit": None},
                {"name": "y", "type": "space", "unit": None},
                {"name": "x", "type": "space", "unit": None},
            ],
            "levels": [
                {
                    "path": "s0",
                    "shape": [5, 64, 64],
                    "chunks": [1, 32, 32],
                    "shard_shape": None,
                    "dtype": "uint16",
                    "coordinate_transformations": [{"type": "scale", "scale": [1.0, 1.0, 1.0]}],
                }
            ],
            "channels": _summary_channels("A549"),
            "ome_zarr_version": "0.5",
            "zarr_format": 3,
        },
        "ome_zarr_version": "0.5",
        "zarr_format": 3,
    }


def _expected_group(field: str, cell_line: str) -> dict[str, Any]:
    return {
        "zarr_format": 3,
        "node_type": "group",
        "attributes": {
            "ome": {
                "version": "0.5",
                "multiscales": [
                    {
                        "name": field,
                        "axes": [
                            {"name": "c", "type": "channel"},
                            {"name": "y", "type": "space"},
                            {"name": "x", "type": "space"},
                        ],
                        "datasets": [
                            {
                                "path": "s0",
                                "coordinateTransformations": [
                                    {"type": "scale", "scale": [1, 1, 1]}
                                ],
                            }
                        ],
                    }
                ],
                "omero": {"channels": _omero_channels(cell_line)},
            }
        },
    }


EXPECTED_ARRAY = {
    "zarr_format": 3,
    "node_type": "array",
    "shape": [5, 64, 64],
    "data_type": "uint16",
    "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": [1, 32, 32]}},
    "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
    "fill_value": 0,
    "codecs": [
        {"name": "bytes", "configuration": {"endian": "little"}},
        {
            "name": "blosc",
            "configuration": {
                "typesize": 2,
                "cname": "zstd",
                "clevel": 3,
                "shuffle": "shuffle",
                "blocksize": 0,
            },
        },
    ],
    "dimension_names": ["c", "y", "x"],
    "attributes": {},
}


def test_linked_summary_wells_and_restart_are_exact(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    settings = _settings(packed_root, tmp_path / "persistent-state")
    expected_well = {
        "well_path": "A01",
        "fields": [
            {"path": "A01_s1.zarr", "acquisition": None},
            {"path": "A01_s2.zarr", "acquisition": None},
        ],
    }

    with TestClient(create_app(settings, TOKEN)) as client:
        record = _link(client, packed_root)
        dataset_id = record["id"]
        summary = client.get(f"/api/datasets/{dataset_id}/summary", headers=AUTH)
        well = client.get(f"/api/datasets/{dataset_id}/wells/A01", headers=AUTH)
        missing = client.get(f"/api/datasets/{dataset_id}/wells/A03", headers=AUTH)
        assert summary.status_code == 200, summary.text
        assert summary.json() == _expected_summary()
        assert well.status_code == 200, well.text
        assert well.json() == expected_well
        assert missing.status_code == 404

    with TestClient(create_app(settings, TOKEN)) as restarted:
        summary = restarted.get(f"/api/datasets/{dataset_id}/summary", headers=AUTH)
        well = restarted.get(f"/api/datasets/{dataset_id}/wells/A01", headers=AUTH)
        assert summary.status_code == 200, summary.text
        assert summary.json() == _expected_summary()
        assert well.status_code == 200, well.text
        assert well.json() == expected_well


@pytest.mark.parametrize(
    ("experiment", "plate", "cell_line"),
    [
        ("idr0080-A549", 14610, "A549"),
        ("idr0080-ES2", 14613, "ES2"),
    ],
)
def test_group_and_array_metadata_are_exact(
    packed_root: Path,
    tmp_path: Path,
    experiment: str,
    plate: int,
    cell_line: str,
) -> None:
    with TestClient(create_app(_settings(packed_root, tmp_path / cell_line), TOKEN)) as client:
        dataset_id = _link(client, packed_root, experiment=experiment, plate=plate)["id"]
        group = client.get(f"/zarr/{dataset_id}/A01_s1.zarr/zarr.json", headers=AUTH)
        array = client.get(f"/zarr/{dataset_id}/A01_s1.zarr/s0/zarr.json", headers=AUTH)

    assert group.status_code == 200, group.text
    assert group.json() == _expected_group("A01_s1", cell_line)
    assert array.status_code == 200, array.text
    assert array.json() == EXPECTED_ARRAY


def test_every_fixture_chunk_decodes_to_the_source_tiff_slice(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    codec = Blosc(
        cname="zstd",
        clevel=3,
        shuffle=Blosc.SHUFFLE,
        blocksize=0,
        typesize=2,
    )
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        dataset_ids = {
            (experiment, plate): _link(
                client,
                packed_root,
                experiment=experiment,
                plate=plate,
            )["id"]
            for experiment, plate in sorted(
                {(row["experiment"], row["plate"]) for row in _index_rows(packed_root)}
            )
        }
        for row in _index_rows(packed_root):
            source = _source_plane(packed_root, row)
            assert source.shape == (64, 64)
            assert source.dtype == np.dtype("uint16")
            dataset_id = dataset_ids[(row["experiment"], row["plate"])]
            field = f"{row['well']}_s{row['site']}.zarr"
            channel = int(row["channel"]) - 1
            for chunk_y in range(2):
                for chunk_x in range(2):
                    response = client.get(
                        f"/zarr/{dataset_id}/{field}/s0/c/{channel}/{chunk_y}/{chunk_x}",
                        headers=AUTH,
                    )
                    assert response.status_code == 200, response.text
                    decoded = np.frombuffer(codec.decode(response.content), dtype="<u2").reshape(
                        1, 32, 32
                    )
                    expected = source[
                        chunk_y * 32 : (chunk_y + 1) * 32,
                        chunk_x * 32 : (chunk_x + 1) * 32,
                    ]
                    np.testing.assert_array_equal(decoded[0], expected)


@pytest.mark.parametrize(
    "resource",
    [
        "A03_s1.zarr/zarr.json",
        "A01_s3.zarr/zarr.json",
        "A01_s01.zarr/zarr.json",
        "A01_s1.zarr/.zgroup",
        "A01_s1.zarr/s1/zarr.json",
        "A01_s1.zarr/s0/.zarray",
        "A01_s1.zarr/s0/0/0/0",
        "A01_s1.zarr/s0/c/-1/0/0",
        "A01_s1.zarr/s0/c/00/0/0",
        "A01_s1.zarr/s0/c/five/0/0",
        "A01_s1.zarr/s0/c/5/0/0",
        "A01_s1.zarr/s0/c/0/-1/0",
        "A01_s1.zarr/s0/c/0/2/0",
        "A01_s1.zarr/s0/c/0/0/2",
        "A01_s1.zarr/s0/c/0/0/0/extra",
    ],
)
def test_invalid_packed_resource_keys_are_strictly_not_found(
    packed_root: Path,
    tmp_path: Path,
    resource: str,
) -> None:
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        dataset_id = _link(client, packed_root)["id"]
        response = client.get(f"/zarr/{dataset_id}/{resource}", headers=AUTH)
    assert response.status_code == 404, response.text


def _copy_fixture(source: Path, destination: Path) -> Path:
    target = destination / "packed-copy"
    shutil.copytree(source, target)
    return target


def _write_index_rows(root: Path, rows: list[dict[str, Any]]) -> None:
    index_path = _index(root)
    source = pq.read_table(index_path)
    table = pa.Table.from_pylist(rows, schema=source.schema)
    pq.write_table(
        table,
        index_path,
        compression="zstd",
        use_dictionary=["experiment", "well", "shard"],
    )


def _corrupt_header(root: Path) -> None:
    row = _matching_row(root)
    path = root / row["shard"]
    with path.open("r+b") as shard:
        shard.seek(int(row["offset"]) - 512)
        original = shard.read(1)
        assert original
        shard.seek(-1, os.SEEK_CUR)
        shard.write(b"X" if original != b"X" else b"Y")


def _move_member_out_of_bounds(root: Path) -> None:
    rows = _index_rows(root)
    target = _matching_row(root)
    for row in rows:
        if row == target:
            row["offset"] = (root / row["shard"]).stat().st_size + 512
            break
    else:  # pragma: no cover - fixture invariant
        raise AssertionError("target row is missing")
    _write_index_rows(root, rows)


def _uint8_tiff(payload: bytes) -> bytes:
    plane = np.asarray(tifffile.imread(io.BytesIO(payload)), dtype=np.uint8)
    output = io.BytesIO()
    tifffile.imwrite(
        output,
        plane,
        photometric="minisblack",
        compression=None,
        metadata=None,
    )
    return output.getvalue()


def _rewrite_member_as_uint8(root: Path) -> None:
    target = _matching_row(root)
    target_name = f"idr0080/raw/{target['relpath']}"
    shard_path = root / target["shard"]
    replacement = shard_path.with_name(f"{shard_path.name}.replacement")

    with (
        tarfile.open(shard_path, mode="r:") as source,
        tarfile.open(replacement, mode="w", format=tarfile.USTAR_FORMAT) as destination,
    ):
        for member in source.getmembers():
            extracted = source.extractfile(member)
            assert extracted is not None
            payload = extracted.read()
            if member.name == target_name:
                payload = _uint8_tiff(payload)
            copied = tarfile.TarInfo(member.name)
            copied.size = len(payload)
            copied.mode = member.mode
            copied.mtime = member.mtime
            copied.uid = member.uid
            copied.gid = member.gid
            copied.uname = member.uname
            copied.gname = member.gname
            destination.addfile(copied, io.BytesIO(payload))

    replacement.replace(shard_path)
    with tarfile.open(shard_path, mode="r:") as archive:
        members = {member.name: member for member in archive.getmembers()}
    rows = _index_rows(root)
    for row in rows:
        if row["shard"] != target["shard"]:
            continue
        member = members[f"idr0080/raw/{row['relpath']}"]
        row["offset"] = member.offset_data
        row["size"] = member.size
    _write_index_rows(root, rows)


@pytest.mark.parametrize(
    ("case", "message_fragment"),
    [
        ("header", "header"),
        ("bounds", "bound"),
        ("dtype", "dtype"),
    ],
)
def test_corrupt_packed_members_return_clean_errors(
    packed_root: Path,
    tmp_path: Path,
    case: str,
    message_fragment: str,
) -> None:
    root = _copy_fixture(packed_root, tmp_path / case)
    if case == "header":
        _corrupt_header(root)
    elif case == "bounds":
        _move_member_out_of_bounds(root)
    else:
        _rewrite_member_as_uint8(root)

    with TestClient(create_app(_settings(root, tmp_path / f"{case}-state"), TOKEN)) as client:
        dataset_id = _link(client, root)["id"]
        response = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0",
            headers=AUTH,
        )

    assert response.status_code == 422, response.text
    detail = response.json().get("detail")
    assert isinstance(detail, str)
    assert message_fragment in detail.casefold()


def test_chunk_http_cache_and_range_semantics(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        dataset_id = _link(client, packed_root)["id"]
        url = f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0"

        first = client.get(url, headers=AUTH)
        assert first.status_code == 200, first.text
        assert first.headers["x-timed-cache"] == "miss"
        assert first.headers["accept-ranges"] == "bytes"
        assert first.headers["content-length"] == str(len(first.content))
        etag = first.headers["etag"]

        cached = client.get(url, headers=AUTH)
        assert cached.status_code == 200
        assert cached.content == first.content
        assert cached.headers["etag"] == etag
        assert cached.headers["x-timed-cache"] == "hit"

        unchanged = client.get(url, headers={**AUTH, "If-None-Match": etag})
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        assert unchanged.headers["etag"] == etag

        ranged = client.get(url, headers={**AUTH, "Range": "bytes=10-29"})
        assert ranged.status_code == 206
        assert ranged.content == first.content[10:30]
        assert ranged.headers["content-range"] == f"bytes 10-29/{len(first.content)}"
        assert ranged.headers["cache-control"] == "private, no-store"

        matching_if_range = client.get(
            url,
            headers={**AUTH, "Range": "bytes=10-29", "If-Range": etag},
        )
        assert matching_if_range.status_code == 206
        assert matching_if_range.content == first.content[10:30]

        stale_if_range = client.get(
            url,
            headers={**AUTH, "Range": "bytes=10-29", "If-Range": '"stale"'},
        )
        assert stale_if_range.status_code == 200
        assert stale_if_range.content == first.content

        unsatisfiable = client.get(
            url,
            headers={**AUTH, "Range": f"bytes={len(first.content)}-"},
        )
        assert unsatisfiable.status_code == 416
        assert unsatisfiable.headers["content-range"] == f"bytes */{len(first.content)}"

        head = client.head(url, headers=AUTH)
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == str(len(first.content))
        assert head.headers["etag"] == etag
        assert head.headers["x-timed-cache"] == "hit"


@pytest.mark.asyncio
async def test_authenticated_zarr_python_round_trip_does_not_block_event_loop(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    connection_file = tmp_path / "runtime" / "connection.json"
    settings = _settings(packed_root, tmp_path / "runtime-state")
    started = asyncio.Event()
    server_holder: list[Any] = []

    def on_started(server: Any) -> None:
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
                json={"path": str(_index(packed_root) / "idr0080-A549" / "14610")},
            )
        assert linked.status_code == 201, linked.text
        dataset_id = linked.json()["id"]

        def read_with_zarr() -> NDArray[np.generic]:
            store = FsspecStore.from_url(
                f"{origin}/zarr/{dataset_id}/A01_s1.zarr",
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

        read_task = asyncio.create_task(anyio.to_thread.run_sync(read_with_zarr))
        heartbeat = asyncio.Event()

        async def pulse() -> None:
            await asyncio.sleep(0)
            heartbeat.set()

        await asyncio.wait_for(pulse(), timeout=1)
        assert heartbeat.is_set()
        actual = await asyncio.wait_for(read_task, timeout=20)
        expected = np.stack(
            [
                _source_plane(packed_root, _matching_row(packed_root, channel=channel))
                for channel in range(1, 6)
            ]
        )
        np.testing.assert_array_equal(actual, expected)
    finally:
        if server_holder:
            server_holder[0].should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
