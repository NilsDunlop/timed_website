"""Reader for TIMED bare-array collections: the virtual OME-Zarr field facade and masks.

A collection with configured mask folders (``serve --timed-masks`` for an
export mirror, ``serve --timed-segmentation`` for production runs) offers the
models pinned below on every field whose folder holds them, in this order, so
the first covering model is the default. TIMED masks cover single fields, so
only a field listing reports them: a plate listing lists none, as it did
before TIMED had masks, and so does a field without masks. They have one plane
per image timepoint, so every count and outline names its timepoint, and cache
keys and ETags include it together with the mask files' identities.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..mask_catalogue import MaskModel
from ..mask_reader import (
    DecodedMask,
    MaskModelEntry,
    MaskNotFoundError,
    MaskReader,
    MaskRequestError,
)
from ..omezarr import (
    OmeZarrError,
    VirtualResource,
    collection_resource,
    timed_field_geometry,
)
from ..timed_mask_config import MaskLayout
from ..timed_mask_layout import TimedMaskFolder, plate_folders
from ..timed_masks import TimedMaskArray, has_array, read_plane
from .base import MaskField
from .native import NativeStoreReader

_WELL = re.compile(r"[A-Z]+[0-9]+")


@dataclass(frozen=True, slots=True)
class TimedMaskModel:
    """A mask model pinned in code: its layout, and each kind's array or run."""

    tag: str
    layout: MaskLayout
    arrays: tuple[tuple[str, str], ...]
    recipe: str

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind, _array in self.arrays)


TIMED_MASK_MODELS = (
    TimedMaskModel(
        tag="R4_cpsam_dS_corrected",
        layout="production",
        arrays=(("nuclei", "R4_cpsam_dS_corrected_nuclei"), ("cells", "R4_cpsam_dS_corrected")),
        recipe="Cellpose-SAM cells (TiLu-corrected 505 + raw 625 nm)",
    ),
    TimedMaskModel(
        tag="masks_raw",
        layout="export",
        arrays=(("nuclei", "nuclei"), ("cells", "masks_raw")),
        recipe="Cellpose-SAM cells (raw 505 + 625 nm) · classical nuclei (raw 625 nm)",
    ),
)


class TimedMaskPlane:
    """One field's mask at one timepoint, decoded on demand."""

    def __init__(self, masks: MaskReader, array: TimedMaskArray, kind: str, t: int) -> None:
        self._masks = masks
        self.array = array
        self.kind = kind
        self.t = t

    @property
    def key(self) -> tuple[object, ...]:
        return ("timed", str(self.array.shard), self.array.revision, self.t)

    def labels(self) -> DecodedMask:
        return self._masks.decoded(self.key, lambda: read_plane(self.array, self.t))

    def etag(self, color: str, width: int) -> str:
        identity = json.dumps(
            {
                "server": __version__,
                "store": self.array.label,
                "revision": list(self.array.revision),
                "t": self.t,
                "kind": self.kind,
                "color": color,
                "width": width,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f'"{hashlib.sha256(identity).hexdigest()}"'

    def outline(self, color: str, width: int) -> bytes:
        return self._masks.rendered(self.key, self.labels, color, width)


class TimedMaskSource:
    """The pinned mask models of one TIMED plate, each read from its layout's folder."""

    def __init__(
        self,
        masks: MaskReader,
        plate: Path,
        folders: Mapping[MaskLayout, TimedMaskFolder],
    ) -> None:
        self._masks = masks
        self._plate = plate
        self._offered = tuple(
            (model, folders[model.layout])
            for model in TIMED_MASK_MODELS
            if model.layout in folders
        )

    def field(self, well: str, site: int) -> MaskField:
        canonical = well.upper()
        if _WELL.fullmatch(canonical) is None or site < 0:
            raise MaskNotFoundError(f"TIMED field does not exist: {canonical} site {site}")
        return MaskField(canonical, site)

    def _image_field(self, field: MaskField) -> Path | None:
        try:
            plate = self._plate.resolve(strict=True)
            resolved = (self._plate / f"{field.well}_s{field.site}.zarr").resolve(strict=True)
        except OSError:
            return None
        if resolved == plate or not resolved.is_relative_to(plate) or not resolved.is_dir():
            return None
        return resolved

    @staticmethod
    def _holds(model: TimedMaskModel, folder: TimedMaskFolder, field: MaskField) -> bool:
        """Whether the folder holds any of the model's arrays for this field."""
        return any(
            has_array(folder.array_dir(field.well, field.site, array), folder.root)
            for _kind, array in model.arrays
        )

    def models(self, field: MaskField | None = None) -> list[MaskModelEntry]:
        """The models covering a field whose image exists; the first is the default."""
        if field is None or self._image_field(field) is None:
            return []
        covering = [
            (model, folder)
            for model, folder in self._offered
            if self._holds(model, folder, field)
        ]
        return [
            MaskModelEntry(
                model=MaskModel(
                    tag=model.tag,
                    recipe=model.recipe,
                    kinds=model.kinds,
                    frozen=True,
                    created_at=None,
                    directory=folder.plate,
                ),
                available=True,
                default=position == 0,
            )
            for position, (model, folder) in enumerate(covering)
        ]

    def _model(self, model_tag: str) -> tuple[TimedMaskModel, TimedMaskFolder]:
        for model, folder in self._offered:
            if model.tag == model_tag:
                return model, folder
        raise MaskNotFoundError(f"Unknown mask model: {model_tag}")

    @staticmethod
    def _require_timepoint(t: int | None) -> int:
        if t is None:
            raise MaskRequestError("TIMED masks have one plane per timepoint: give t")
        return t

    def _arrays(
        self,
        model: TimedMaskModel,
        folder: TimedMaskFolder,
        field: MaskField,
        kinds: tuple[str, ...],
    ) -> dict[str, TimedMaskArray]:
        """The field's validated arrays for these kinds; absent kinds are left out."""
        missing = f"TIMED field does not exist: {field.well} site {field.site}"
        image = self._image_field(field)
        if image is None:
            raise MaskNotFoundError(missing)
        try:
            shape, t_indices = timed_field_geometry(image)
        except OmeZarrError:
            raise MaskNotFoundError(missing) from None
        found: dict[str, TimedMaskArray] = {}
        for kind, array_name in model.arrays:
            if kind not in kinds:
                continue
            array = self._masks.timed_arrays.get(
                folder.label(field.well, field.site, array_name),
                folder.array_dir(field.well, field.site, array_name),
                folder.root,
                shape,
                t_indices,
                folder.run(field.well, field.site, array_name),
            )
            if array is not None:
                found[kind] = array
        return found

    @staticmethod
    def _check_timepoint(array: TimedMaskArray, t: int) -> int:
        if t >= array.timepoints:
            raise MaskRequestError(
                f"Timepoint {t} is outside this field's {array.timepoints} timepoints "
                f"(0 to {array.timepoints - 1})"
            )
        return t

    def counts(self, model_tag: str, field: MaskField, t: int | None = None) -> dict[str, int]:
        model, folder = self._model(model_tag)
        timepoint = self._require_timepoint(t)
        arrays = self._arrays(model, folder, field, model.kinds)
        if not arrays:
            raise MaskNotFoundError(f"No masks for {field.well} site {field.site}")
        return {
            kind: TimedMaskPlane(self._masks, array, kind, self._check_timepoint(array, timepoint))
            .labels()
            .count
            for kind, array in arrays.items()
        }

    def open_plane(
        self,
        model_tag: str,
        field: MaskField,
        kind: str,
        t: int | None = None,
    ) -> TimedMaskPlane:
        model, folder = self._model(model_tag)
        if kind not in model.kinds:
            raise MaskNotFoundError(f"No {kind} mask for {field.well} site {field.site}")
        timepoint = self._require_timepoint(t)
        array = self._arrays(model, folder, field, (kind,)).get(kind)
        if array is None:
            raise MaskNotFoundError(f"No {kind} mask for {field.well} site {field.site}")
        return TimedMaskPlane(self._masks, array, kind, self._check_timepoint(array, timepoint))


class TimedCollectionReader(NativeStoreReader):
    """A TIMED plate folder; each field appears as a virtual OME-Zarr group."""

    def _virtual_resource(self, resource_path: str) -> VirtualResource | None:
        return collection_resource(self.dataset.path, resource_path)

    def mask_source(self) -> TimedMaskSource | None:
        folders = plate_folders(self.ctx.timed_masks, self.dataset.path)
        if not folders:
            return None
        return TimedMaskSource(self.ctx.masks, self.dataset.path, folders)
