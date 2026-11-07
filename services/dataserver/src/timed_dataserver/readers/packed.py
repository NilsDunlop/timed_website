"""Reader for packed plates: the profiled facade, its metadata and its masks.

Decoding and metadata reads keep their worker-thread hops. The mask source
serves plates whose pinned code profile declares masks; its stores hold one
plane per field, at timepoint 0.
"""

from __future__ import annotations

import re

import anyio

from ..context import ServerContext
from ..errors import not_found, unprocessable
from ..linked import LinkedPackedPlate, packed_profile
from ..mask_catalogue import MaskPlateFiles
from ..mask_reader import MaskModelEntry, MaskNotFoundError, MaskReader, MaskTarget, outline_etag
from ..mask_store import MaskTable
from ..models import PlateMetadataResponse, PlateSummary, WellFieldsResponse
from ..packed_facade import (
    PackedChunkRequest,
    PackedFacadeNotFoundError,
    PackedMetadataRequest,
    packed_plate_summary,
    packed_well_fields,
    parse_packed_resource,
)
from ..packed_io import PackedReadError
from ..packed_metadata import PackedMetadataError
from ..packed_profiles import PackedMasks
from .base import MaskField, MemoryResource, ZarrResource

_WELL = re.compile(r"[A-Z]+[0-9]+")


def _single_plane(t: int | None) -> None:
    if t not in (None, 0):
        raise MaskNotFoundError(f"No masks at timepoint {t}")


class PackedMaskPlane:
    """One committed member of a plate store."""

    def __init__(self, masks: MaskReader, target: MaskTarget, shape: tuple[int, int]) -> None:
        self._masks = masks
        self.target = target
        self._shape = shape

    def etag(self, color: str, width: int) -> str:
        return outline_etag(self.target, color, width)

    def outline(self, color: str, width: int) -> bytes:
        return self._masks.outline(self.target, self._shape, color, width)


class PackedMaskSource:
    """The masks of one packed plate, through the shared mask reader."""

    def __init__(
        self,
        masks: MaskReader,
        dataset: LinkedPackedPlate,
        declaration: PackedMasks,
        shape: tuple[int, int],
    ) -> None:
        self._masks = masks
        self._dataset = dataset
        self._declaration = declaration
        self._shape = shape

    def field(self, well: str, site: int) -> MaskField:
        canonical = well.upper()
        dataset = self._dataset
        if (
            _WELL.fullmatch(canonical) is None
            or dataset.packed.catalogue.field(dataset.experiment, dataset.plate, canonical, site)
            is None
        ):
            raise MaskNotFoundError(f"Packed field does not exist: {canonical} site {site}")
        return MaskField(canonical, site)

    def models(self) -> list[MaskModelEntry]:
        dataset = self._dataset
        return self._masks.models(
            dataset.packed.index_path,
            self._declaration,
            dataset.experiment,
            dataset.plate,
        )

    def _plate(self, model_tag: str) -> tuple[MaskPlateFiles, MaskTable]:
        dataset = self._dataset
        return self._masks.plate(
            dataset.packed.index_path,
            self._declaration,
            model_tag,
            dataset.experiment,
            dataset.plate,
        )

    def counts(self, model_tag: str, field: MaskField, t: int | None = None) -> dict[str, int]:
        _single_plane(t)
        files, table = self._plate(model_tag)
        return self._masks.counts(files, table, field.well, field.site, self._shape)

    def open_plane(
        self,
        model_tag: str,
        field: MaskField,
        kind: str,
        t: int | None = None,
    ) -> PackedMaskPlane:
        _single_plane(t)
        files, table = self._plate(model_tag)
        target = self._masks.target(files, table, field.well, field.site, kind)
        return PackedMaskPlane(self._masks, target, self._shape)


class PackedPlateReader:
    """A linked packed plate, served through its pinned or inferred profile."""

    def __init__(self, ctx: ServerContext, dataset: LinkedPackedPlate) -> None:
        self.ctx = ctx
        self.dataset = dataset

    async def summary(self) -> PlateSummary:
        packed = self.dataset.packed
        try:
            return packed_plate_summary(packed, await packed_profile(self.ctx, packed))
        except PackedFacadeNotFoundError as exc:
            raise not_found(str(exc)) from exc

    async def well_fields(self, well_path: str) -> WellFieldsResponse:
        try:
            return packed_well_fields(self.dataset.packed, well_path)
        except PackedFacadeNotFoundError as exc:
            raise not_found(str(exc)) from exc

    async def metadata(self) -> PlateMetadataResponse:
        dataset = self.dataset
        profile = await packed_profile(self.ctx, dataset.packed)
        if profile.metadata_path is None:
            return PlateMetadataResponse(available=False)
        try:
            return await anyio.to_thread.run_sync(
                self.ctx.packed_metadata.for_plate,
                dataset.packed.index_path,
                profile,
                dataset.experiment,
                dataset.plate,
            )
        except PackedMetadataError as exc:
            raise unprocessable(exc) from exc

    async def zarr_resource(self, resource_path: str) -> ZarrResource:
        packed = self.dataset.packed
        profile = await packed_profile(self.ctx, packed)
        try:
            resource = parse_packed_resource(packed, profile, resource_path)
        except PackedFacadeNotFoundError as exc:
            raise not_found(str(exc)) from exc
        if isinstance(resource, PackedMetadataRequest):
            return MemoryResource(resource.content, "application/json")
        if not isinstance(resource, PackedChunkRequest):
            raise not_found(f"Unknown packed facade resource: {resource_path}")
        try:
            chunk = await anyio.to_thread.run_sync(
                self.ctx.packed_reader.get_chunk,
                packed.catalogue,
                profile,
                resource.field.experiment,
                resource.field.plate,
                resource.field.well,
                resource.field.site,
                resource.channel,
                resource.chunk_y,
                resource.chunk_x,
            )
        except PackedReadError as exc:
            raise unprocessable(exc) from exc
        return MemoryResource(
            chunk.content,
            "application/octet-stream",
            etag=chunk.etag,
            extra_headers={"X-TIMED-Cache": chunk.cache_status},
        )

    def mask_source(self) -> PackedMaskSource | None:
        profile = self.ctx.packed_profiles.pinned(self.dataset.packed)
        if profile is None or profile.masks is None:
            return None
        return PackedMaskSource(
            self.ctx.masks,
            self.dataset,
            profile.masks,
            profile.geometry.image_shape,
        )
