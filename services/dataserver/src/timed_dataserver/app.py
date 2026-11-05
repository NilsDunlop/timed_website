"""FastAPI application for OME-Zarr access and isolated QC annotations."""

from __future__ import annotations

import hashlib
import json
import platform
import socket
import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

import anyio
import pyarrow.compute as pc
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status

from . import API_VERSION, __version__
from .auth import bearer_auth
from .errors import (
    forbidden,
    install_error_handlers,
    not_found,
    service_unavailable,
    translate_path_errors,
    unprocessable,
)
from .http_cache import file_response, memory_response
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
    QcAnnotationCreateRequest,
    QcAnnotationMutationResponse,
    QcAnnotationRequest,
    QcAnnotationsResponse,
    QcFieldResponse,
    QcFieldsResponse,
    QcRowsResponse,
    QcStatusResponse,
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
from .qc_annotations import (
    QcAnnotationDraft,
    QcAnnotationState,
    QcAnnotationStore,
    QcAnnotationTarget,
    QcAnnotationUnavailableError,
    QcAnnotationValidationError,
    QcRetractionDraft,
)
from .qc_config import QcPathConfig, resolve_qc_path_configs
from .qc_export import QcExportSnapshot, QcExportStore
from .qc_http import qc_json_response
from .qc_payloads import (
    QcFieldNotFoundError,
    QcPayloadError,
    annotation_labels_payload,
    annotation_record_payload,
    dictionary_payload,
    display_ranges_payload,
    field_payload,
    fields_payload,
    review_list_payload,
    triage_payload,
)
from .registry import DatasetRegistry

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
    qc_paths: tuple[QcPathConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class QcRuntime:
    """Loaded index context for one configured QC export."""

    config: QcPathConfig
    catalogue: PackedCatalogue
    profile: PackedDatasetProfile
    store: QcExportStore
    annotations: QcAnnotationStore


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
        raise not_found("Linked path is no longer a recognized OME-Zarr dataset")
    return descriptor


def create_app(settings: ServerSettings, token: str) -> FastAPI:
    """Create one isolated data-server application."""
    if settings.packed_cache_mb <= 0:
        raise ValueError("Packed cache size must be greater than zero")
    guard = RootGuard(settings.data_roots)
    qc_paths = resolve_qc_path_configs(settings.qc_paths, guard)
    qc_private_roots = frozenset(
        private_path
        for config in qc_paths.values()
        for private_path in (config.export_path, config.annotations_path)
    )
    qc_stores: dict[Path, tuple[object, QcExportStore]] = {}
    qc_annotation_stores: dict[Path, QcAnnotationStore] = {}
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

    install_error_handlers(app)

    app.state.settings = settings
    app.state.guard = guard
    app.state.registry = registry
    app.state.metadata_catalog = metadata_catalog
    app.state.packed_catalogues = packed_catalogues
    app.state.packed_reader = packed_reader
    app.state.packed_metadata = packed_metadata
    app.state.qc_paths = qc_paths
    app.state.qc_private_roots = qc_private_roots
    app.state.qc_stores = qc_stores
    app.state.qc_annotation_stores = qc_annotation_stores

    def is_private_qc_path(path: Path) -> bool:
        return any(path == root or path.is_relative_to(root) for root in qc_private_roots)

    def is_raw_private_qc_path(raw: str) -> bool:
        try:
            supplied = Path(raw).expanduser()
            candidates = (
                (supplied,)
                if supplied.is_absolute()
                else tuple(root / supplied for root in guard.roots)
            )
            return any(
                is_private_qc_path(candidate.resolve(strict=False))
                for candidate in candidates
            )
        except (OSError, ValueError):
            return False

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
            raise unprocessable(exc) from exc

    async def packed_path(raw: str) -> PackedVirtualPath | None:
        try:
            with translate_path_errors():
                return await resolve_packed_virtual_path_in_thread(
                    raw,
                    guard,
                    cache=packed_catalogues,
                )
        except PackedIndexError as exc:
            raise unprocessable(exc) from exc

    async def configured_qc_runtime(
        raw: str,
    ) -> tuple[QcPathConfig, QcRuntime | None, str | None]:
        with translate_path_errors():
            index_path = await anyio.to_thread.run_sync(guard.resolve, raw)
        config = qc_paths.get(index_path)
        if config is None:
            raise not_found("No QC export is configured for this index")

        try:
            catalogue = await get_catalogue_in_thread(
                index_path,
                cache=packed_catalogues,
            )
            profile = profile_for_index(index_path.name, catalogue.row_count)
            if profile is None:
                profile = await anyio.to_thread.run_sync(
                    infer_packed_profile,
                    PackedVirtualPath(index_path=index_path, catalogue=catalogue),
                )
        except (OSError, PackedIndexError, PackedProfileError):
            return config, None, "Configured QC index could not be loaded"

        cached = qc_stores.get(index_path)
        if cached is None or cached[0] != catalogue.signature:
            store = QcExportStore(config.export_path, catalogue, profile)
            qc_stores[index_path] = (catalogue.signature, store)
        else:
            store = cached[1]
        annotation_store = qc_annotation_stores.get(index_path)
        if annotation_store is None:
            annotation_store = QcAnnotationStore(config.annotations_path)
            qc_annotation_stores[index_path] = annotation_store
        return (
            config,
            QcRuntime(
                config=config,
                catalogue=catalogue,
                profile=profile,
                store=store,
                annotations=annotation_store,
            ),
            None,
        )

    async def ready_qc_snapshot(raw: str) -> tuple[QcRuntime, QcExportSnapshot]:
        _config, runtime, index_error = await configured_qc_runtime(raw)
        if runtime is None:
            raise service_unavailable(index_error or "Configured QC index is unavailable")
        snapshot = await anyio.to_thread.run_sync(runtime.store.refresh)
        if snapshot is None:
            raise service_unavailable(runtime.store.status.error or "QC export is unavailable")
        if not snapshot.core_ready:
            raise service_unavailable(
                "QC export does not satisfy the core response contract; see QC status"
            )
        return runtime, snapshot

    def qc_well_metadata(
        runtime: QcRuntime,
    ) -> tuple[dict[tuple[str, int, str], dict[str, object]], str]:
        metadata: dict[tuple[str, int, str], dict[str, object]] = {}
        for experiment, plate in sorted(runtime.catalogue.plates):
            response = packed_metadata.for_plate(
                runtime.catalogue.index_path,
                runtime.profile,
                experiment,
                plate,
            )
            if not response.available:
                raise PackedMetadataError("Phase 1 metadata is unavailable")
            for well in response.wells:
                metadata[(experiment, plate, well.well)] = {
                    "gene": well.compound,
                    "well_type": well.moa,
                    "perturbation_type": well.well_type,
                    "is_control": well.is_control,
                }
        serialized = json.dumps(
            [
                [experiment, plate, well, values]
                for (experiment, plate, well), values in sorted(metadata.items())
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        identity = hashlib.blake2s(serialized, digest_size=16).hexdigest()
        return metadata, identity

    async def ready_qc_metadata(
        runtime: QcRuntime,
        *,
        blind: bool,
    ) -> tuple[dict[tuple[str, int, str], dict[str, object]], str | None]:
        if blind:
            return {}, None
        try:
            return await anyio.to_thread.run_sync(qc_well_metadata, runtime)
        except PackedMetadataError as exc:
            raise service_unavailable("Phase 1 metadata is unavailable or invalid") from exc

    def annotation_state(runtime: QcRuntime) -> QcAnnotationState:
        try:
            return runtime.annotations.read()
        except QcAnnotationUnavailableError:
            return QcAnnotationState(
                current=(),
                malformed_lines=0,
                revision="unavailable",
            )

    def annotation_target(
        runtime: QcRuntime,
        snapshot: QcExportSnapshot,
        command: QcAnnotationCreateRequest,
    ) -> QcAnnotationTarget:
        canonical_well = command.well.upper()
        table = snapshot.table("site_metrics.csv")
        selected = table.filter(
            pc.and_(
                pc.equal(table["plate"], command.plate),
                pc.equal(table["well"], canonical_well),
            )
        )
        if command.scope == "field":
            selected = selected.filter(pc.equal(selected["site"], command.site))
            if selected.num_rows != 1:
                raise QcAnnotationValidationError("Annotation field does not exist")
        elif selected.num_rows == 0:
            raise QcAnnotationValidationError("Annotation well does not exist")

        channels = {
            str(value) for value in snapshot.table("channels.csv")["channel"].to_pylist()
        }
        if command.channel != "all" and command.channel not in channels:
            raise QcAnnotationValidationError("Annotation channel is invalid")
        return QcAnnotationTarget(
            scope=command.scope,
            plate=command.plate,
            barcode=runtime.profile.plate_name(command.plate),
            well=canonical_well,
            site=command.site,
            channel=command.channel,
        )

    async def qc_payload_response(
        snapshot: QcExportSnapshot,
        payload: Callable[[], object],
        *,
        endpoint: str,
        blind: bool,
        accept_encoding: str | None,
        if_none_match: str | None,
        state: str | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> Response:
        identity = f"{__version__}:{snapshot.export_id}:{snapshot.registry_hash}"
        try:
            return await anyio.to_thread.run_sync(
                partial(
                    qc_json_response,
                    payload,
                    export_id=identity,
                    endpoint=endpoint,
                    blind=blind,
                    accept_encoding=accept_encoding,
                    if_none_match=if_none_match,
                    state=state,
                    status_code=status_code,
                )
            )
        except QcFieldNotFoundError as exc:
            raise not_found(str(exc)) from exc
        except PackedMetadataError as exc:
            raise service_unavailable("Phase 1 metadata is unavailable or invalid") from exc
        except QcPayloadError as exc:
            raise service_unavailable(str(exc)) from exc

    async def displayed_dataset(record: DatasetRecord) -> DatasetRecord:
        try:
            packed = await packed_path(record.path)
        except HTTPException:
            return record
        if packed is None or packed.experiment is None or packed.plate is None:
            return record
        profile = profile_for_index(
            packed.index_path.name,
            packed.catalogue.row_count,
        )
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

    async def linked(
        dataset_id: str,
    ) -> tuple[DatasetRecord, Path, StoreDescriptor, PackedVirtualPath | None]:
        record = registry.get(dataset_id)
        if record is None:
            raise not_found("Dataset is not linked")
        packed = await packed_path(record.path)
        if packed is not None:
            if packed.plate is None:
                raise not_found("Packed plate is no longer available")
            return record, packed.index_path, PACKED_PLATE_DESCRIPTOR, packed
        with translate_path_errors():
            path = guard.resolve(record.path)
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
                qc_review=any(config.export_readable for config in qc_paths.values()),
                qc_annotations_writable=any(
                    config.annotations_writable for config in qc_paths.values()
                ),
            ),
        )

    @app.get("/api/qc/status", response_model=QcStatusResponse)
    async def qc_status(
        path: Annotated[str, Query(min_length=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        config, runtime, index_error = await configured_qc_runtime(path)
        export_readable, annotations_writable = await anyio.to_thread.run_sync(
            lambda: (config.export_readable, config.annotations_writable)
        )
        if runtime is None:
            response = QcStatusResponse(
                available=False,
                core_ready=False,
                export_readable=export_readable,
                annotations_writable=annotations_writable,
                hash_check="unavailable",
                error=index_error,
            )
        else:
            await anyio.to_thread.run_sync(runtime.store.refresh)
            annotations = await anyio.to_thread.run_sync(annotation_state, runtime)
            store_status = runtime.store.status_for(blind=blind)
            response = QcStatusResponse(
                available=store_status.available,
                core_ready=store_status.core_ready,
                export_readable=export_readable,
                annotations_writable=annotations_writable,
                export_id=store_status.export_id,
                registry_hash=store_status.registry_hash,
                row_count=store_status.row_count,
                hash_check=store_status.hash_check,
                missing_contract=list(store_status.missing_contract),
                unknown_statuses=list(store_status.unknown_statuses),
                malformed_annotation_lines=annotations.malformed_lines,
                error=store_status.error,
            )
        payload = response.model_dump(mode="json")
        state_identity = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        export_identity = (
            f"{__version__}:{response.export_id}:{response.registry_hash}"
            if response.export_id is not None
            else f"{__version__}:unavailable:{path}"
        )
        return await anyio.to_thread.run_sync(
            partial(
                qc_json_response,
                lambda: payload,
                export_id=export_identity,
                endpoint="status",
                blind=blind,
                accept_encoding=accept_encoding,
                if_none_match=if_none_match,
                state=state_identity,
            )
        )

    @app.get("/api/qc/dictionary", response_model=QcRowsResponse)
    async def qc_dictionary(
        path: Annotated[str, Query(min_length=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        _runtime, snapshot = await ready_qc_snapshot(path)
        return await qc_payload_response(
            snapshot,
            lambda: dictionary_payload(snapshot, blind=blind),
            endpoint="dictionary",
            blind=blind,
            accept_encoding=accept_encoding,
            if_none_match=if_none_match,
        )

    @app.get("/api/qc/fields", response_model=QcFieldsResponse)
    async def qc_fields(
        path: Annotated[str, Query(min_length=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        runtime, snapshot = await ready_qc_snapshot(path)
        metadata, metadata_identity = await ready_qc_metadata(runtime, blind=blind)
        annotations = await anyio.to_thread.run_sync(annotation_state, runtime)

        return await qc_payload_response(
            snapshot,
            lambda: fields_payload(
                snapshot,
                metadata,
                blind=blind,
                annotations=annotations.current,
            ),
            endpoint="fields",
            blind=blind,
            accept_encoding=accept_encoding,
            if_none_match=if_none_match,
            state=f"{metadata_identity or ''}:{annotations.revision}",
        )

    @app.get("/api/qc/field", response_model=QcFieldResponse)
    async def qc_field(
        path: Annotated[str, Query(min_length=1)],
        plate: Annotated[int, Query(ge=1)],
        well: Annotated[
            str,
            Query(min_length=2, max_length=16, pattern=r"^[A-Za-z]+[0-9]+$"),
        ],
        site: Annotated[int, Query(ge=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        runtime, snapshot = await ready_qc_snapshot(path)
        canonical_well = well.upper()
        metadata, metadata_identity = await ready_qc_metadata(runtime, blind=blind)
        annotations = await anyio.to_thread.run_sync(annotation_state, runtime)

        def payload() -> object:
            return field_payload(
                snapshot,
                metadata,
                plate=plate,
                well=canonical_well,
                site=site,
                blind=blind,
                annotations=annotations.current,
            )

        return await qc_payload_response(
            snapshot,
            payload,
            endpoint="field",
            blind=blind,
            accept_encoding=accept_encoding,
            if_none_match=if_none_match,
            state=(
                f"{plate}:{canonical_well}:{site}:{metadata_identity or ''}:"
                f"{annotations.revision}"
            ),
        )

    @app.get("/api/qc/triage", response_model=QcRowsResponse)
    async def qc_triage(
        path: Annotated[str, Query(min_length=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        _runtime, snapshot = await ready_qc_snapshot(path)
        return await qc_payload_response(
            snapshot,
            lambda: triage_payload(snapshot, blind=blind),
            endpoint="triage",
            blind=blind,
            accept_encoding=accept_encoding,
            if_none_match=if_none_match,
        )

    @app.get("/api/qc/display-ranges", response_model=QcRowsResponse)
    async def qc_display_ranges(
        path: Annotated[str, Query(min_length=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        _runtime, snapshot = await ready_qc_snapshot(path)
        return await qc_payload_response(
            snapshot,
            lambda: display_ranges_payload(snapshot, blind=blind),
            endpoint="display-ranges",
            blind=blind,
            accept_encoding=accept_encoding,
            if_none_match=if_none_match,
        )

    @app.get("/api/qc/review-list", response_model=QcRowsResponse)
    async def qc_review_list(
        path: Annotated[str, Query(min_length=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        _runtime, snapshot = await ready_qc_snapshot(path)
        return await qc_payload_response(
            snapshot,
            lambda: review_list_payload(snapshot, blind=blind),
            endpoint="review-list",
            blind=blind,
            accept_encoding=accept_encoding,
            if_none_match=if_none_match,
        )

    @app.get("/api/qc/annotations", response_model=QcAnnotationsResponse)
    async def qc_annotations(
        path: Annotated[str, Query(min_length=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        runtime, snapshot = await ready_qc_snapshot(path)
        annotations = await anyio.to_thread.run_sync(annotation_state, runtime)
        writable = await anyio.to_thread.run_sync(
            lambda: runtime.config.annotations_writable
        )
        return await qc_payload_response(
            snapshot,
            lambda: {
                "export_id": snapshot.export_id,
                "registry_hash": snapshot.registry_hash,
                "blinded": blind,
                "writable": writable,
                "malformed_lines": annotations.malformed_lines,
                "labels": annotation_labels_payload(snapshot),
                "annotations": [
                    annotation_record_payload(record, blind=blind)
                    for record in annotations.current
                ],
            },
            endpoint="annotations",
            blind=blind,
            accept_encoding=accept_encoding,
            if_none_match=if_none_match,
            state=f"{annotations.revision}:{int(writable)}",
        )

    @app.post(
        "/api/qc/annotations",
        response_model=QcAnnotationMutationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def write_qc_annotation(
        command: QcAnnotationRequest,
        path: Annotated[str, Query(min_length=1)],
        blind: Annotated[bool, Query()] = False,
        accept_encoding: Annotated[str | None, Header()] = None,
    ) -> Response:
        runtime, snapshot = await ready_qc_snapshot(path)
        if command.export_id != snapshot.export_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The QC export changed; reload the review before saving",
            )
        writable = await anyio.to_thread.run_sync(
            lambda: runtime.config.annotations_writable
        )
        if not writable:
            raise service_unavailable("QC annotations are not writable")

        def append() -> dict[str, object]:
            if isinstance(command, QcAnnotationCreateRequest):
                target = annotation_target(runtime, snapshot, command)
                record = runtime.annotations.append_annotation(
                    QcAnnotationDraft(
                        export_id=snapshot.export_id,
                        registry_hash=snapshot.registry_hash,
                        target=target,
                        label=command.label,
                        confidence=command.confidence,
                        note=command.note,
                        blinded=blind,
                        app_version=__version__,
                    ),
                    allowed_labels=snapshot.annotation_label_names,
                )
            else:
                record = runtime.annotations.append_retraction(
                    QcRetractionDraft(
                        annotation_id=command.id,
                        export_id=snapshot.export_id,
                        registry_hash=snapshot.registry_hash,
                        blinded=blind,
                        app_version=__version__,
                    )
                )
            return dict(record)

        try:
            record = await anyio.to_thread.run_sync(append)
        except QcAnnotationValidationError as exc:
            raise unprocessable(exc) from exc
        except QcAnnotationUnavailableError as exc:
            raise service_unavailable("QC annotations are not writable") from exc

        return await qc_payload_response(
            snapshot,
            lambda: {
                "export_id": snapshot.export_id,
                "registry_hash": snapshot.registry_hash,
                "blinded": blind,
                "record": annotation_record_payload(record, blind=blind),
            },
            endpoint="annotations:write",
            blind=blind,
            accept_encoding=accept_encoding,
            if_none_match=None,
            state=f"{record['record']}:{record['id']}:{record['time']}",
            status_code=status.HTTP_201_CREATED,
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
        if is_raw_private_qc_path(path):
            raise forbidden(PathForbiddenError("Configured QC folders are not browsable"))
        packed = await packed_path(path)
        if packed is not None:
            if packed.plate is not None:
                raise HTTPException(status_code=400, detail="Packed plates are not listable")
            if packed.experiment is None:
                profile = await packed_profile(packed)
                return FilesystemListResponse(
                    path=packed.registry_key,
                    entries=[
                        FilesystemEntry(
                            name=profile.experiment_display_name(experiment),
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
                        name=profile.plate_display_name(packed.experiment, plate),
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
        with translate_path_errors():
            target = guard.resolve(path)
            root = guard.root_for(target)
        if is_private_qc_path(target):
            raise forbidden(PathForbiddenError("Configured QC folders are not browsable"))
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
            if is_private_qc_path(safe_child):
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
                    raise unprocessable(exc) from exc
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
        datasets = [
            await displayed_dataset(record)
            for record in registry.list()
        ]
        return DatasetListResponse(datasets=datasets)

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
            record = registry.link_key(
                packed.registry_key,
                request.name
                or profile.plate_display_name(packed.experiment, packed.plate),
                PACKED_PLATE_DESCRIPTOR.info,
            )
            return await displayed_dataset(record)
        with translate_path_errors():
            path = guard.resolve(request.path)
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
            raise not_found("Dataset is not linked")
        return Response(status_code=204)

    @app.get("/api/datasets/{dataset_id}/summary", response_model=DatasetSummary)
    async def summarize_dataset(dataset_id: str) -> DatasetSummary:
        _record, path, descriptor, packed = await linked(dataset_id)
        if packed is not None:
            try:
                return packed_plate_summary(packed, await packed_profile(packed))
            except PackedFacadeNotFoundError as exc:
                raise not_found(str(exc)) from exc
        try:
            return dataset_summary(path, descriptor)
        except PathForbiddenError as exc:
            raise forbidden(exc) from exc
        except OmeZarrError as exc:
            raise unprocessable(exc) from exc

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
                raise not_found(str(exc)) from exc
        if descriptor.info.kind != "plate":
            raise HTTPException(status_code=422, detail="Dataset is not a plate")
        try:
            return well_fields(path, descriptor, well_path)
        except PathForbiddenError as exc:
            raise forbidden(exc) from exc
        except OmeZarrError as exc:
            raise not_found(str(exc)) from exc

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
                raise unprocessable(exc) from exc
        if descriptor.info.kind != "plate":
            return PlateMetadataResponse(available=False)
        if metadata_catalog is None:
            return PlateMetadataResponse(available=False)
        try:
            return metadata_catalog.for_dataset(path.name)
        except PlateMetadataError as exc:
            raise unprocessable(exc) from exc

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
            raise not_found("Dataset matches are available only for packed plates")
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
            raise not_found(str(exc)) from exc
        except PackedMetadataError as exc:
            raise unprocessable(exc) from exc

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
                guard.child(dataset_path, resource_path, must_exist=False)
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
                file_path = guard.child(dataset_path, mapped_path)
        except OmeZarrError as exc:
            raise unprocessable(exc) from exc
        if not file_path.is_file():
            raise not_found(f"Zarr resource does not exist: {resource_path}")
        return file_response(file_path, range_header, if_none_match, if_range)

    return app
