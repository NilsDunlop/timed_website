"""Reader for TIMED bare-array collections: the virtual OME-Zarr field facade."""

from __future__ import annotations

from ..omezarr import VirtualResource, collection_resource
from .native import NativeStoreReader


class TimedCollectionReader(NativeStoreReader):
    """A TIMED plate folder; each field appears as a virtual OME-Zarr group."""

    def _virtual_resource(self, resource_path: str) -> VirtualResource | None:
        return collection_resource(self.dataset.path, resource_path)
