"""Zarr resource route: native stores, the TIMED facade and the packed facade."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from ..context import ServerContext, get_context
from ..http_cache import file_response, memory_response
from ..readers import open_reader
from ..readers.base import FileResource

router = APIRouter()


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
    resource = await (await open_reader(ctx, dataset_id)).zarr_resource(resource_path)
    if isinstance(resource, FileResource):
        return file_response(resource.path, range_header, if_none_match, if_range)
    return memory_response(
        resource.content,
        resource.content_type,
        range_header,
        if_none_match,
        if_range,
        etag=resource.etag,
        extra_headers=resource.extra_headers,
    )
