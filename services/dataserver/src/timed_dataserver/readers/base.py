"""What every dataset reader offers, and the optional mask-source hook.

A reader serves one linked dataset through its kind's builders and caches,
and raises the same HTTP errors, with the same texts, as the per-kind route
branches it replaces.

``mask_source()`` returns None for a dataset without segmentation masks. A
mask source is read-only and blocking: call its methods, and its planes',
from worker threads; ``field()`` alone never blocks. Mask sources raise
MaskNotFoundError and MaskStoreError, which the mask routes answer with 404
and 409.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..mask_reader import MaskModelEntry
from ..models import ImageSummary, PlateMetadataResponse, PlateSummary, WellFieldsResponse


@dataclass(frozen=True, slots=True)
class MemoryResource:
    """A Zarr resource answered from memory."""

    content: bytes
    content_type: str
    etag: str | None = None
    extra_headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class FileResource:
    """A Zarr resource answered from an unchanged file."""

    path: Path


ZarrResource = MemoryResource | FileResource


@dataclass(frozen=True, slots=True)
class MaskField:
    """A field as a mask source names it."""

    well: str
    site: int


class MaskPlane(Protocol):
    """One committed mask of one kind at one timepoint, not yet decoded."""

    def etag(self, color: str, width: int) -> str:
        """The outline's validator; computed without reading the mask."""
        ...

    def outline(self, color: str, width: int) -> bytes:
        """The outline PNG in an upper-case ``RRGGBB`` colour."""
        ...


class MaskSource(Protocol):
    """One dataset's segmentation masks."""

    def field(self, well: str, site: int) -> MaskField:
        """The canonical field; raise MaskNotFoundError when it does not exist."""
        ...

    def models(self) -> list[MaskModelEntry]:
        """The models offered for this dataset, the default first."""
        ...

    def counts(self, model_tag: str, field: MaskField, t: int | None = None) -> dict[str, int]:
        """Each kind's object count for one field and timepoint."""
        ...

    def open_plane(
        self,
        model_tag: str,
        field: MaskField,
        kind: str,
        t: int | None = None,
    ) -> MaskPlane:
        """Resolve one mask; ``t`` is None, or 0, for sources without time."""
        ...


class DatasetReader(Protocol):
    """One linked dataset, read through its kind's builders and caches."""

    async def summary(self) -> ImageSummary | PlateSummary: ...

    async def well_fields(self, well_path: str) -> WellFieldsResponse: ...

    async def metadata(self) -> PlateMetadataResponse: ...

    async def zarr_resource(self, resource_path: str) -> ZarrResource: ...

    def mask_source(self) -> MaskSource | None: ...
