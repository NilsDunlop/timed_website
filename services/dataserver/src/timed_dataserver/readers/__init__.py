"""One reader per dataset kind behind a common interface.

``open_reader`` resolves a linked dataset exactly as ``linked()`` does (packed
paths first, with the same 404, 403 and 422 texts) and wraps it in the
reader for its kind. No dataset kind is persisted.
"""

from __future__ import annotations

from ..context import ServerContext
from ..linked import LinkedPackedPlate, linked
from .base import DatasetReader
from .native import NativeStoreReader
from .packed import PackedPlateReader
from .timed import TimedCollectionReader


async def open_reader(ctx: ServerContext, dataset_id: str) -> DatasetReader:
    """The reader for one linked dataset."""
    dataset = await linked(ctx, dataset_id)
    if isinstance(dataset, LinkedPackedPlate):
        return PackedPlateReader(ctx, dataset)
    if dataset.descriptor.layout == "timed-collection":
        return TimedCollectionReader(ctx, dataset)
    return NativeStoreReader(ctx, dataset)
