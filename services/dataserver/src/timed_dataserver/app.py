"""FastAPI application for authenticated, read-only OME-Zarr access."""

from __future__ import annotations

import hashlib
import mimetypes
import platform
import re
import socket
import threading
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Annotated, Literal

import anyio
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from . import API_VERSION, __version__
from .auth import bearer_auth
from .models import (
    Capabilities,
    DatasetLinkRequest,
    DatasetListResponse,
    DatasetMatchesResponse,
    DatasetRecord,
    DatasetSummary,
    FilesystemEntry,
    FilesystemListResponse,
    HandshakeResponse,
    MatchMode,
    OmeZarrInfo,
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
from .packed import (
    PackedCatalogue,
    PackedCatalogueCache,
    PackedIndexError,
    PackedVirtualPath,
    get_catalogue_in_thread,
    is_packed_index,
    resolve_packed_virtual_path_in_thread,
)
from .packed_facade import (
    PackedChunkRequest,
    PackedFacadeNotFoundError,
    PackedMetadataRequest,
    packed_plate_summary,
    packed_well_fields,
    parse_packed_resource,
)
from .packed_io import PackedReader, PackedReadError
from .packed_metadata import (
    PackedMetadataCache,
    PackedMetadataError,
    PackedMetadataNotFoundError,
)
from .packed_profiles import (
    PackedDatasetProfile,
    PackedProbeSignature,
    PackedProfileError,
    packed_probe_signature,
    profile_for_catalogue,
    profile_for_index,
)
from .paths import PathForbiddenError, PathNotFoundError, RootGuard
from .plate_metadata import PlateMetadataCatalog, PlateMetadataError
from .registry import DatasetRegistry

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
STREAM_BLOCK_SIZE = 1024 * 1024
PRIVATE_CACHE_CONTROL = "private, max-age=3600"
PRIVATE_RANGE_CACHE_CONTROL = "private, no-store"
PACKED_PLATE_DESCRIPTOR = StoreDescriptor(
    info=OmeZarrInfo(kind="plate", version="0.5", zarr_format=3),
    layout="packed",
)


@dataclass(frozen=True)
class ServerSettings:
    """Runtime paths and environment advertised by one server process."""

    data_roots: tuple[Path, ...]
    state_dir: Path
    environment: Literal["local", "hpc"]
    metadata_root: Path | None = None
    packed_cache_mb: int = 1024


def _forbidden(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _quantity(count: int, singular: str) -> str:
    return f"{count} {singular if count == 1 else singular + 's'}"


def _packed_index_detail(catalogue: PackedCatalogue) -> str:
    return (
        f"{_quantity(len(catalogue.experiments), 'cell line')} · "
        f"{_quantity(len(catalogue.plates), 'plate')}"
    )


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
    *,
    etag: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    size = len(content)
    if etag is None:
        digest = hashlib.blake2s(content, digest_size=16).hexdigest()
        etag = f'"{digest}"'
    headers = _cache_headers(etag)
    if extra_headers is not None:
        headers.update(extra_headers)
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    range_header = _range_for_validator(range_header, if_range, etag)
    try:
        bounds = _range_bounds(range_header, size)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail=str(exc),
            headers={**headers, "Content-Range": f"bytes */{size}"},
        ) from exc
    if bounds is None:
        headers["Content-Length"] = str(size)
        return Response(content, media_type=content_type, headers=headers)
    start, end = bounds
    body = content[start : end + 1]
    headers["Cache-Control"] = PRIVATE_RANGE_CACHE_CONTROL
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
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
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
        headers["Cache-Control"] = PRIVATE_RANGE_CACHE_CONTROL
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _file_blocks(path, start, length),
        status_code=response_status,
        media_type=content_type,
        headers=headers,
    )


def create_app(settings: ServerSettings, token: str) -> FastAPI:
    """Create one isolated data-server application."""
    if settings.packed_cache_mb <= 0:
        raise ValueError("Packed cache size must be greater than zero")
    guard = RootGuard(settings.data_roots)
    registry = DatasetRegistry(settings.state_dir)
    packed_catalogues = PackedCatalogueCache()
    packed_reader = PackedReader(cache_bytes=settings.packed_cache_mb * 1024 * 1024)
    packed_metadata = PackedMetadataCache()
    generic_profiles: dict[
        tuple[str, int, int, PackedProbeSignature], PackedDatasetProfile
    ] = {}
    generic_profile_loads: dict[
        tuple[str, int, int, PackedProbeSignature],
        Future[PackedDatasetProfile | None],
    ] = {}
    generic_profiles_lock = threading.Lock()
    metadata_catalog = (
        PlateMetadataCatalog(settings.metadata_root) if settings.metadata_root is not None else None
    )
    require_token = bearer_auth(token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await anyio.to_thread.run_sync(packed_reader.close)

    app = FastAPI(
        title="TIMED data server",
        version=__version__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        dependencies=[Depends(require_token)],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.guard = guard
    app.state.registry = registry
    app.state.metadata_catalog = metadata_catalog
    app.state.packed_catalogues = packed_catalogues
    app.state.packed_reader = packed_reader
    app.state.packed_metadata = packed_metadata

    def infer_packed_profile(packed: PackedVirtualPath) -> PackedDatasetProfile:
        index_signature = packed.catalogue.signature
        for _attempt in range(3):
            probe_signature = packed_probe_signature(packed.catalogue)
            key = (
                str(packed.index_path),
                index_signature.mtime_ns,
                index_signature.size,
                probe_signature,
            )
            with generic_profiles_lock:
                cached = generic_profiles.get(key)
                if cached is not None:
                    return cached
                load = generic_profile_loads.get(key)
                is_loader = load is None
                if load is None:
                    load = Future()
                    generic_profile_loads[key] = load
            if not is_loader:
                profile = load.result()
                if profile is None:
                    continue
                return profile

            try:
                profile = profile_for_catalogue(packed.catalogue)
                if packed_probe_signature(packed.catalogue) != probe_signature:
                    load.set_result(None)
                    continue
                with generic_profiles_lock:
                    if len(generic_profiles) >= 256:
                        generic_profiles.pop(next(iter(generic_profiles)))
                    generic_profiles[key] = profile
                load.set_result(profile)
                return profile
            except BaseException as exc:
                load.set_exception(exc)
                raise
            finally:
                with generic_profiles_lock:
                    if generic_profile_loads.get(key) is load:
                        del generic_profile_loads[key]
        raise PackedProfileError("Generic packed shard changed repeatedly while probing")

    async def packed_profile(packed: PackedVirtualPath) -> PackedDatasetProfile:
        known = profile_for_index(packed.index_path.name, packed.catalogue.row_count)
        if known is not None:
            return known
        try:
            return await anyio.to_thread.run_sync(infer_packed_profile, packed)
        except PackedProfileError as exc:
            raise _unprocessable(exc) from exc

    async def packed_path(raw: str) -> PackedVirtualPath | None:
        try:
            return await resolve_packed_virtual_path_in_thread(
                raw,
                guard,
                cache=packed_catalogues,
            )
        except PathForbiddenError as exc:
            raise _forbidden(exc) from exc
        except PathNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        except PackedIndexError as exc:
            raise _unprocessable(exc) from exc

    async def linked(
        dataset_id: str,
    ) -> tuple[DatasetRecord, Path, StoreDescriptor, PackedVirtualPath | None]:
        record = registry.get(dataset_id)
        if record is None:
            raise _not_found("Dataset is not linked")
        packed = await packed_path(record.path)
        if packed is not None:
            if packed.plate is None:
                raise _not_found("Packed plate is no longer available")
            return record, packed.index_path, PACKED_PLATE_DESCRIPTOR, packed
        try:
            path = guard.resolve(record.path)
        except PathForbiddenError as exc:
            raise _forbidden(exc) from exc
        except PathNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        return record, path, _descriptor_or_404(path), None

    @app.get("/api/handshake", response_model=HandshakeResponse)
    async def handshake() -> HandshakeResponse:
        return HandshakeResponse(
            api_version=API_VERSION,
            server_version=__version__,
            environment=settings.environment,
            hostname=socket.gethostname(),
            data_roots=[str(root) for root in guard.roots],
            python_version=platform.python_version(),
            capabilities=Capabilities(
                plate_metadata=metadata_catalog is not None,
                packed_datasets=True,
            ),
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
        packed = await packed_path(path)
        if packed is not None:
            if packed.plate is not None:
                raise HTTPException(status_code=400, detail="Packed plates are not listable")
            if packed.experiment is None:
                return FilesystemListResponse(
                    path=packed.registry_key,
                    entries=[
                        FilesystemEntry(
                            name=experiment,
                            path=str(Path(packed.registry_key) / experiment),
                            kind="dir",
                            detail=_quantity(
                                len(packed.catalogue.plates_for(experiment)),
                                "plate",
                            ),
                        )
                        for experiment in packed.catalogue.experiments
                    ],
                )

            profile = await packed_profile(packed)
            return FilesystemListResponse(
                path=packed.registry_key,
                entries=[
                    FilesystemEntry(
                        name=profile.plate_name(plate),
                        path=str(Path(packed.registry_key) / str(plate)),
                        kind="dir",
                        ome_zarr=PACKED_PLATE_DESCRIPTOR.info,
                        detail=(
                            f"{_quantity(len(plate_info.wells), 'well')} · "
                            f"{_quantity(len(plate_info.sites), 'site')} · "
                            f"{_quantity(len(plate_info.channels), 'channel')}"
                        ),
                    )
                    for plate in packed.catalogue.plates_for(packed.experiment)
                    if (plate_info := packed.catalogue.plate(packed.experiment, plate)) is not None
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
            packed_index = not is_directory and await anyio.to_thread.run_sync(
                is_packed_index, safe_child
            )
            if packed_index:
                try:
                    catalogue = await get_catalogue_in_thread(
                        safe_child,
                        cache=packed_catalogues,
                    )
                except PackedIndexError as exc:
                    raise _unprocessable(exc) from exc
                entries.append(
                    FilesystemEntry(
                        name=child.name,
                        path=str(safe_child),
                        kind="dir",
                        detail=_packed_index_detail(catalogue),
                    )
                )
                continue
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
        packed = await packed_path(request.path)
        if packed is not None:
            if packed.plate is None:
                raise HTTPException(
                    status_code=422,
                    detail="Link a packed plate, not an index or experiment",
                )
            profile = await packed_profile(packed)
            return registry.link_key(
                packed.registry_key,
                request.name or profile.plate_name(packed.plate),
                PACKED_PLATE_DESCRIPTOR.info,
            )
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
        _record, path, descriptor, packed = await linked(dataset_id)
        if packed is not None:
            try:
                return packed_plate_summary(packed, await packed_profile(packed))
            except PackedFacadeNotFoundError as exc:
                raise _not_found(str(exc)) from exc
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
        _record, path, descriptor, packed = await linked(dataset_id)
        if packed is not None:
            try:
                return packed_well_fields(packed, well_path)
            except PackedFacadeNotFoundError as exc:
                raise _not_found(str(exc)) from exc
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
        _record, path, descriptor, packed = await linked(dataset_id)
        if packed is not None:
            profile = await packed_profile(packed)
            if profile.metadata_path is None:
                return PlateMetadataResponse(available=False)
            try:
                assert packed.experiment is not None
                assert packed.plate is not None
                return await anyio.to_thread.run_sync(
                    packed_metadata.for_plate,
                    packed.index_path,
                    profile,
                    packed.experiment,
                    packed.plate,
                )
            except PackedMetadataError as exc:
                raise _unprocessable(exc) from exc
        if descriptor.info.kind != "plate":
            return PlateMetadataResponse(available=False)
        if metadata_catalog is None:
            return PlateMetadataResponse(available=False)
        try:
            return metadata_catalog.for_dataset(path.name)
        except PlateMetadataError as exc:
            raise _unprocessable(exc) from exc

    @app.get(
        "/api/datasets/{dataset_id}/matches",
        response_model=DatasetMatchesResponse,
    )
    async def dataset_matches(
        dataset_id: str,
        well: Annotated[
            str,
            Query(min_length=2, max_length=16, pattern=r"^[A-Za-z]+[0-9]+$"),
        ],
        by: Annotated[MatchMode, Query()],
    ) -> DatasetMatchesResponse:
        _record, _path, _descriptor, packed = await linked(dataset_id)
        if packed is None:
            raise _not_found("Dataset matches are available only for packed plates")
        profile = await packed_profile(packed)
        try:
            assert packed.experiment is not None
            assert packed.plate is not None
            return await anyio.to_thread.run_sync(
                packed_metadata.matches,
                packed.catalogue,
                profile,
                packed.experiment,
                packed.plate,
                well.upper(),
                by,
            )
        except PackedMetadataNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        except PackedMetadataError as exc:
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
        _record, dataset_path, descriptor, packed = await linked(dataset_id)
        if packed is not None:
            profile = await packed_profile(packed)
            try:
                resource = parse_packed_resource(packed, profile, resource_path)
            except PackedFacadeNotFoundError as exc:
                raise _not_found(str(exc)) from exc
            if isinstance(resource, PackedMetadataRequest):
                return _memory_response(
                    resource.content,
                    "application/json",
                    range_header,
                    if_none_match,
                    if_range,
                )
            if not isinstance(resource, PackedChunkRequest):
                raise _not_found(f"Unknown packed facade resource: {resource_path}")
            try:
                chunk = await anyio.to_thread.run_sync(
                    packed_reader.get_chunk,
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
                raise _unprocessable(exc) from exc
            return _memory_response(
                chunk.content,
                "application/octet-stream",
                range_header,
                if_none_match,
                if_range,
                etag=chunk.etag,
                extra_headers={"X-TIMED-Cache": chunk.cache_status},
            )
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
