"""Configured TIMED mask folders: per collection, an export mirror or production runs.

``serve --timed-masks COLLECTION=FOLDER`` names, for a TIMED collection inside
a data root, a folder that mirrors it: the masks of
``COLLECTION/<plate>.zarr/<well>_s<site>.zarr`` lie in
``FOLDER/<plate>.zarr/<well>_s<site>.zarr/``. ``serve --timed-segmentation
COLLECTION=FOLDER`` names a folder of production segmentation runs, laid out as
``FOLDER/<plate>/sites/<well>_s<site>/stage1/<run>/labels/<plate>/<well>_s<site>.zarr``,
where ``<plate>`` is the plate folder's name without ``.zarr``. Both are mask
folders: explicit trusted configuration, like the QC folders, read-only and
never browsable, linkable or served, and never added to ``RootGuard``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .paths import PathAccessError, PrivateFolders, RootGuard

type MaskLayout = Literal["export", "production"]

OPTION = "--timed-masks"
OPTIONS: dict[MaskLayout, str] = {"export": OPTION, "production": "--timed-segmentation"}


class MaskConfigurationError(ValueError):
    """Raised when TIMED mask-folder mappings are malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class TimedMaskMapping:
    """One TIMED collection and one mask folder for it, in one layout."""

    collection: Path
    masks_root: Path
    layout: MaskLayout = "export"


def parse_timed_mask_mappings(
    values: Sequence[str],
    layout: MaskLayout = "export",
) -> tuple[TimedMaskMapping, ...]:
    """Parse one option's repeated CLI mappings without resolving them."""
    option = OPTIONS[layout]
    mappings: dict[Path, Path] = {}
    for value in values:
        if value.count("=") != 1:
            raise MaskConfigurationError(
                f"{option} must use exactly one collection=folder separator"
            )
        raw_collection, raw_folder = value.split("=", 1)
        if not raw_collection or not raw_folder:
            raise MaskConfigurationError(f"{option} requires a non-empty collection and folder")
        collection = Path(raw_collection).expanduser()
        folder = Path(raw_folder).expanduser()
        if not collection.is_absolute() or not folder.is_absolute():
            raise MaskConfigurationError(f"{option} paths must be absolute")
        if collection in mappings:
            raise MaskConfigurationError(f"{option} repeats a collection path")
        mappings[collection] = folder
    return tuple(
        TimedMaskMapping(collection=collection, masks_root=folder, layout=layout)
        for collection, folder in mappings.items()
    )


def _overlaps(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def resolve_timed_mask_mappings(
    mappings: Sequence[TimedMaskMapping],
    guard: RootGuard,
) -> tuple[TimedMaskMapping, ...]:
    """Canonicalize each collection through the guard and each mask folder as given.

    A mask folder may lie inside a data root, where it stays private, but it
    must not contain a data root or its own collection, nor overlap any other
    mask folder, whatever its layout. A collection has at most one folder per
    layout.
    """
    resolved: list[TimedMaskMapping] = []
    for mapping in mappings:
        try:
            collection = guard.resolve(str(mapping.collection))
        except PathAccessError as exc:
            raise MaskConfigurationError(f"Invalid TIMED mask collection: {exc}") from exc
        if not collection.is_dir():
            raise MaskConfigurationError("Configured TIMED mask collection is not a directory")
        if any(
            existing.collection == collection and existing.layout == mapping.layout
            for existing in resolved
        ):
            raise MaskConfigurationError("TIMED mask configuration repeats a collection")
        masks_root = mapping.masks_root.resolve(strict=False)
        if any(masks_root == root or root.is_relative_to(masks_root) for root in guard.roots):
            raise MaskConfigurationError("Configured mask folders cannot contain a data root")
        if masks_root == collection or collection.is_relative_to(masks_root):
            raise MaskConfigurationError("Configured mask folders cannot contain their collection")
        if any(_overlaps(masks_root, existing.masks_root) for existing in resolved):
            raise MaskConfigurationError("Configured mask folders cannot overlap each other")
        resolved.append(
            TimedMaskMapping(collection=collection, masks_root=masks_root, layout=mapping.layout)
        )
    return tuple(resolved)


def mask_private_folders(
    mappings: Sequence[TimedMaskMapping],
    guard: RootGuard,
) -> PrivateFolders:
    """The configured mask folders, which are never browsable, linkable or served."""
    return PrivateFolders(
        roots=frozenset(mapping.masks_root for mapping in mappings),
        data_roots=guard.roots,
    )


def _innermost(
    mappings: Sequence[TimedMaskMapping],
    plate: Path,
    layout: MaskLayout,
) -> TimedMaskMapping | None:
    """The mapping of this layout whose collection holds a resolved plate.

    The innermost collection wins when collections nest.
    """
    candidates = [
        mapping
        for mapping in mappings
        if mapping.layout == layout
        and plate != mapping.collection
        and plate.is_relative_to(mapping.collection)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: len(candidate.collection.parts))


def mirrored_plate(
    mappings: Sequence[TimedMaskMapping],
    plate: Path,
) -> tuple[TimedMaskMapping, Path] | None:
    """The export mapping whose collection holds a resolved plate, and its mask folder."""
    mapping = _innermost(mappings, plate, "export")
    if mapping is None:
        return None
    return mapping, mapping.masks_root / plate.relative_to(mapping.collection)


def production_plate(
    mappings: Sequence[TimedMaskMapping],
    plate: Path,
) -> tuple[TimedMaskMapping, Path, str] | None:
    """The production mapping for a resolved plate, its runs' folder and its plate name.

    The plate name is the plate folder's name without ``.zarr``, as the runs
    store it; nested plates keep their place below the collection.
    """
    mapping = _innermost(mappings, plate, "production")
    if mapping is None:
        return None
    relative = plate.relative_to(mapping.collection)
    name = relative.name.removesuffix(".zarr")
    if not name:
        return None
    return mapping, mapping.masks_root / relative.parent / name, name
