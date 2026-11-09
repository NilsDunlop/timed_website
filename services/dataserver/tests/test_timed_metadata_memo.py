"""TIMED requests reuse the plate listing and field metadata, yet see every change.

DS-20: a request used to list the plate folder, resolve every field and re-read
the field's metadata. Both are now memoised by file identity, so these tests
check that repeated requests do neither, and that an added, removed or rewritten
field is picked up on the next request without a restart.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import zarr
from fastapi.testclient import TestClient
from served_stores import write_berzelius_plate, write_json
from timed_dataserver import omezarr
from timed_dataserver.app import ServerSettings, create_app
from zarr.codecs import BloscCodec

TOKEN = "timed-metadata-memo-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# Longer than a filesystem timestamp tick, so a change gets a new modification time.
TICK = 0.05


@pytest.fixture
def plate(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return write_berzelius_plate(root)[0]


@pytest.fixture
def client(plate: Path, tmp_path: Path) -> Iterator[TestClient]:
    settings = ServerSettings(
        data_roots=(plate.parent,),
        state_dir=tmp_path / "state",
        environment="local",
    )
    with TestClient(create_app(settings, TOKEN)) as test_client:
        yield test_client


def _link(client: TestClient, path: Path) -> str:
    response = client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _add_field(plate: Path, well: str, site: int = 1) -> Path:
    data = np.zeros((2, 3, 16, 16), dtype=np.uint16)
    field = zarr.create_array(
        plate / f"{well}_s{site}.zarr",
        shape=data.shape,
        dtype="uint16",
        chunks=(1, 1, 16, 16),
        shards=data.shape,
        compressors=[BloscCodec(cname="zstd", clevel=5, shuffle="shuffle")],
        attributes={
            "timed": {
                "well": well,
                "site": site,
                "axes": ["t", "c", "y", "x"],
                "channels_nm": [405, 505, 625],
                "t_indices": [0, 1],
            }
        },
        overwrite=True,
    )
    field[:] = data
    return plate / f"{well}_s{site}.zarr"


def _wells(client: TestClient, dataset: str) -> list[str]:
    summary = client.get(f"/api/datasets/{dataset}/summary", headers=AUTH)
    assert summary.status_code == 200, summary.text
    return [well["path"] for well in summary.json()["wells"]]


def test_repeated_requests_neither_rescan_the_plate_nor_reread_metadata(
    client: TestClient,
    plate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _link(client, plate)
    calls: dict[str, int] = {"scan": 0, "load": 0}

    def counted(name: str, original: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calls[name] += 1
            return original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(omezarr, "_scan_collection", counted("scan", omezarr._scan_collection))
    monkeypatch.setattr(omezarr, "_load_json", counted("load", omezarr._load_json))
    zarr_root = f"/zarr/{dataset}/C03_s1.zarr"
    for _ in range(3):
        for path in (
            f"/api/datasets/{dataset}/summary",
            f"/api/datasets/{dataset}/wells/C03",
            f"{zarr_root}/zarr.json",
            f"{zarr_root}/s0/zarr.json",
            f"{zarr_root}/s0/c/0/0/0/0",
        ):
            assert client.get(path, headers=AUTH).status_code == 200, path
    assert calls == {"scan": 0, "load": 0}


def test_an_added_or_removed_field_is_listed_on_the_next_request(
    client: TestClient,
    plate: Path,
) -> None:
    dataset = _link(client, plate)
    assert _wells(client, dataset) == ["C03"]
    time.sleep(TICK)
    added = _add_field(plate, "C04")
    assert _wells(client, dataset) == ["C03", "C04"]
    fields = client.get(f"/api/datasets/{dataset}/wells/C04", headers=AUTH)
    assert [field["path"] for field in fields.json()["fields"]] == ["C04_s1.zarr"]
    assert client.get(f"/zarr/{dataset}/C04_s1.zarr/zarr.json", headers=AUTH).status_code == 200

    time.sleep(TICK)
    shutil.rmtree(added)
    assert _wells(client, dataset) == ["C03"]
    gone = client.get(f"/api/datasets/{dataset}/wells/C04", headers=AUTH)
    assert gone.status_code == 404
    assert client.get(f"/zarr/{dataset}/C04_s1.zarr/zarr.json", headers=AUTH).status_code == 404


def test_rewritten_field_metadata_is_read_again(client: TestClient, plate: Path) -> None:
    dataset = _link(client, plate)
    array_metadata = f"/zarr/{dataset}/C03_s1.zarr/s0/zarr.json"
    before = client.get(array_metadata, headers=AUTH).json()
    assert before["attributes"]["timed"]["channels_nm"] == [405, 505, 625]

    time.sleep(TICK)
    path = plate / "C03_s1.zarr" / "zarr.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["attributes"]["timed"]["channels_nm"] = [405, 488, 625]
    write_json(path, metadata)
    after = client.get(array_metadata, headers=AUTH).json()
    assert after["attributes"]["timed"]["channels_nm"] == [405, 488, 625]

    time.sleep(TICK)
    metadata["data_type"] = "float32"
    write_json(path, metadata)
    broken = client.get(array_metadata, headers=AUTH)
    assert broken.status_code == 404
    assert broken.json() == {"detail": "Linked path is no longer a recognized OME-Zarr dataset"}


def test_the_virtual_array_metadata_is_the_same_on_every_request(
    client: TestClient,
    plate: Path,
) -> None:
    dataset = _link(client, plate)
    responses = [
        client.get(f"/zarr/{dataset}/C03_s1.zarr/s0/zarr.json", headers=AUTH).content
        for _ in range(3)
    ]
    assert responses[0] == responses[1] == responses[2]
    assert json.loads(responses[0])["dimension_names"] == ["t", "c", "y", "x"]
    # The memoised metadata itself never gains the facade's dimension names.
    assert "dimension_names" not in omezarr._timed_field_metadata(plate / "C03_s1.zarr")
