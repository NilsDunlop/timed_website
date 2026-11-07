"""Dataset routes: link, unlink, list, and per-dataset summary, wells, metadata and matches."""

from __future__ import annotations

from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..context import ServerContext, get_context
from ..errors import forbidden, not_found, translate_path_errors, unprocessable
from ..linked import (
    PACKED_PLATE_INFO,
    displayed_dataset,
    packed_path,
    packed_profile,
    require_packed_plate,
)
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
from ..omezarr import inspect_store
from ..packed_metadata import PackedMetadataError, PackedMetadataNotFoundError
from ..paths import PathForbiddenError
from ..readers import open_reader

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
            PACKED_PLATE_INFO,
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
    return await (await open_reader(ctx, dataset_id)).summary()


@router.get(
    "/api/datasets/{dataset_id}/wells/{well_path:path}",
    response_model=WellFieldsResponse,
)
async def summarize_well(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
    well_path: str,
) -> WellFieldsResponse:
    return await (await open_reader(ctx, dataset_id)).well_fields(well_path)


@router.get(
    "/api/datasets/{dataset_id}/metadata",
    response_model=PlateMetadataResponse,
)
async def dataset_metadata(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
) -> PlateMetadataResponse:
    return await (await open_reader(ctx, dataset_id)).metadata()


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
    dataset = await require_packed_plate(
        ctx,
        dataset_id,
        "Dataset matches are available only for packed plates",
    )
    profile = await packed_profile(ctx, dataset.packed)
    try:
        return await anyio.to_thread.run_sync(
            ctx.packed_metadata.matches,
            dataset.packed.catalogue,
            profile,
            dataset.experiment,
            dataset.plate,
            well.upper(),
            by,
        )
    except PackedMetadataNotFoundError as exc:
        raise not_found(str(exc)) from exc
    except PackedMetadataError as exc:
        raise unprocessable(exc) from exc
