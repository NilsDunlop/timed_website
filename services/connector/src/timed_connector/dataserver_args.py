"""The data-server arguments that both launch paths derive from a profile's collections."""

from __future__ import annotations

from collections.abc import Sequence

from .models import CollectionConfig


def collection_arguments(collections: Sequence[CollectionConfig]) -> list[str]:
    """``serve`` arguments naming each collection and its mask, segmentation and QC folders."""
    arguments: list[str] = []
    for collection in collections:
        arguments.extend(("--collection", collection.path))
        if collection.masks_root is not None:
            arguments.extend(("--timed-masks", f"{collection.path}={collection.masks_root}"))
        if collection.segmentation_root is not None:
            arguments.extend(
                ("--timed-segmentation", f"{collection.path}={collection.segmentation_root}")
            )
        if collection.qc_export is not None and collection.qc_annotations is not None:
            arguments.extend(("--qc-export", f"{collection.path}={collection.qc_export}"))
            arguments.extend(
                ("--qc-annotations", f"{collection.path}={collection.qc_annotations}")
            )
    return arguments
