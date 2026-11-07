"""Segmentation mask routes: a dataset's models, a field's counts and outline images.

The listing answers every linked dataset, with no models when it has none, so
the viewer can ask without knowing the dataset's kind. The field routes serve
packed plates whose code profile declares masks; anything else answers 404, as
does an unknown model, field, record or kind. A corrupt store answers 409 and
affects only its own model. All mask work runs in worker threads.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Header, Query, Response

from ..context import ServerContext, get_context
from ..errors import conflict, not_found
from ..http_cache import etag_matches, memory_response, not_modified
from ..linked import linked
from ..mask_outlines import MAX_OUTLINE_WIDTH, MIN_OUTLINE_WIDTH
from ..mask_reader import MaskNotFoundError, MaskTarget, outline_etag
from ..mask_store import MaskStoreError
from ..models import MaskFieldResponse, MaskModelsResponse, MaskModelSummary
from ..packed import PackedVirtualPath
from ..packed_profiles import PackedDatasetProfile, PackedMasks

router = APIRouter()

_WELL = re.compile(r"[A-Z]+[0-9]+")
_OUTLINE_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "A 1-bit palette PNG at plane size; index 0 is transparent",
        "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
    },
    304: {"description": "The outline is unchanged"},
}


async def _mask_call[T](work: Callable[[], T]) -> T:
    try:
        return await anyio.to_thread.run_sync(work)
    except MaskNotFoundError as exc:
        raise not_found(str(exc)) from exc
    except MaskStoreError as exc:
        raise conflict(exc) from exc


async def _masked_field(
    ctx: ServerContext,
    dataset_id: str,
    well: str,
    site: int,
) -> tuple[PackedVirtualPath, PackedDatasetProfile, PackedMasks, str]:
    _record, _path, _descriptor, packed = await linked(ctx, dataset_id)
    profile = None if packed is None else ctx.packed_profiles.pinned(packed)
    if packed is None or profile is None or profile.masks is None:
        raise not_found("This dataset has no segmentation masks")
    assert packed.experiment is not None
    assert packed.plate is not None
    canonical = well.upper()
    if (
        _WELL.fullmatch(canonical) is None
        or packed.catalogue.field(packed.experiment, packed.plate, canonical, site) is None
    ):
        raise not_found(f"Packed field does not exist: {canonical} site {site}")
    return packed, profile, profile.masks, canonical


@router.get("/api/datasets/{dataset_id}/masks", response_model=MaskModelsResponse)
async def list_mask_models(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
) -> MaskModelsResponse:
    _record, _path, _descriptor, packed = await linked(ctx, dataset_id)
    profile = None if packed is None else ctx.packed_profiles.pinned(packed)
    if packed is None or profile is None or profile.masks is None:
        return MaskModelsResponse(models=[])
    assert packed.experiment is not None
    assert packed.plate is not None
    entries = await anyio.to_thread.run_sync(
        ctx.masks.models,
        packed.index_path,
        profile.masks,
        packed.experiment,
        packed.plate,
    )
    return MaskModelsResponse(
        models=[
            MaskModelSummary(
                model_tag=entry.model.tag,
                recipe=entry.model.recipe,
                kinds=list(entry.model.kinds),
                frozen=entry.model.frozen,
                created_at=entry.model.created_at,
                available=entry.available,
                default=entry.default,
            )
            for entry in entries
        ]
    )


@router.get(
    "/api/datasets/{dataset_id}/masks/{model}/{well}/{site}",
    response_model=MaskFieldResponse,
)
async def mask_field_counts(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
    model: str,
    well: str,
    site: int,
) -> MaskFieldResponse:
    packed, profile, masks, canonical = await _masked_field(ctx, dataset_id, well, site)
    assert packed.experiment is not None
    assert packed.plate is not None
    experiment, plate = packed.experiment, packed.plate

    def read() -> dict[str, int]:
        files, table = ctx.masks.plate(packed.index_path, masks, model, experiment, plate)
        return ctx.masks.counts(files, table, canonical, site, profile.geometry.image_shape)

    counts = await _mask_call(read)
    return MaskFieldResponse(model_tag=model, well=canonical, site=site, counts=counts)


@router.get(
    "/api/datasets/{dataset_id}/masks/{model}/{well}/{site}/{kind}.png",
    response_class=Response,
    responses=_OUTLINE_RESPONSES,
)
async def mask_outline(
    ctx: Annotated[ServerContext, Depends(get_context)],
    dataset_id: str,
    model: str,
    well: str,
    site: int,
    kind: str,
    color: Annotated[str, Query(pattern=r"^[0-9A-Fa-f]{6}$")] = "FFFFFF",
    width: Annotated[int, Query(ge=MIN_OUTLINE_WIDTH, le=MAX_OUTLINE_WIDTH)] = 2,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    if_range: Annotated[str | None, Header(alias="If-Range")] = None,
) -> Response:
    packed, profile, masks, canonical = await _masked_field(ctx, dataset_id, well, site)
    assert packed.experiment is not None
    assert packed.plate is not None
    experiment, plate = packed.experiment, packed.plate
    shape = profile.geometry.image_shape
    rgb = color.upper()

    def resolve() -> MaskTarget:
        files, table = ctx.masks.plate(packed.index_path, masks, model, experiment, plate)
        return ctx.masks.target(files, table, canonical, site, kind)

    target = await _mask_call(resolve)
    etag = outline_etag(target, rgb, width)
    if etag_matches(if_none_match, etag):
        return not_modified(etag)
    content = await _mask_call(lambda: ctx.masks.outline(target, shape, rgb, width))
    return memory_response(
        content,
        "image/png",
        range_header,
        if_none_match,
        if_range,
        etag=etag,
    )
