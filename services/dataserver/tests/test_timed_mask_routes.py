"""TIMED masks over HTTP: per-field listings, counts and outlines that follow time."""

from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from png_reader import read_png
from served_stores import write_native_plate
from timed_dataserver import readers
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.mask_outlines import outline_mask
from timed_dataserver.timed_mask_config import TimedMaskMapping

from scripts import make_test_data, timed_mask_stores
from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "timed-mask-route-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SHAPE = (make_test_data.HEIGHT, make_test_data.WIDTH)
MODEL = "masks_raw"
MASKS_RAW = {
    "model_tag": MODEL,
    "recipe": "Cellpose-SAM cells (raw 505 + 625 nm) · classical nuclei (raw 625 nm)",
    "kinds": ["nuclei", "cells"],
    "frozen": True,
    "created_at": None,
    "available": True,
    "default": True,
}


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory, write_audit: WriteAudit) -> Path:
    root = tmp_path_factory.mktemp("timed-mask-routes")
    generate_test_data(root)
    write_audit.watch(root)
    return root


def _settings(root: Path, state: Path, masks_root: Path | None = None) -> ServerSettings:
    return ServerSettings(
        data_roots=(root,),
        state_dir=state,
        environment="local",
        timed_masks=(
            TimedMaskMapping(root, masks_root or root / timed_mask_stores.TIMED_MASKS_DIRECTORY),
        ),
    )


@pytest.fixture
def client(data_root: Path, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(data_root, tmp_path / "state"), TOKEN)) as test_client:
        yield test_client


def _link(client: TestClient, path: Path) -> str:
    response = client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _get(client: TestClient, dataset: str, route: str, **params: Any) -> httpx.Response:
    return client.get(f"/api/datasets/{dataset}/{route}", headers=AUTH, params=params)


def _field_index(plate: str, well: str, site: int) -> int:
    return timed_mask_stores.MASKED_FIELDS.index((plate, well, site))


def test_listings_offer_masks_raw_only_where_the_field_has_masks(
    client: TestClient,
    data_root: Path,
) -> None:
    plate = _link(client, data_root / "test_plate.zarr")
    # Without a field, a TIMED plate lists no models, exactly as before it had masks.
    assert _get(client, plate, "masks").json() == {"models": []}
    for well, site in (("A01", 1), ("a02", 1), ("B01", 1)):
        assert _get(client, plate, "masks", well=well, site=site).json() == {"models": [MASKS_RAW]}
    for well, site in (("A01", 2), ("B01", 2), ("Z99", 1), ("A-1", 1)):
        listing = _get(client, plate, "masks", well=well, site=site)
        assert listing.status_code == 200
        assert listing.json() == {"models": []}
    no_masks = _link(client, data_root / "test_plate_3.zarr")
    assert _get(client, no_masks, "masks").json() == {"models": []}
    assert _get(client, no_masks, "masks", well="B01", site=1).json() == {"models": []}
    native = _link(client, data_root / "test_image_v05.zarr")
    assert _get(client, native, "masks", well="A01", site=1).json() == {"models": []}
    for params in ({"well": "A01"}, {"site": 1}):
        refused = _get(client, plate, "masks", **params)
        assert refused.status_code == 422
        assert refused.json() == {"detail": "Give well and site together, or neither"}


def test_counts_follow_the_timepoint(client: TestClient, data_root: Path) -> None:
    for plate_name, well, site in timed_mask_stores.MASKED_FIELDS:
        dataset = _link(client, data_root / f"{plate_name}.zarr")
        index = _field_index(plate_name, well, site)
        for t in range(make_test_data.TIMEPOINTS):
            response = _get(client, dataset, f"masks/{MODEL}/{well.lower()}/{site}", t=t)
            assert response.status_code == 200, response.text
            assert response.json() == {
                "model_tag": MODEL,
                "well": well,
                "site": site,
                "counts": {
                    "nuclei": timed_mask_stores.nucleus_count(index, t),
                    "cells": timed_mask_stores.cell_count(index, t),
                },
            }


def test_timepoints_are_required_and_bounded(client: TestClient, data_root: Path) -> None:
    dataset = _link(client, data_root / "test_plate.zarr")
    for route in (f"masks/{MODEL}/A01/1", f"masks/{MODEL}/A01/1/cells.png"):
        missing = _get(client, dataset, route)
        assert missing.status_code == 422
        assert missing.json() == {"detail": "TIMED masks have one plane per timepoint: give t"}
        beyond = _get(client, dataset, route, t=make_test_data.TIMEPOINTS)
        assert beyond.status_code == 422
        assert beyond.json() == {
            "detail": "Timepoint 6 is outside this field's 6 timepoints (0 to 5)"
        }
        assert _get(client, dataset, route, t=-1).status_code == 422


def test_missing_models_fields_masks_and_kinds_answer_404(
    client: TestClient,
    data_root: Path,
) -> None:
    dataset = _link(client, data_root / "test_plate.zarr")
    for route, detail in (
        ("masks/masks_corrected/A01/1", "Unknown mask model: masks_corrected"),
        (f"masks/{MODEL}/A01/2", "No masks for A01 site 2"),
        (f"masks/{MODEL}/C09/1", "TIMED field does not exist: C09 site 1"),
        (f"masks/{MODEL}/A-1/1", "TIMED field does not exist: A-1 site 1"),
        (f"masks/{MODEL}/A01/1/membranes.png", "No membranes mask for A01 site 1"),
    ):
        response = _get(client, dataset, route, t=0)
        assert response.status_code == 404, (route, response.text)
        assert response.json() == {"detail": detail}
        assert str(data_root) not in response.text


def _outline(
    client: TestClient, dataset: str, t: int, kind: str = "cells", **params: Any
) -> httpx.Response:
    return _get(client, dataset, f"masks/{MODEL}/A01/1/{kind}.png", t=t, **params)


def test_outlines_draw_the_requested_frame(client: TestClient, data_root: Path) -> None:
    dataset = _link(client, data_root / "test_plate.zarr")
    pixels = []
    for t in range(make_test_data.TIMEPOINTS):
        response = _outline(client, dataset, t, color="FFFFFF", width=2)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        image = read_png(response.content)
        labels = timed_mask_stores.frame_labels(0, t, *SHAPE)
        assert np.array_equal(image.pixels, outline_mask(labels, 2))
        pixels.append(image.pixels)
        nuclei = read_png(_outline(client, dataset, t, "nuclei", color="FFD400").content)
        expected = outline_mask(timed_mask_stores.frame_nuclei(0, t, *SHAPE), 2)
        assert np.array_equal(nuclei.pixels, expected)
    assert all(not np.array_equal(pixels[t], pixels[t + 1]) for t in range(len(pixels) - 1))


def test_outline_etags_include_the_timepoint_and_answer_304_without_rendering(
    client: TestClient,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _link(client, data_root / "test_plate.zarr")
    first = _outline(client, dataset, 1)
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "private, max-age=3600"
    reader = client.app.state.context.masks  # type: ignore[attr-defined]

    def must_not_render(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("a matching validator must not render the outline")

    monkeypatch.setattr(reader, "rendered", must_not_render)
    revalidated = client.get(
        f"/api/datasets/{dataset}/masks/{MODEL}/A01/1/cells.png",
        headers={**AUTH, "If-None-Match": etag},
        params={"t": 1},
    )
    assert revalidated.status_code == 304
    assert revalidated.content == b""
    monkeypatch.undo()
    others = {
        _outline(client, dataset, 2).headers["etag"],
        _outline(client, dataset, 1, color="FFD400").headers["etag"],
        _outline(client, dataset, 1, width=3).headers["etag"],
        _outline(client, dataset, 1, "nuclei").headers["etag"],
    }
    assert len(others) == 4 and etag not in others


def _isolated_copy(data_root: Path, tmp_path: Path) -> Path:
    root = tmp_path / "copy"
    root.mkdir()
    shutil.copytree(data_root / "test_plate.zarr", root / "test_plate.zarr")
    shutil.copytree(
        data_root / timed_mask_stores.TIMED_MASKS_DIRECTORY,
        root / timed_mask_stores.TIMED_MASKS_DIRECTORY,
    )
    return root


def test_a_regenerated_export_is_served_without_a_restart(
    data_root: Path,
    tmp_path: Path,
) -> None:
    root = _isolated_copy(data_root, tmp_path)
    field_group = root / timed_mask_stores.TIMED_MASKS_DIRECTORY / "test_plate.zarr" / "A01_s1.zarr"
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        dataset = _link(client, root / "test_plate.zarr")
        before = _get(client, dataset, f"masks/{MODEL}/A01/1", t=0).json()["counts"]
        etag = _outline(client, dataset, 0).headers["etag"]
        regenerated = timed_mask_stores.field_labels(3, make_test_data.TIMEPOINTS, *SHAPE)
        timed_mask_stores.write_mask_array(
            field_group, regenerated, range(make_test_data.TIMEPOINTS)
        )
        after = _get(client, dataset, f"masks/{MODEL}/A01/1", t=0).json()["counts"]
        nuclei = timed_mask_stores.nucleus_count(0, 0)
        assert before == {"nuclei": nuclei, "cells": timed_mask_stores.cell_count(0, 0)}
        assert after == {"nuclei": nuclei, "cells": int(regenerated[0].max())}
        assert after != before
        refreshed = _outline(client, dataset, 0)
        assert refreshed.headers["etag"] != etag
        assert np.array_equal(read_png(refreshed.content).pixels, outline_mask(regenerated[0], 2))


def test_invalid_masks_answer_409_without_absolute_paths(
    data_root: Path,
    tmp_path: Path,
) -> None:
    root = _isolated_copy(data_root, tmp_path)
    array_dir = (
        root / timed_mask_stores.TIMED_MASKS_DIRECTORY / "test_plate.zarr" / "A01_s1.zarr"
    ) / "masks_raw"
    shard = array_dir / "0.0.0"
    shard.write_bytes(shard.read_bytes()[:-4] + b"\0\0\0\0")
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        dataset = _link(client, root / "test_plate.zarr")
        for route in (f"masks/{MODEL}/A01/1", f"masks/{MODEL}/A01/1/cells.png"):
            response = _get(client, dataset, route, t=0)
            assert response.status_code == 409
            assert response.json() == {
                "detail": (
                    "TIMED masks test_plate.zarr/A01_s1.zarr/masks_raw are invalid: "
                    "the shard index fails its CRC32C check"
                )
            }
            assert str(tmp_path) not in response.text
        assert _get(client, dataset, "masks", well="A01", site=1).json() == {"models": [MASKS_RAW]}


def test_a_field_without_one_kind_offers_the_others(data_root: Path, tmp_path: Path) -> None:
    root = _isolated_copy(data_root, tmp_path)
    field = root / timed_mask_stores.TIMED_MASKS_DIRECTORY / "test_plate.zarr" / "A01_s1.zarr"
    shutil.rmtree(field / timed_mask_stores.NUCLEUS_ARRAY)
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        dataset = _link(client, root / "test_plate.zarr")
        assert _get(client, dataset, "masks", well="A01", site=1).json() == {"models": [MASKS_RAW]}
        counts = _get(client, dataset, f"masks/{MODEL}/A01/1", t=2).json()["counts"]
        assert counts == {"cells": timed_mask_stores.cell_count(0, 2)}
        assert _outline(client, dataset, 2).status_code == 200
        missing = _outline(client, dataset, 2, "nuclei")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "No nuclei mask for A01 site 1"}


def test_mask_folders_are_never_browsable_linkable_or_listed(
    client: TestClient,
    data_root: Path,
) -> None:
    masks = data_root / timed_mask_stores.TIMED_MASKS_DIRECTORY
    root_listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(data_root)})
    assert timed_mask_stores.TIMED_MASKS_DIRECTORY not in {
        entry["name"] for entry in root_listing.json()["entries"]
    }
    relative = timed_mask_stores.TIMED_MASKS_DIRECTORY
    for path in (str(masks), str(masks / "test_plate.zarr"), relative):
        browsed = client.get("/api/fs/list", headers=AUTH, params={"path": path})
        assert browsed.status_code == 403
        assert browsed.json() == {"detail": "Configured mask folders are not browsable"}
        linked = client.post("/api/datasets", headers=AUTH, json={"path": path})
        assert linked.status_code == 403
        assert linked.json() == {"detail": "Configured mask folders cannot be linked"}


def test_a_mask_folder_inside_a_linked_store_is_not_served(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = write_native_plate(root / "store.zarr")
    masks = store / "masks"
    (masks / "notes").mkdir(parents=True)
    (masks / "notes" / "private.txt").write_text("private", encoding="utf-8")
    with TestClient(create_app(_settings(root, tmp_path / "state", masks), TOKEN)) as client:
        dataset = _link(client, store)
        assert client.get(f"/zarr/{dataset}/.zattrs", headers=AUTH).status_code == 200
        for resource in ("masks/notes/private.txt", "masks/missing.txt", "masks"):
            response = client.get(f"/zarr/{dataset}/{resource}", headers=AUTH)
            assert response.status_code == 403
            assert response.json() == {"detail": "Configured mask folders are not served"}
            assert b"private" not in response.content


@pytest.mark.needs_symlinks
def test_symlinks_never_lead_out_of_the_mask_folder(data_root: Path, tmp_path: Path) -> None:
    root = _isolated_copy(data_root, tmp_path)
    masks_plate = root / timed_mask_stores.TIMED_MASKS_DIRECTORY / "test_plate.zarr"
    outside = tmp_path / "outside" / "A01_s1.zarr"
    shutil.move(masks_plate / "A01_s1.zarr", outside)
    try:
        (masks_plate / "A01_s1.zarr").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        dataset = _link(client, root / "test_plate.zarr")
        assert _get(client, dataset, "masks", well="A01", site=1).json() == {"models": []}
        refused = _get(client, dataset, f"masks/{MODEL}/A01/1", t=0)
        assert refused.status_code == 404
        assert refused.json() == {"detail": "No masks for A01 site 1"}
        assert _get(client, dataset, "masks", well="A02", site=1).json() == {"models": [MASKS_RAW]}


@pytest.mark.asyncio
async def test_timed_mask_reads_run_off_the_event_loop(
    data_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(data_root, tmp_path / "state"), TOKEN)
    event_loop_thread = threading.get_ident()
    reader = app.state.context.masks
    threads: list[tuple[str, int]] = []

    def track(owner: Any, name: str) -> None:
        original = getattr(owner, name)

        def tracked(*args: Any, **kwargs: Any) -> Any:
            threads.append((name, threading.get_ident()))
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, tracked)

    track(reader, "decoded")
    track(reader, "rendered")
    track(reader.timed_arrays, "get")
    track(readers.timed, "has_array")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/datasets", headers=AUTH, json={"path": str(data_root / "test_plate.zarr")}
        )
        dataset = linked.json()["id"]
        responses = [
            await client.get(f"/api/datasets/{dataset}/{route}", headers=AUTH, params=params)
            for route, params in (
                ("masks", {"well": "A01", "site": 1}),
                (f"masks/{MODEL}/A01/1", {"t": 3}),
                (f"masks/{MODEL}/A01/1/cells.png", {"t": 4}),
            )
        ]
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert {name for name, _thread in threads} == {"decoded", "rendered", "get", "has_array"}
    assert event_loop_thread not in {thread for _name, thread in threads}
