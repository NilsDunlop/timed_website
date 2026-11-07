"""Filesystem route: data roots, folders, packed indexes and their virtual hierarchy."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query

from ..context import ServerContext, get_context
from ..errors import forbidden, translate_path_errors, unprocessable
from ..linked import PACKED_PLATE_INFO, packed_path, packed_profile
from ..models import FilesystemEntry, FilesystemListResponse
from ..omezarr import inspect_store
from ..packed import PackedCatalogue, PackedIndexError, get_catalogue_in_thread, is_packed_index
from ..paths import PathForbiddenError, PathNotFoundError, os_error_reason

router = APIRouter()


def _quantity(count: int, singular: str) -> str:
    return f"{count} {singular if count == 1 else singular + 's'}"


def _packed_index_detail(catalogue: PackedCatalogue) -> str:
    return (
        f"{_quantity(len(catalogue.experiments), 'cell line')} · "
        f"{_quantity(len(catalogue.plates), 'plate')}"
    )


@router.get("/api/fs/list", response_model=FilesystemListResponse)
async def list_files(
    ctx: Annotated[ServerContext, Depends(get_context)],
    path: Annotated[str | None, Query()] = None,
) -> FilesystemListResponse:
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
                for root in ctx.guard.roots
            ],
        )
    if ctx.qc_private_roots.contains_raw(path):
        raise forbidden(PathForbiddenError("Configured QC folders are not browsable"))
    packed = await packed_path(ctx, path)
    if packed is not None:
        if packed.plate is not None:
            raise HTTPException(status_code=400, detail="Packed plates are not listable")
        if packed.experiment is None:
            profile = await packed_profile(ctx, packed)
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

        profile = await packed_profile(ctx, packed)
        return FilesystemListResponse(
            path=packed.registry_key,
            entries=[
                FilesystemEntry(
                    name=profile.plate_display_name(packed.experiment, plate),
                    path=str(Path(packed.registry_key) / str(plate)),
                    kind="dir",
                    ome_zarr=PACKED_PLATE_INFO,
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
        target = ctx.guard.resolve(path)
        root = ctx.guard.root_for(target)
    if ctx.qc_private_roots.contains(target):
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
        raise HTTPException(
            status_code=403, detail=f"Could not list directory: {os_error_reason(exc)}"
        ) from exc
    for child in children:
        try:
            safe_child = ctx.guard.resolve(str(child))
        except (PathForbiddenError, PathNotFoundError):
            continue
        if ctx.qc_private_roots.contains(safe_child):
            continue
        is_directory = safe_child.is_dir()
        packed_index = not is_directory and await anyio.to_thread.run_sync(
            is_packed_index, safe_child
        )
        if packed_index:
            try:
                catalogue = await get_catalogue_in_thread(
                    safe_child,
                    cache=ctx.packed_catalogues,
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
