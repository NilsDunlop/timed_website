"""Configured TIMED mask folders, each mirroring one collection's plates and fields.

``serve --timed-masks COLLECTION=FOLDER`` names, for a TIMED collection inside
a data root, a folder that mirrors it: the masks of
``COLLECTION/<plate>.zarr/<well>_s<site>.zarr`` lie in
``FOLDER/<plate>.zarr/<well>_s<site>.zarr/``. Mask folders are explicit trusted
configuration, like the QC folders: read-only and never browsable, linkable or
served, and never added to ``RootGuard``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .paths import PathAccessError, PrivateFolders, RootGuard

OPTION = "--timed-masks"


class MaskConfigurationError(ValueError):
    """Raised when TIMED mask-folder mappings are malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class TimedMaskMapping:
    """One TIMED collection and the mask folder that mirrors it."""

    collection: Path
    masks_root: Path


def parse_timed_mask_mappings(values: Sequence[str]) -> tuple[TimedMaskMapping, ...]:
    """Parse repeated CLI mappings without resolving them."""
    mappings: dict[Path, Path] = {}
    for value in values:
        if value.count("=") != 1:
            raise MaskConfigurationError(
                f"{OPTION} must use exactly one collection=folder separator"
            )
        raw_collection, raw_folder = value.split("=", 1)
        if not raw_collection or not raw_folder:
            raise MaskConfigurationError(f"{OPTION} requires a non-empty collection and folder")
        collection = Path(raw_collection).expanduser()
        folder = Path(raw_folder).expanduser()
        if not collection.is_absolute() or not folder.is_absolute():
            raise MaskConfigurationError(f"{OPTION} paths must be absolute")
        if collection in mappings:
            raise MaskConfigurationError(f"{OPTION} repeats a collection path")
        mappings[collection] = folder
    return tuple(
        TimedMaskMapping(collection=collection, masks_root=folder)
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
    must not contain a data root or its own collection.
    """
    resolved: list[TimedMaskMapping] = []
    for mapping in mappings:
        try:
            collection = guard.resolve(str(mapping.collection))
        except PathAccessError as exc:
            raise MaskConfigurationError(f"Invalid TIMED mask collection: {exc}") from exc
        if not collection.is_dir():
            raise MaskConfigurationError("Configured TIMED mask collection is not a directory")
        if any(existing.collection == collection for existing in resolved):
            raise MaskConfigurationError("TIMED mask configuration repeats a collection")
        masks_root = mapping.masks_root.resolve(strict=False)
        if any(masks_root == root or root.is_relative_to(masks_root) for root in guard.roots):
            raise MaskConfigurationError("Configured mask folders cannot contain a data root")
        if masks_root == collection or collection.is_relative_to(masks_root):
            raise MaskConfigurationError("Configured mask folders cannot contain their collection")
        if any(_overlaps(masks_root, existing.masks_root) for existing in resolved):
            raise MaskConfigurationError("Configured mask folders cannot overlap each other")
        resolved.append(TimedMaskMapping(collection=collection, masks_root=masks_root))
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


def mirrored_plate(
    mappings: Sequence[TimedMaskMapping],
    plate: Path,
) -> tuple[TimedMaskMapping, Path] | None:
    """The mapping whose collection holds a resolved plate, and the plate's mask folder.

    The innermost collection wins when collections nest.
    """
    candidates = [
        mapping
        for mapping in mappings
        if plate != mapping.collection and plate.is_relative_to(mapping.collection)
    ]
    if not candidates:
        return None
    mapping = max(candidates, key=lambda candidate: len(candidate.collection.parts))
    return mapping, mapping.masks_root / plate.relative_to(mapping.collection)
