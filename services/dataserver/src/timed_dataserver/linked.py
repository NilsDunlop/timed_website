"""Packed paths, typed linked-dataset lookup and display names shared by the routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from .context import ServerContext
from .errors import not_found, translate_path_errors, unprocessable
from .models import DatasetRecord, OmeZarrInfo
from .omezarr import StoreDescriptor, inspect_store
from .packed import PackedIndexError, PackedVirtualPath, resolve_packed_virtual_path_in_thread
from .packed_profiles import PackedDatasetProfile, PackedProfileError

PACKED_PLATE_INFO = OmeZarrInfo(kind="plate", version="0.5", zarr_format=3)


@dataclass(frozen=True, slots=True)
class LinkedPackedPlate:
    """A linked plate inside a packed index's virtual hierarchy."""

    record: DatasetRecord
    packed: PackedVirtualPath
    experiment: str
    plate: int


@dataclass(frozen=True, slots=True)
class LinkedStore:
    """A linked native OME-Zarr store or TIMED collection on the filesystem."""

    record: DatasetRecord
    path: Path
    descriptor: StoreDescriptor


LinkedDataset = LinkedPackedPlate | LinkedStore


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


async def linked(ctx: ServerContext, dataset_id: str) -> LinkedDataset:
    """Resolve a linked record: packed paths first, then the filesystem store."""
    record = ctx.registry.get(dataset_id)
    if record is None:
        raise not_found("Dataset is not linked")
    packed = await packed_path(ctx, record.path)
    if packed is not None:
        # A packed plate path always names its experiment as well.
        if packed.plate is None or packed.experiment is None:
            raise not_found("Packed plate is no longer available")
        return LinkedPackedPlate(record, packed, packed.experiment, packed.plate)
    with translate_path_errors():
        path = ctx.guard.resolve(record.path)
    return LinkedStore(record, path, _descriptor_or_404(path))


async def require_packed_plate(
    ctx: ServerContext,
    dataset_id: str,
    detail: str,
) -> LinkedPackedPlate:
    """The linked packed plate, or 404 with *detail* for any other kind."""
    dataset = await linked(ctx, dataset_id)
    if not isinstance(dataset, LinkedPackedPlate):
        raise not_found(detail)
    return dataset
