"""Reader for native OME-Zarr stores, and the filesystem-store logic TIMED shares.

Everything here runs on the event loop, as it did inside the routes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from ..context import ServerContext
from ..errors import forbidden, not_found, translate_path_errors, unprocessable
from ..linked import LinkedStore
from ..models import ImageSummary, PlateMetadataResponse, PlateSummary, WellFieldsResponse
from ..omezarr import OmeZarrError, VirtualResource, dataset_summary, well_fields
from ..paths import PathForbiddenError
from ..plate_metadata import PlateMetadataError
from .base import FileResource, MaskSource, MemoryResource, ZarrResource


def _refuse_qc_folders(ctx: ServerContext, path: Path) -> None:
    """Never serve a configured QC folder: blind=1 protects only /api/qc."""
    if ctx.qc_private_roots.contains(path):
        raise PathForbiddenError("Configured QC folders are not served")


class NativeStoreReader:
    """A linked filesystem store; native stores serve their files unchanged."""

    def __init__(self, ctx: ServerContext, dataset: LinkedStore) -> None:
        self.ctx = ctx
        self.dataset = dataset

    async def summary(self) -> ImageSummary | PlateSummary:
        try:
            return dataset_summary(self.dataset.path, self.dataset.descriptor)
        except PathForbiddenError as exc:
            raise forbidden(exc) from exc
        except OmeZarrError as exc:
            raise unprocessable(exc) from exc

    async def well_fields(self, well_path: str) -> WellFieldsResponse:
        if self.dataset.descriptor.info.kind != "plate":
            raise HTTPException(status_code=422, detail="Dataset is not a plate")
        try:
            return well_fields(self.dataset.path, self.dataset.descriptor, well_path)
        except PathForbiddenError as exc:
            raise forbidden(exc) from exc
        except OmeZarrError as exc:
            raise not_found(str(exc)) from exc

    async def metadata(self) -> PlateMetadataResponse:
        if self.dataset.descriptor.info.kind != "plate":
            return PlateMetadataResponse(available=False)
        if self.ctx.metadata_catalog is None:
            return PlateMetadataResponse(available=False)
        try:
            return self.ctx.metadata_catalog.for_dataset(self.dataset.path.name)
        except PlateMetadataError as exc:
            raise unprocessable(exc) from exc

    def _virtual_resource(self, resource_path: str) -> VirtualResource | None:
        """A facade's virtual metadata or mapped path; native stores have none."""
        return None

    async def zarr_resource(self, resource_path: str) -> ZarrResource:
        dataset_path = self.dataset.path
        try:
            with translate_path_errors():
                requested = self.ctx.guard.child(dataset_path, resource_path, must_exist=False)
                # Checked before existence, so a private folder's contents stay unknown.
                _refuse_qc_folders(self.ctx, requested)
                virtual = self._virtual_resource(resource_path)
                if virtual is not None and virtual.content is not None:
                    return MemoryResource(virtual.content, virtual.content_type)
                mapped_path = (
                    virtual.mapped_path
                    if virtual is not None and virtual.mapped_path is not None
                    else resource_path
                )
                file_path = self.ctx.guard.child(dataset_path, mapped_path)
                _refuse_qc_folders(self.ctx, file_path)
        except OmeZarrError as exc:
            raise unprocessable(exc) from exc
        if not file_path.is_file():
            raise not_found(f"Zarr resource does not exist: {resource_path}")
        return FileResource(file_path)

    def mask_source(self) -> MaskSource | None:
        return None
