"""Dataset routes: link, unlink, list, and per-dataset summary, wells, metadata and matches."""

from __future__ import annotations

from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..context import ServerContext, get_context
from ..errors import forbidden, not_found, translate_path_errors, unprocessable
from ..linked import PACKED_PLATE_DESCRIPTOR, displayed_dataset, linked, packed_path, packed_profile
from ..models import (
    DatasetLinkRequest,
    DatasetListResponse,
    DatasetMatchesResponse,
    DatasetRecord,
    DatasetSummary,
    MatchMode,
    PlateMetadataResponse,
    WellFieldsResponse,
)
from ..omezarr import OmeZarrError, dataset_summary, inspect_store, well_fields
from ..packed_facade import PackedFacadeNotFoundError, packed_plate_summary, packed_well_fields
from ..packed_metadata import PackedMetadataError, PackedMetadataNotFoundError
from ..paths import PathForbiddenError
from ..plate_metadata import PlateMetadataError

router = APIRouter()

QC_FOLDERS_NOT_LINKABLE = "Configured QC folders cannot be linked"


@router.get("/api/datasets", response_model=DatasetListResponse)
async def list_datasets(ctx: Annotated[ServerContext, Depends(get_context)]) -> DatasetListResponse:
    datasets = [
        await displayed_dataset(ctx, record)
        for record in ctx.registry.list()
    ]
    return DatasetListResponse(datasets=datasets)


@router.post("/api/datasets", response_model=DatasetRecord, status_code=201)
async def link_dataset(
    ctx: Annotated[ServerContext, Depends(get_context)],
    request: DatasetLinkRequest,
) -> DatasetRecord:
    # Configured QC folders stay private: blind=1 protects only /api/qc.
    if ctx.qc_private_roots.contains_raw(request.path):
        raise forbidden(PathForbiddenError(QC_FOLDERS_NOT_LINKABLE))
    packed = await packed_path(ctx, request.path)
    if packed is not None:
        if packed.plate is None:
            raise HTTPException(
                status_code=422,
                detail="Link a packed plate, not an index or experiment",
            )
        profile = await packed_profile(ctx, packed)
        record = ctx.registry.link_key(
            packed.registry_key,
            request.name
            or profile.plate_display_name(packed.experiment, packed.plate),
            PACKED_PLATE_DESCRIPTOR.info,
        )
        return await displayed_dataset(ctx, record)
    with translate_path_errors():
        path = ctx.guard.resolve(request.path)
    if ctx.qc_private_roots.contains(path):
        raise forbidden(PathForbiddenError(QC_FOLDERS_NOT_LINKABLE))
    descriptor = inspect_store(path)
    if descriptor is None:
        raise HTTPException(status_code=422, detail="Path is not a recognized OME-Zarr store")
    if descriptor.info.kind == "well":
        raise HTTPException(
            status_code=422, detail="Link a plate or image, not an individual well"
        )
    return ctx.registry.link(path, request.name or path.name, descriptor.info)


@router.delete("/api/datasets/{dataset_id}", status_code=204, response_class=Response)
async def unlink_dataset(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
) -> Response:
    if not ctx.registry.unlink(dataset_id):
        raise not_found("Dataset is not linked")
    return Response(status_code=204)


@router.get("/api/datasets/{dataset_id}/summary", response_model=DatasetSummary)
async def summarize_dataset(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
) -> DatasetSummary:
    _record, path, descriptor, packed = await linked(ctx, dataset_id)
    if packed is not None:
        try:
            return packed_plate_summary(packed, await packed_profile(ctx, packed))
        except PackedFacadeNotFoundError as exc:
            raise not_found(str(exc)) from exc
    try:
        return dataset_summary(path, descriptor)
    except PathForbiddenError as exc:
        raise forbidden(exc) from exc
    except OmeZarrError as exc:
        raise unprocessable(exc) from exc


@router.get(
    "/api/datasets/{dataset_id}/wells/{well_path:path}",
    response_model=WellFieldsResponse,
)
async def summarize_well(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
    well_path: str,
) -> WellFieldsResponse:
    _record, path, descriptor, packed = await linked(ctx, dataset_id)
    if packed is not None:
        try:
            return packed_well_fields(packed, well_path)
        except PackedFacadeNotFoundError as exc:
            raise not_found(str(exc)) from exc
    if descriptor.info.kind != "plate":
        raise HTTPException(status_code=422, detail="Dataset is not a plate")
    try:
        return well_fields(path, descriptor, well_path)
    except PathForbiddenError as exc:
        raise forbidden(exc) from exc
    except OmeZarrError as exc:
        raise not_found(str(exc)) from exc


@router.get(
    "/api/datasets/{dataset_id}/metadata",
    response_model=PlateMetadataResponse,
)
async def dataset_metadata(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
) -> PlateMetadataResponse:
    _record, path, descriptor, packed = await linked(ctx, dataset_id)
    if packed is not None:
        profile = await packed_profile(ctx, packed)
        if profile.metadata_path is None:
            return PlateMetadataResponse(available=False)
        try:
            assert packed.experiment is not None
            assert packed.plate is not None
            return await anyio.to_thread.run_sync(
                ctx.packed_metadata.for_plate,
                packed.index_path,
                profile,
                packed.experiment,
                packed.plate,
            )
        except PackedMetadataError as exc:
            raise unprocessable(exc) from exc
    if descriptor.info.kind != "plate":
        return PlateMetadataResponse(available=False)
    if ctx.metadata_catalog is None:
        return PlateMetadataResponse(available=False)
    try:
        return ctx.metadata_catalog.for_dataset(path.name)
    except PlateMetadataError as exc:
        raise unprocessable(exc) from exc


@router.get(
    "/api/datasets/{dataset_id}/matches",
    response_model=DatasetMatchesResponse,
)
async def dataset_matches(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
    well: Annotated[
        str,
        Query(min_length=2, max_length=16, pattern=r"^[A-Za-z]+[0-9]+$"),
    ],
    by: Annotated[MatchMode, Query()],
) -> DatasetMatchesResponse:
    _record, _path, _descriptor, packed = await linked(ctx, dataset_id)
    if packed is None:
        raise not_found("Dataset matches are available only for packed plates")
    profile = await packed_profile(ctx, packed)
    try:
        assert packed.experiment is not None
        assert packed.plate is not None
        return await anyio.to_thread.run_sync(
            ctx.packed_metadata.matches,
            packed.catalogue,
            profile,
            packed.experiment,
            packed.plate,
            well.upper(),
            by,
        )
    except PackedMetadataNotFoundError as exc:
        raise not_found(str(exc)) from exc
    except PackedMetadataError as exc:
        raise unprocessable(exc) from exc
