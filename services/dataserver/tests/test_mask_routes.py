"""The mask routes: a dataset's models, a field's counts and outline PNGs."""

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
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.mask_outlines import outline_mask

from scripts import make_test_data, mask_stores
from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "mask-route-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SHAPE = (make_test_data.PACKED_HEIGHT, make_test_data.PACKED_WIDTH)
WELLS = [well.address for well in make_test_data.PACKED_WELLS]
PACKED = Path("packed") / "idr0080-like" / make_test_data.PACKED_INDEX_NAME
B90 = mask_stores.DEFAULT_MODEL
STOCK = mask_stores.STOCK_MODEL


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory, write_audit: WriteAudit) -> Path:
    root = tmp_path_factory.mktemp("mask-routes")
    generate_test_data(root)
    write_audit.watch(root)
    return root


@pytest.fixture
def client(data_root: Path, tmp_path: Path) -> Iterator[TestClient]:
    settings = ServerSettings(
        data_roots=(data_root,),
        state_dir=tmp_path / "state",
        environment="local",
    )
    with TestClient(create_app(settings, TOKEN)) as test_client:
        yield test_client


def _link(client: TestClient, path: Path) -> str:
    response = client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _plate(client: TestClient, root: Path, experiment: str, plate: int) -> str:
    return _link(client, root / PACKED / experiment / str(plate))


def _expected(model: str, plate: int, well: str, site: int) -> dict[str, int]:
    return mask_stores.field_masks(model, plate, WELLS.index(well), site, SHAPE).counts


def _model(tag: str, *, available: bool, default: bool, recipe: str, created_at: str) -> dict:
    return {
        "model_tag": tag,
        "recipe": recipe,
        "kinds": ["nuclei", "cells", "cytoplasm"],
        "frozen": False,
        "created_at": created_at,
        "available": available,
        "default": default,
    }


B90_14610 = _model(
    B90, available=True, default=True, recipe="IDR0080_B90", created_at="2026-10-15T15:54:43+0200"
)


def _stock(*, available: bool) -> dict:
    return _model(
        STOCK,
        available=available,
        default=False,
        recipe="R2",
        created_at="2026-10-14T03:08:39+0200",
    )


def test_listing_offers_the_stock_model_only_on_14610(client: TestClient, data_root: Path) -> None:
    for experiment, plate, stock_available in (
        ("idr0080-A549", 14610, True),
        ("idr0080-A549", 14611, False),
        ("idr0080-ES2", 14613, False),
    ):
        dataset = _plate(client, data_root, experiment, plate)
        response = client.get(f"/api/datasets/{dataset}/masks", headers=AUTH)
        assert response.status_code == 200, response.text
        assert response.json() == {"models": [B90_14610, _stock(available=stock_available)]}


def test_listing_answers_every_linked_dataset_without_masks_with_no_models(
    client: TestClient,
    data_root: Path,
) -> None:
    for path in (data_root / "test_plate.zarr", data_root / "test_image_v05.zarr"):
        dataset = _link(client, path)
        response = client.get(f"/api/datasets/{dataset}/masks", headers=AUTH)
        assert response.status_code == 200, response.text
        assert response.json() == {"models": []}
        for route in ("masks/x/A01/1", "masks/x/A01/1/nuclei.png"):
            missing = client.get(f"/api/datasets/{dataset}/{route}", headers=AUTH)
            assert missing.status_code == 404
            assert missing.json() == {"detail": "This dataset has no segmentation masks"}
    for route in ("masks", "masks/x/A01/1", "masks/x/A01/1/nuclei.png"):
        unknown = client.get(f"/api/datasets/not-linked/{route}", headers=AUTH)
        assert unknown.status_code == 404
        assert unknown.json() == {"detail": "Dataset is not linked"}


def test_counts_are_each_kinds_largest_label_for_the_selected_model(
    client: TestClient,
    data_root: Path,
) -> None:
    dataset = _plate(client, data_root, "idr0080-A549", 14610)
    for model in (B90, STOCK):
        for well in WELLS:
            for site in make_test_data.PACKED_SITES:
                response = client.get(
                    f"/api/datasets/{dataset}/masks/{model}/{well.lower()}/{site}",
                    headers=AUTH,
                )
                assert response.status_code == 200, response.text
                assert response.json() == {
                    "model_tag": model,
                    "well": well,
                    "site": site,
                    "counts": _expected(model, 14610, well, site),
                }
    reference = client.get(f"/api/datasets/{dataset}/masks/{B90}/A01/1", headers=AUTH)
    assert reference.json()["counts"] == {"nuclei": 4, "cells": 2, "cytoplasm": 2}
    assert list(reference.json()["counts"]) == ["nuclei", "cells", "cytoplasm"]


def test_missing_models_fields_records_and_kinds_answer_404(
    client: TestClient,
    data_root: Path,
) -> None:
    dataset = _plate(client, data_root, "idr0080-A549", 14611)
    for route, detail in (
        (f"masks/{STOCK}/A01/1", f"Mask model {STOCK} does not cover this plate"),
        (f"masks/{mask_stores.OTHER_FORMAT_MODEL}/A01/1", "Unknown mask model: fixture_zarr_masks"),
        ("masks/fixture/A01/1", "Unknown mask model: fixture"),
        (f"masks/{B90}/C02/1", "Packed field does not exist: C02 site 1"),
        (f"masks/{B90}/A01/3", "Packed field does not exist: A01 site 3"),
        (f"masks/{B90}/A-1/1", "Packed field does not exist: A-1 site 1"),
        (f"masks/{B90}/A01/1/membranes.png", "No membranes mask for A01 site 1"),
        (f"masks/{STOCK}/A01/1/nuclei.png", f"Mask model {STOCK} does not cover this plate"),
    ):
        response = client.get(f"/api/datasets/{dataset}/{route}", headers=AUTH)
        assert response.status_code == 404, (route, response.text)
        assert response.json() == {"detail": detail}
    assert client.get(f"/api/datasets/{dataset}/masks/{B90}/A01/one", headers=AUTH).status_code == (
        422
    )


def _outline(client: TestClient, dataset: str, route: str, **headers: str) -> httpx.Response:
    return client.get(f"/api/datasets/{dataset}/masks/{route}", headers={**AUTH, **headers})


def test_the_outline_is_a_cached_one_bit_png_of_the_labels(
    client: TestClient,
    data_root: Path,
) -> None:
    dataset = _plate(client, data_root, "idr0080-A549", 14610)
    for model, kind, color, width in (
        (B90, "nuclei", "FFD400", 2),
        (B90, "cells", "ffffff", 1),
        (STOCK, "cytoplasm", "FF8C00", 4),
    ):
        response = _outline(
            client, dataset, f"{model}/A01/1/{kind}.png?color={color}&width={width}"
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "private, max-age=3600"
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["etag"].startswith('"')
        png = read_png(response.content)
        assert (png.height, png.width, png.bit_depth, png.color_type) == (*SHAPE, 1, 3)
        assert png.palette == bytes.fromhex(color * 2)
        assert png.transparency == bytes((0, 255))
        labels = mask_stores.field_masks(model, 14610, 0, 1, SHAPE).labels[kind]
        assert np.array_equal(png.pixels, outline_mask(labels, width))
    default = _outline(client, dataset, f"{B90}/A01/1/nuclei.png")
    assert read_png(default.content).palette == bytes.fromhex("FFFFFF" * 2)
    assert np.array_equal(
        read_png(default.content).pixels,
        outline_mask(mask_stores.field_masks(B90, 14610, 0, 1, SHAPE).labels["nuclei"], 2),
    )


def test_outline_etags_give_304_without_rendering_and_change_with_each_input(
    client: TestClient,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _plate(client, data_root, "idr0080-A549", 14610)
    route = f"{B90}/A01/1/nuclei.png?color=FFD400&width=2"
    first = _outline(client, dataset, route)
    etag = first.headers["etag"]
    assert _outline(client, dataset, route).headers["etag"] == etag
    assert _outline(client, dataset, route.replace("FFD400", "ffd400")).headers["etag"] == etag

    reader = client.app.state.context.masks  # type: ignore[attr-defined]

    def must_not_render(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("a matching validator must not render the outline")

    monkeypatch.setattr(reader, "outline", must_not_render)
    cached = _outline(client, dataset, route, **{"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == etag
    assert cached.headers["cache-control"] == "private, max-age=3600"
    monkeypatch.undo()

    others = {
        _outline(client, dataset, variant).headers["etag"]
        for variant in (
            route.replace("FFD400", "FF8C00"),
            route.replace("width=2", "width=3"),
            route.replace("nuclei", "cells"),
            route.replace("A01/1", "A01/2"),
            route.replace(B90, STOCK),
        )
    }
    assert len(others) == 5 and etag not in others

    partial = _outline(client, dataset, route, Range="bytes=0-7")
    assert partial.status_code == 206
    assert partial.content == b"\x89PNG\r\n\x1a\n"
    assert partial.headers["content-range"] == f"bytes 0-7/{len(first.content)}"


@pytest.mark.parametrize(
    "query",
    ["color=FFD40", "color=GGGGGG", "color=%23FFD400", "width=0", "width=5", "width=two"],
)
def test_outline_parameters_are_validated(
    client: TestClient,
    data_root: Path,
    query: str,
) -> None:
    dataset = _plate(client, data_root, "idr0080-A549", 14610)
    assert _outline(client, dataset, f"{B90}/A01/1/nuclei.png?{query}").status_code == 422


def _copied_root(source: Path, tmp_path: Path) -> Path:
    root = tmp_path / "copied"
    shutil.copytree(source / "packed" / "idr0080-like", root)
    return root


def _isolated(root: Path, tmp_path: Path) -> TestClient:
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
    )
    return TestClient(create_app(settings, TOKEN))


def test_a_corrupt_second_model_never_blocks_the_default(
    data_root: Path,
    tmp_path: Path,
) -> None:
    root = _copied_root(data_root, tmp_path)
    stock_tar = root / "masks" / STOCK / "idr0080-A549" / "Plate14610.masks.tar"
    stock_tar.write_bytes(stock_tar.read_bytes()[:4096])
    with _isolated(root, tmp_path) as client:
        dataset = _link(client, root / make_test_data.PACKED_INDEX_NAME / "idr0080-A549" / "14610")
        listing = client.get(f"/api/datasets/{dataset}/masks", headers=AUTH)
        assert listing.status_code == 200
        assert [model["model_tag"] for model in listing.json()["models"]] == [B90, STOCK]
        assert client.get(f"/api/datasets/{dataset}/masks/{B90}/A01/1", headers=AUTH).json()[
            "counts"
        ] == {"nuclei": 4, "cells": 2, "cytoplasm": 2}
        assert _outline(client, dataset, f"{B90}/A01/1/nuclei.png").status_code == 200
        for route in (f"masks/{STOCK}/A01/1", f"masks/{STOCK}/A01/1/nuclei.png"):
            corrupt = client.get(f"/api/datasets/{dataset}/{route}", headers=AUTH)
            assert corrupt.status_code == 409, corrupt.text
            assert corrupt.json() == {
                "detail": (
                    f"Mask store {STOCK}/idr0080-A549/Plate14610 is corrupt: "
                    "the tar has 4096 bytes but the log commits 30720"
                )
            }
            assert str(tmp_path) not in corrupt.text


def test_a_growing_store_changes_the_outline_etag_and_counts(
    data_root: Path,
    tmp_path: Path,
) -> None:
    root = _copied_root(data_root, tmp_path)
    plate, well, site = mask_stores.IN_PROGRESS_FIELD
    log = root / "masks" / B90 / "idr0080-A549" / f"Plate{plate}.wal.jsonl"
    fields = [
        (name, number, mask_stores.field_masks(B90, plate, index, number, SHAPE))
        for index, name in enumerate(WELLS)
        for number in make_test_data.PACKED_SITES
    ]
    pending = mask_stores.write_plate_store(
        tmp_path / "pending" / B90,
        B90,
        "idr0080-A549",
        plate,
        fields,
        in_progress=(well, site, mask_stores.in_progress_masks(SHAPE)),
    )
    assert pending.pending_line is not None
    with _isolated(root, tmp_path) as client:
        index = root / make_test_data.PACKED_INDEX_NAME
        dataset = _link(client, index / "idr0080-A549" / str(plate))
        counts = f"/api/datasets/{dataset}/masks/{B90}/{well}/{site}"
        before = client.get(counts, headers=AUTH).json()["counts"]
        etag = _outline(client, dataset, f"{B90}/{well}/{site}/nuclei.png").headers["etag"]
        assert before == _expected(B90, plate, well, site)
        with log.open("ab") as handle:
            handle.write(pending.pending_line[pending.partial_bytes :])
        assert client.get(counts, headers=AUTH).json()["counts"] == (
            mask_stores.in_progress_masks(SHAPE).counts
        )
        grown = _outline(client, dataset, f"{B90}/{well}/{site}/nuclei.png")
        assert grown.headers["etag"] != etag
        outline = f"{B90}/{well}/{site}/nuclei.png"
        assert _outline(client, dataset, outline, **{"If-None-Match": etag}).status_code == 200


@pytest.mark.asyncio
async def test_mask_decoding_and_rendering_run_off_the_event_loop(
    data_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        ServerSettings(data_roots=(data_root,), state_dir=tmp_path / "state", environment="local"),
        TOKEN,
    )
    event_loop_thread = threading.get_ident()
    reader = app.state.context.masks
    threads: list[tuple[str, int]] = []
    for name in ("models", "plate", "counts", "target", "outline"):
        original = getattr(reader, name)

        def tracked(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            threads.append((_name, threading.get_ident()))
            return _original(*args, **kwargs)

        monkeypatch.setattr(reader, name, tracked)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(data_root / PACKED / "idr0080-A549" / "14610")},
        )
        dataset = linked.json()["id"]
        responses = [
            await client.get(f"/api/datasets/{dataset}/{route}", headers=AUTH)
            for route in ("masks", f"masks/{B90}/A01/1", f"masks/{B90}/A01/1/cells.png")
        ]
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert {name for name, _thread in threads} == {"models", "plate", "counts", "target", "outline"}
    assert event_loop_thread not in {thread for _name, thread in threads}
