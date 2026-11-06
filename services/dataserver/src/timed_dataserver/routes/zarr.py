"""Zarr resource route: native stores, the TIMED facade and the packed facade."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Header, Response

from ..context import ServerContext, get_context
from ..errors import not_found, translate_path_errors, unprocessable
from ..http_cache import file_response, memory_response
from ..linked import linked, packed_profile
from ..omezarr import OmeZarrError, collection_resource
from ..packed_facade import (
    PackedChunkRequest,
    PackedFacadeNotFoundError,
    PackedMetadataRequest,
    parse_packed_resource,
)
from ..packed_io import PackedReadError
from ..paths import PathForbiddenError

router = APIRouter()


def _refuse_qc_folders(ctx: ServerContext, path: Path) -> None:
    """Never serve a configured QC folder: blind=1 protects only /api/qc."""
    if ctx.qc_private_roots.contains(path):
        raise PathForbiddenError("Configured QC folders are not served")


@router.head(
    "/zarr/{dataset_id}/{resource_path:path}",
    include_in_schema=False,
)
@router.get("/zarr/{dataset_id}/{resource_path:path}")
async def get_zarr_resource(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
    resource_path: str,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    if_range: Annotated[str | None, Header(alias="If-Range")] = None,
) -> Response:
    _record, dataset_path, descriptor, packed = await linked(ctx, dataset_id)
    if packed is not None:
        profile = await packed_profile(ctx, packed)
        try:
            resource = parse_packed_resource(packed, profile, resource_path)
        except PackedFacadeNotFoundError as exc:
            raise not_found(str(exc)) from exc
        if isinstance(resource, PackedMetadataRequest):
            return memory_response(
                resource.content,
                "application/json",
                range_header,
                if_none_match,
                if_range,
            )
        if not isinstance(resource, PackedChunkRequest):
            raise not_found(f"Unknown packed facade resource: {resource_path}")
        try:
            chunk = await anyio.to_thread.run_sync(
                ctx.packed_reader.get_chunk,
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
        return memory_response(
            chunk.content,
            "application/octet-stream",
            range_header,
            if_none_match,
            if_range,
            etag=chunk.etag,
            extra_headers={"X-TIMED-Cache": chunk.cache_status},
        )
    try:
        with translate_path_errors():
            requested = ctx.guard.child(dataset_path, resource_path, must_exist=False)
            # Checked before existence, so a private folder's contents stay unknown.
            _refuse_qc_folders(ctx, requested)
            virtual = (
                collection_resource(dataset_path, resource_path)
                if descriptor.layout == "timed-collection"
                else None
            )
            if virtual is not None and virtual.content is not None:
                return memory_response(
                    virtual.content,
                    virtual.content_type,
                    range_header,
                    if_none_match,
                    if_range,
                )
            mapped_path = (
                virtual.mapped_path
                if virtual is not None and virtual.mapped_path is not None
                else resource_path
            )
            file_path = ctx.guard.child(dataset_path, mapped_path)
            _refuse_qc_folders(ctx, file_path)
    except OmeZarrError as exc:
        raise unprocessable(exc) from exc
    if not file_path.is_file():
        raise not_found(f"Zarr resource does not exist: {resource_path}")
    return file_response(file_path, range_header, if_none_match, if_range)
