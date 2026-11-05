"""Packed paths, linked-dataset lookup and display names shared by the routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from .context import ServerContext
from .errors import not_found, translate_path_errors, unprocessable
from .models import DatasetRecord, OmeZarrInfo
from .omezarr import StoreDescriptor, inspect_store
from .packed import PackedIndexError, PackedVirtualPath, resolve_packed_virtual_path_in_thread
from .packed_profiles import PackedDatasetProfile, PackedProfileError

PACKED_PLATE_DESCRIPTOR = StoreDescriptor(
    info=OmeZarrInfo(kind="plate", version="0.5", zarr_format=3),
    layout="packed",
)


def _descriptor_or_404(path: Path) -> StoreDescriptor:
    descriptor = inspect_store(path)
    if descriptor is None:
        raise not_found("Linked path is no longer a recognized OME-Zarr dataset")
    return descriptor


async def packed_profile(ctx: ServerContext, packed: PackedVirtualPath) -> PackedDatasetProfile:
    try:
        return await ctx.packed_profiles.resolve(packed)
    except PackedProfileError as exc:
        raise unprocessable(exc) from exc


async def packed_path(ctx: ServerContext, raw: str) -> PackedVirtualPath | None:
    try:
        with translate_path_errors():
            return await resolve_packed_virtual_path_in_thread(
                raw,
                ctx.guard,
                cache=ctx.packed_catalogues,
            )
    except PackedIndexError as exc:
        raise unprocessable(exc) from exc


async def displayed_dataset(ctx: ServerContext, record: DatasetRecord) -> DatasetRecord:
    try:
        packed = await packed_path(ctx, record.path)
    except HTTPException:
        return record
    if packed is None or packed.experiment is None or packed.plate is None:
        return record
    profile = ctx.packed_profiles.pinned(packed)
    if profile is None or profile.display_name is None:
        return record
    return record.model_copy(
        update={
            "name": profile.plate_display_name(
                packed.experiment,
                packed.plate,
            )
        }
    )


async def linked(
    ctx: ServerContext,
    dataset_id: str,
) -> tuple[DatasetRecord, Path, StoreDescriptor, PackedVirtualPath | None]:
    record = ctx.registry.get(dataset_id)
    if record is None:
        raise not_found("Dataset is not linked")
    packed = await packed_path(ctx, record.path)
    if packed is not None:
        if packed.plate is None:
            raise not_found("Packed plate is no longer available")
        return record, packed.index_path, PACKED_PLATE_DESCRIPTOR, packed
    with translate_path_errors():
        path = ctx.guard.resolve(record.path)
    return record, path, _descriptor_or_404(path), None
