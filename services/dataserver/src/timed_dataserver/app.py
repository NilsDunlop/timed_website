"""FastAPI application for authenticated, read-only OME-Zarr access."""

from __future__ import annotations

import hashlib
import mimetypes
import platform
import re
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from . import API_VERSION, __version__
from .auth import bearer_auth
from .models import (
    Capabilities,
    DatasetLinkRequest,
    DatasetListResponse,
    DatasetRecord,
    DatasetSummary,
    FilesystemEntry,
    FilesystemListResponse,
    HandshakeResponse,
    PlateMetadataResponse,
    WellFieldsResponse,
)
from .omezarr import (
    OmeZarrError,
    StoreDescriptor,
    collection_resource,
    dataset_summary,
    inspect_store,
    well_fields,
)
from .paths import PathForbiddenError, PathNotFoundError, RootGuard
from .plate_metadata import PlateMetadataCatalog, PlateMetadataError
from .registry import DatasetRegistry

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
STREAM_BLOCK_SIZE = 1024 * 1024
PRIVATE_CACHE_CONTROL = "private, max-age=3600"


@dataclass(frozen=True)
class ServerSettings:
    """Runtime paths and environment advertised by one server process."""

    data_roots: tuple[Path, ...]
    state_dir: Path
    environment: Literal["local", "hpc"]
    metadata_root: Path | None = None


def _forbidden(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


def _descriptor_or_404(path: Path) -> StoreDescriptor:
    descriptor = inspect_store(path)
    if descriptor is None:
        raise _not_found("Linked path is no longer a recognized OME-Zarr dataset")
    return descriptor


def _range_bounds(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    match = RANGE_PATTERN.fullmatch(value.strip())
    if match is None or size == 0:
        raise ValueError("Invalid byte range")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("Invalid byte range")
    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        if start >= size or end < start:
            raise ValueError("Unsatisfiable byte range")
        return start, min(end, size - 1)
    suffix_length = int(end_text)
    if suffix_length <= 0:
        raise ValueError("Invalid byte range")
    return max(0, size - suffix_length), size - 1


def _cache_headers(etag: str, *, modified: float | None = None) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": PRIVATE_CACHE_CONTROL,
        "ETag": etag,
    }
    if modified is not None:
        headers["Last-Modified"] = formatdate(modified, usegmt=True)
    return headers


def _etag_matches(value: str | None, etag: str) -> bool:
    if value is None:
        return False
    expected = etag.removeprefix("W/")
    return any(
        candidate == "*" or candidate.removeprefix("W/") == expected
        for candidate in (part.strip() for part in value.split(","))
    )


def _range_for_validator(
    range_header: str | None,
    if_range: str | None,
    etag: str,
    *,
    modified: float | None = None,
) -> str | None:
    if range_header is None or if_range is None:
        return range_header
    validator = if_range.strip()
    if validator.startswith('"') or validator.startswith("W/"):
        return range_header if not validator.startswith("W/") and validator == etag else None
    if modified is None:
        return None
    try:
        parsed = parsedate_to_datetime(validator)
        validator_time = (
            parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        ).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    return range_header if int(modified) <= int(validator_time) else None


def _memory_response(
    content: bytes,
    content_type: str,
    range_header: str | None,
    if_none_match: str | None,
    if_range: str | None,
) -> Response:
    size = len(content)
    digest = hashlib.blake2s(content, digest_size=16).hexdigest()
    etag = f'"{digest}"'
    headers = _cache_headers(etag)
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    range_header = _range_for_validator(range_header, if_range, etag)
    try:
        bounds = _range_bounds(range_header, size)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail=str(exc),
            headers={**headers, "Content-Range": f"bytes */{size}"},
        ) from exc
    if bounds is None:
        headers["Content-Length"] = str(size)
        return Response(content, media_type=content_type, headers=headers)
    start, end = bounds
    body = content[start : end + 1]
    headers.update(
        {
            "Content-Length": str(len(body)),
            "Content-Range": f"bytes {start}-{end}/{size}",
        }
    )
    return Response(
        body,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=content_type,
        headers=headers,
    )


def _file_blocks(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining:
            block = handle.read(min(STREAM_BLOCK_SIZE, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def _file_response(
    path: Path,
    range_header: str | None,
    if_none_match: str | None,
    if_range: str | None,
) -> Response:
    metadata = path.stat()
    size = metadata.st_size
    etag = f'"{metadata.st_mtime_ns:x}-{size:x}"'
    headers = _cache_headers(etag, modified=metadata.st_mtime)
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    range_header = _range_for_validator(
        range_header,
        if_range,
        etag,
        modified=metadata.st_mtime,
    )
    try:
        bounds = _range_bounds(range_header, size)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail=str(exc),
            headers={**headers, "Content-Range": f"bytes */{size}"},
        ) from exc
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if bounds is None:
        start, end = 0, size - 1
        response_status = status.HTTP_200_OK
    else:
        start, end = bounds
        response_status = status.HTTP_206_PARTIAL_CONTENT
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)
    if bounds is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _file_blocks(path, start, length),
        status_code=response_status,
        media_type=content_type,
        headers=headers,
    )


def create_app(settings: ServerSettings, token: str) -> FastAPI:
    """Create one isolated data-server application."""
    guard = RootGuard(settings.data_roots)
    registry = DatasetRegistry(settings.state_dir)
    metadata_catalog = (
        PlateMetadataCatalog(settings.metadata_root) if settings.metadata_root is not None else None
    )
    require_token = bearer_auth(token)
    app = FastAPI(
        title="TIMED data server",
        version=__version__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        dependencies=[Depends(require_token)],
    )
    app.state.settings = settings
    app.state.guard = guard
    app.state.registry = registry
    app.state.metadata_catalog = metadata_catalog

    def linked(dataset_id: str) -> tuple[DatasetRecord, Path, StoreDescriptor]:
        record = registry.get(dataset_id)
        if record is None:
            raise _not_found("Dataset is not linked")
        try:
            path = guard.resolve(record.path)
        except PathForbiddenError as exc:
            raise _forbidden(exc) from exc
        except PathNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        return record, path, _descriptor_or_404(path)

    @app.get("/api/handshake", response_model=HandshakeResponse)
    async def handshake() -> HandshakeResponse:
        return HandshakeResponse(
            api_version=API_VERSION,
            server_version=__version__,
            environment=settings.environment,
            hostname=socket.gethostname(),
            data_roots=[str(root) for root in guard.roots],
            python_version=platform.python_version(),
            capabilities=Capabilities(plate_metadata=metadata_catalog is not None),
        )

    @app.get("/api/fs/list", response_model=FilesystemListResponse)
    async def list_files(path: Annotated[str | None, Query()] = None) -> FilesystemListResponse:
        if path is None or path == "":
            return FilesystemListResponse(
                path=None,
                entries=[
                    FilesystemEntry(
                        name=root.name or str(root),
                        path=str(root),
                        kind="dir",
                        ome_zarr=(descriptor.info if (descriptor := inspect_store(root)) else None),
                    )
                    for root in guard.roots
                ],
            )
        try:
            target = guard.resolve(path)
            root = guard.root_for(target)
        except PathForbiddenError as exc:
            raise _forbidden(exc) from exc
        except PathNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Only directories can be listed")

        current = target
        while True:
            if inspect_store(current) is not None:
                raise HTTPException(
                    status_code=400, detail="The inside of a Zarr store is not listable"
                )
            if current == root:
                break
            current = current.parent

        entries: list[FilesystemEntry] = []
        try:
            children = sorted(target.iterdir(), key=lambda child: child.name.casefold())
        except OSError as exc:
            raise HTTPException(status_code=403, detail=f"Could not list directory: {exc}") from exc
        for child in children:
            try:
                safe_child = guard.resolve(str(child))
            except (PathForbiddenError, PathNotFoundError):
                continue
            is_directory = safe_child.is_dir()
            descriptor = inspect_store(safe_child) if is_directory else None
            entries.append(
                FilesystemEntry(
                    name=child.name,
                    path=str(safe_child),
                    kind="dir" if is_directory else "file",
                    ome_zarr=descriptor.info if descriptor else None,
                )
            )
        return FilesystemListResponse(path=str(target), entries=entries)

    @app.get("/api/datasets", response_model=DatasetListResponse)
    async def list_datasets() -> DatasetListResponse:
        return DatasetListResponse(datasets=registry.list())

    @app.post("/api/datasets", response_model=DatasetRecord, status_code=201)
    async def link_dataset(request: DatasetLinkRequest) -> DatasetRecord:
        try:
            path = guard.resolve(request.path)
        except PathForbiddenError as exc:
            raise _forbidden(exc) from exc
        except PathNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        descriptor = inspect_store(path)
        if descriptor is None:
            raise HTTPException(status_code=422, detail="Path is not a recognized OME-Zarr store")
        if descriptor.info.kind == "well":
            raise HTTPException(
                status_code=422, detail="Link a plate or image, not an individual well"
            )
        return registry.link(path, request.name or path.name, descriptor.info)

    @app.delete("/api/datasets/{dataset_id}", status_code=204, response_class=Response)
    async def unlink_dataset(dataset_id: str) -> Response:
        if not registry.unlink(dataset_id):
            raise _not_found("Dataset is not linked")
        return Response(status_code=204)

    @app.get("/api/datasets/{dataset_id}/summary", response_model=DatasetSummary)
    async def summarize_dataset(dataset_id: str) -> DatasetSummary:
        _record, path, descriptor = linked(dataset_id)
        try:
            return dataset_summary(path, descriptor)
        except PathForbiddenError as exc:
            raise _forbidden(exc) from exc
        except OmeZarrError as exc:
            raise _unprocessable(exc) from exc

    @app.get(
        "/api/datasets/{dataset_id}/wells/{well_path:path}",
        response_model=WellFieldsResponse,
    )
    async def summarize_well(dataset_id: str, well_path: str) -> WellFieldsResponse:
        _record, path, descriptor = linked(dataset_id)
        if descriptor.info.kind != "plate":
            raise HTTPException(status_code=422, detail="Dataset is not a plate")
        try:
            return well_fields(path, descriptor, well_path)
        except PathForbiddenError as exc:
            raise _forbidden(exc) from exc
        except OmeZarrError as exc:
            raise _not_found(str(exc)) from exc

    @app.get(
        "/api/datasets/{dataset_id}/metadata",
        response_model=PlateMetadataResponse,
    )
    async def dataset_metadata(dataset_id: str) -> PlateMetadataResponse:
        _record, path, descriptor = linked(dataset_id)
        if descriptor.info.kind != "plate":
            return PlateMetadataResponse(available=False)
        if metadata_catalog is None:
            return PlateMetadataResponse(available=False)
        try:
            return metadata_catalog.for_dataset(path.name)
        except PlateMetadataError as exc:
            raise _unprocessable(exc) from exc

    @app.head(
        "/zarr/{dataset_id}/{resource_path:path}",
        include_in_schema=False,
    )
    @app.get("/zarr/{dataset_id}/{resource_path:path}")
    async def get_zarr_resource(
        dataset_id: str,
        resource_path: str,
        range_header: Annotated[str | None, Header(alias="Range")] = None,
        if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
        if_range: Annotated[str | None, Header(alias="If-Range")] = None,
    ) -> Response:
        _record, dataset_path, descriptor = linked(dataset_id)
        try:
            guard.child(dataset_path, resource_path, must_exist=False)
            virtual = (
                collection_resource(dataset_path, resource_path)
                if descriptor.layout == "timed-collection"
                else None
            )
            if virtual is not None and virtual.content is not None:
                return _memory_response(
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
            file_path = guard.child(dataset_path, mapped_path)
        except PathForbiddenError as exc:
            raise _forbidden(exc) from exc
        except PathNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        if not file_path.is_file():
            raise _not_found(f"Zarr resource does not exist: {resource_path}")
        return _file_response(file_path, range_header, if_none_match, if_range)

    return app
