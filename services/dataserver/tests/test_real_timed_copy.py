from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import anyio
import httpx
import numpy as np
import pytest
import zarr
from timed_dataserver.app import ServerSettings
from timed_dataserver.cli import serve
from zarr.storage import FsspecStore

TOKEN = "real-local-read-only-test"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PLATE_NAME = "PB100140_P2.1"


def _copy_root() -> Path:
    """The external TIMED copy, which these opt-in checks never assume."""
    root = os.environ.get("TIMED_REAL_TIMED_COPY_ROOT")
    if not root:
        pytest.skip("set TIMED_REAL_TIMED_COPY_ROOT to the external TIMED copy's folder")
    return Path(root)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


@pytest.mark.asyncio
async def test_real_berzelius_layout_copy_read_only(tmp_path: Path) -> None:
    if os.environ.get("TIMED_TEST_REAL_TIMED_COPY") != "1":
        pytest.skip("set TIMED_TEST_REAL_TIMED_COPY=1 to test the external TIMED copy")

    copy_root = _copy_root()
    data_root = copy_root / "zarr"
    metadata_root = copy_root / "metadata"
    plate = data_root / f"{PLATE_NAME}.zarr"
    field = plate / "C03_s1.zarr"
    protected_files = (
        plate / "zarr.json",
        field / "zarr.json",
        metadata_root / "acquisition" / f"{PLATE_NAME}.json",
        metadata_root / "csv" / f"{PLATE_NAME}.csv",
    )
    assert data_root.is_dir()
    assert metadata_root.is_dir()
    assert plate.is_dir()
    assert field.is_dir()
    assert all(path.is_file() for path in protected_files)

    original_entries = tuple(sorted(path.name for path in plate.iterdir()))
    original_digests = {path: _digest(path) for path in protected_files}
    connection_file = tmp_path / "runtime" / "connection.json"
    settings = ServerSettings(
        data_roots=(data_root,),
        state_dir=tmp_path / "state",
        environment="local",
        metadata_root=metadata_root,
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
        await asyncio.wait_for(started.wait(), timeout=20)
        connection = json.loads(connection_file.read_text(encoding="utf-8"))
        origin = f"http://127.0.0.1:{connection['port']}"
        async with httpx.AsyncClient(base_url=origin, timeout=30) as client:
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
            metadata = await client.get(
                f"/api/datasets/{dataset_id}/metadata",
                headers=AUTH,
            )
            well = await client.get(
                f"/api/datasets/{dataset_id}/wells/C03",
                headers=AUTH,
            )

        assert summary.status_code == 200, summary.text
        assert summary.json()["first_image"]["levels"][0]["chunks"] == [
            1,
            1,
            2500,
            2500,
        ]
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["available"] is True
        assert metadata.json()["plate"]["plate_id"] == "P2.1"
        assert len(metadata.json()["wells"]) == 196
        assert well.status_code == 200, well.text
        c03_site_1 = next(
            item for item in well.json()["fields"] if item["path"] == "C03_s1.zarr"
        )
        assert c03_site_1["site_position_fix"] == {
            "grid_x": 1,
            "repaired_timepoints": [0],
        }

        def read_real_plane() -> np.ndarray:
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
            return np.asarray(group["s0"][0, 0, :, :])

        plane = await asyncio.wait_for(
            asyncio.create_task(anyio.to_thread.run_sync(read_real_plane)),
            timeout=180,
        )
        assert plane.shape == (2500, 2500)
        assert plane.dtype == np.dtype("uint16")
        assert int(plane.max()) > int(plane.min())
    finally:
        if server_holder:
            server_holder[0].should_exit = True  # type: ignore[attr-defined]
        await asyncio.wait_for(server_task, timeout=20)

    assert tuple(sorted(path.name for path in plate.iterdir())) == original_entries
    assert {path: _digest(path) for path in protected_files} == original_digests
