"""Dataset readers: typed linked results, one reader per kind and the mask-source hook."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import HTTPException
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.linked import LinkedPackedPlate, LinkedStore, linked, require_packed_plate
from timed_dataserver.mask_reader import MaskNotFoundError, MaskRequestError
from timed_dataserver.readers import open_reader
from timed_dataserver.readers.base import MaskField
from timed_dataserver.readers.native import NativeStoreReader
from timed_dataserver.readers.packed import PackedMaskSource, PackedPlateReader
from timed_dataserver.readers.timed import TimedCollectionReader, TimedMaskSource
from timed_dataserver.timed_mask_config import TimedMaskMapping

from scripts import make_test_data, mask_stores, timed_mask_stores
from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit
    from fastapi import FastAPI

TOKEN = "reader-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SHAPE = (make_test_data.PACKED_HEIGHT, make_test_data.PACKED_WIDTH)
PACKED = Path("packed") / "idr0080-like" / make_test_data.PACKED_INDEX_NAME
B90 = mask_stores.DEFAULT_MODEL


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory, write_audit: WriteAudit) -> Path:
    root = tmp_path_factory.mktemp("readers")
    generate_test_data(root)
    write_audit.watch(root)
    return root


def _app(root: Path, tmp_path: Path, *, timed_masks: bool = False) -> FastAPI:
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
        timed_masks=(
            (TimedMaskMapping(root, root / timed_mask_stores.TIMED_MASKS_DIRECTORY),)
            if timed_masks
            else ()
        ),
    )
    return create_app(settings, TOKEN)


async def _link(app: FastAPI, path: Path) -> str:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("relative", "reader_type", "linked_type"),
    [
        (Path("test_plate.zarr"), TimedCollectionReader, LinkedStore),
        (Path("test_image_v04.zarr"), NativeStoreReader, LinkedStore),
        (Path("test_image_v05.zarr"), NativeStoreReader, LinkedStore),
        (PACKED / "idr0080-A549" / "14610", PackedPlateReader, LinkedPackedPlate),
    ],
)
async def test_each_kind_opens_its_own_reader(
    data_root: Path,
    tmp_path: Path,
    relative: Path,
    reader_type: type,
    linked_type: type,
) -> None:
    app = _app(data_root, tmp_path)
    dataset_id = await _link(app, data_root / relative)
    ctx = app.state.context
    assert type(await linked(ctx, dataset_id)) is linked_type
    reader = await open_reader(ctx, dataset_id)
    assert type(reader) is reader_type
    if reader_type is PackedPlateReader:
        assert isinstance(reader.mask_source(), PackedMaskSource)
    else:
        assert reader.mask_source() is None


@pytest.mark.asyncio
async def test_packed_plates_link_their_experiment_and_plate(
    data_root: Path,
    tmp_path: Path,
) -> None:
    app = _app(data_root, tmp_path)
    ctx = app.state.context
    plate_id = await _link(app, data_root / PACKED / "idr0080-A549" / "14610")
    timed_id = await _link(app, data_root / "test_plate.zarr")
    plate = await require_packed_plate(ctx, plate_id, "only packed plates")
    assert (plate.experiment, plate.plate) == ("idr0080-A549", 14610)
    assert plate.packed.index_path.name == make_test_data.PACKED_INDEX_NAME
    with pytest.raises(HTTPException) as refused:
        await require_packed_plate(ctx, timed_id, "only packed plates")
    assert (refused.value.status_code, refused.value.detail) == (404, "only packed plates")
    with pytest.raises(HTTPException) as unknown:
        await open_reader(ctx, "not-linked")
    assert (unknown.value.status_code, unknown.value.detail) == (404, "Dataset is not linked")


@pytest.mark.asyncio
async def test_inferred_packed_profiles_offer_no_mask_source(tmp_path: Path) -> None:
    root = make_test_data._write_packed_fixture(tmp_path / "generated")
    index = root / make_test_data.PACKED_INDEX_NAME
    renamed = index.rename(index.with_name("generic.index.parquet"))
    app = _app(root, tmp_path)
    dataset_id = await _link(app, renamed / "idr0080-A549" / "14610")
    reader = await open_reader(app.state.context, dataset_id)
    assert isinstance(reader, PackedPlateReader)
    assert reader.mask_source() is None


@pytest.mark.asyncio
async def test_packed_mask_sources_have_one_plane_at_timepoint_zero(
    data_root: Path,
    tmp_path: Path,
) -> None:
    app = _app(data_root, tmp_path)
    dataset_id = await _link(app, data_root / PACKED / "idr0080-A549" / "14610")
    source = (await open_reader(app.state.context, dataset_id)).mask_source()
    assert source is not None
    field = source.field("a01", 1)
    assert field == MaskField("A01", 1)
    expected = mask_stores.field_masks(B90, 14610, 0, 1, SHAPE).counts
    assert source.counts(B90, field) == source.counts(B90, field, 0) == expected
    plane = source.open_plane(B90, field, "nuclei")
    assert plane.etag("FFD400", 2) == source.open_plane(B90, field, "nuclei", 0).etag("FFD400", 2)
    assert plane.outline("FFD400", 2).startswith(b"\x89PNG\r\n\x1a\n")
    for call in (
        lambda: source.counts(B90, field, 1),
        lambda: source.open_plane(B90, field, "nuclei", 1),
    ):
        with pytest.raises(
            MaskRequestError,
            match="^These masks have a single timepoint: omit t or give 0, not 1$",
        ):
            call()
    with pytest.raises(MaskNotFoundError, match="^Packed field does not exist: Z99 site 1$"):
        source.field("z99", 1)


@pytest.mark.asyncio
async def test_timed_plates_offer_a_mask_source_only_with_a_mask_folder(
    data_root: Path,
    tmp_path: Path,
) -> None:
    for timed_masks, expected in ((False, type(None)), (True, TimedMaskSource)):
        app = _app(data_root, tmp_path / str(timed_masks), timed_masks=timed_masks)
        dataset_id = await _link(app, data_root / "test_plate.zarr")
        reader = await open_reader(app.state.context, dataset_id)
        assert isinstance(reader, TimedCollectionReader)
        assert type(reader.mask_source()) is expected
    native_id = await _link(app, data_root / "test_image_v05.zarr")
    assert (await open_reader(app.state.context, native_id)).mask_source() is None


@pytest.mark.asyncio
async def test_timed_mask_sources_need_a_timepoint_inside_the_field(
    data_root: Path,
    tmp_path: Path,
) -> None:
    app = _app(data_root, tmp_path, timed_masks=True)
    dataset_id = await _link(app, data_root / "test_plate.zarr")
    source = (await open_reader(app.state.context, dataset_id)).mask_source()
    assert source is not None
    field = source.field("a01", 1)
    assert field == MaskField("A01", 1)
    assert [entry.model.tag for entry in source.models(field)] == ["masks_raw"]
    assert source.models(source.field("A01", 2)) == []
    for t in range(make_test_data.TIMEPOINTS):
        assert source.counts("masks_raw", field, t) == {
            "cells": timed_mask_stores.cell_count(0, t)
        }
    first, second = (source.open_plane("masks_raw", field, "cells", t) for t in (0, 1))
    assert first.etag("FFFFFF", 2) != second.etag("FFFFFF", 2)
    with pytest.raises(MaskRequestError, match="give t"):
        source.counts("masks_raw", field)
    with pytest.raises(MaskRequestError, match="outside this field's 6 timepoints"):
        source.open_plane("masks_raw", field, "cells", 6)
