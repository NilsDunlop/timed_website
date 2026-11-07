"""FastAPI application for OME-Zarr access and isolated QC annotations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Annotated

import anyio
import pyarrow.compute as pc
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status

from . import __version__
from .auth import bearer_auth
from .context import ServerContext
from .errors import (
    install_error_handlers,
    not_found,
    service_unavailable,
    translate_path_errors,
    unprocessable,
)
from .models import (
    QcAnnotationCreateRequest,
    QcAnnotationMutationResponse,
    QcAnnotationRequest,
    QcAnnotationsResponse,
    QcFieldResponse,
    QcFieldsResponse,
    QcRowsResponse,
    QcStatusResponse,
)
from .packed import (
    PackedCatalogue,
    PackedIndexError,
    PackedVirtualPath,
    get_catalogue_in_thread,
)
from .packed_metadata import PackedMetadataError
from .packed_profiles import PackedDatasetProfile, PackedProfileError
from .qc_annotations import (
    QcAnnotationDraft,
    QcAnnotationState,
    QcAnnotationStore,
    QcAnnotationTarget,
    QcAnnotationUnavailableError,
    QcAnnotationValidationError,
    QcRetractionDraft,
)
from .qc_config import QcPathConfig
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
from .routes import datasets as dataset_routes
from .routes import filesystem as filesystem_routes
from .routes import handshake as handshake_routes
from .routes import zarr as zarr_routes
from .settings import ServerSettings


@dataclass(frozen=True, slots=True)
class QcRuntime:
    """Loaded index context for one configured QC export."""

    config: QcPathConfig
    catalogue: PackedCatalogue
    profile: PackedDatasetProfile
    store: QcExportStore
    annotations: QcAnnotationStore


def create_app(
    settings: ServerSettings,
    token: str,
    *,
    on_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    """Create one isolated data-server application.

    ``on_shutdown`` runs during lifespan shutdown, after the context closes.
    """
    ctx = ServerContext.build(settings)
    require_token = bearer_auth(token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            try:
                await ctx.close()
            finally:
                if on_shutdown is not None:
                    on_shutdown()

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

    app.state.context = ctx
    app.state.settings = ctx.settings
    app.state.guard = ctx.guard
    app.state.registry = ctx.registry
    app.state.metadata_catalog = ctx.metadata_catalog
    app.state.packed_catalogues = ctx.packed_catalogues
    app.state.packed_reader = ctx.packed_reader
    app.state.packed_metadata = ctx.packed_metadata
    app.state.qc_paths = ctx.qc_paths
    app.state.qc_private_roots = ctx.qc_private_roots.roots
    app.state.qc_stores = ctx.qc_stores
    app.state.qc_annotation_stores = ctx.qc_annotation_stores

    async def configured_qc_runtime(
        raw: str,
    ) -> tuple[QcPathConfig, QcRuntime | None, str | None]:
        with translate_path_errors():
            index_path = await anyio.to_thread.run_sync(ctx.guard.resolve, raw)
        config = ctx.qc_paths.get(index_path)
        if config is None:
            raise not_found("No QC export is configured for this index")

        try:
            catalogue = await get_catalogue_in_thread(
                index_path,
                cache=ctx.packed_catalogues,
            )
            profile = await ctx.packed_profiles.resolve(
                PackedVirtualPath(index_path=index_path, catalogue=catalogue)
            )
        except (OSError, PackedIndexError, PackedProfileError):
            return config, None, "Configured QC index could not be loaded"

        cached = ctx.qc_stores.get(index_path)
        if cached is None or cached[0] != catalogue.signature:
            store = QcExportStore(config.export_path, catalogue, profile)
            ctx.qc_stores[index_path] = (catalogue.signature, store)
        else:
            store = cached[1]
        annotation_store = ctx.qc_annotation_stores.get(index_path)
        if annotation_store is None:
            annotation_store = QcAnnotationStore(config.annotations_path)
            ctx.qc_annotation_stores[index_path] = annotation_store
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
            response = ctx.packed_metadata.for_plate(
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

    # Registration order is the OpenAPI order: handshake, QC, filesystem, datasets, Zarr.
    # The QC routes stay here until they get their own route modules.
    app.include_router(handshake_routes.router)

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

    app.include_router(filesystem_routes.router)
    app.include_router(dataset_routes.router)
    app.include_router(zarr_routes.router)

    return app
