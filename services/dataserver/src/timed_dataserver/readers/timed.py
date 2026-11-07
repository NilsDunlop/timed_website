"""Reader for TIMED bare-array collections: the virtual OME-Zarr field facade and masks.

A collection with a configured mask folder (``serve --timed-masks``) offers the
models pinned below on every field whose mirrored folder holds them. TIMED
masks cover single fields, so only a field listing reports them: a plate
listing lists none, as it did before TIMED had masks, and so does a field
without masks. They have one plane per image timepoint, so every count and
outline names its timepoint, and cache keys and ETags include it together
with the mask files' identities.
"""

from __future__ import annotations

import hashlib
import json
import re
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
from ..timed_mask_config import mirrored_plate
from ..timed_masks import TimedMaskArray, has_array, read_plane
from .base import MaskField
from .native import NativeStoreReader

_WELL = re.compile(r"[A-Z]+[0-9]+")


@dataclass(frozen=True, slots=True)
class TimedMaskModel:
    """A mask model pinned in code: the field array that holds it and its kind."""

    tag: str
    array: str
    kind: str
    recipe: str


TIMED_MASK_MODELS = (
    TimedMaskModel(
        tag="masks_raw",
        array="masks_raw",
        kind="cells",
        recipe="Cellpose-SAM · raw 505 + 625 nm",
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
    """The pinned mask models of one TIMED plate, read from its mirrored folder."""

    def __init__(
        self,
        masks: MaskReader,
        plate: Path,
        masks_plate: Path,
        masks_root: Path,
    ) -> None:
        self._masks = masks
        self._plate = plate
        self._masks_plate = masks_plate
        self._masks_root = masks_root

    def field(self, well: str, site: int) -> MaskField:
        canonical = well.upper()
        if _WELL.fullmatch(canonical) is None or site < 0:
            raise MaskNotFoundError(f"TIMED field does not exist: {canonical} site {site}")
        return MaskField(canonical, site)

    @staticmethod
    def _folder(field: MaskField) -> str:
        return f"{field.well}_s{field.site}.zarr"

    def _array_dir(self, model: TimedMaskModel, field: MaskField) -> Path:
        return self._masks_plate / self._folder(field) / model.array

    def _image_field(self, field: MaskField) -> Path | None:
        try:
            plate = self._plate.resolve(strict=True)
            resolved = (self._plate / self._folder(field)).resolve(strict=True)
        except OSError:
            return None
        if resolved == plate or not resolved.is_relative_to(plate) or not resolved.is_dir():
            return None
        return resolved

    def _covers(self, model: TimedMaskModel, field: MaskField) -> bool:
        return self._image_field(field) is not None and has_array(
            self._array_dir(model, field), self._masks_root
        )

    def models(self, field: MaskField | None = None) -> list[MaskModelEntry]:
        if field is None:
            return []
        return [
            MaskModelEntry(
                model=MaskModel(
                    tag=model.tag,
                    recipe=model.recipe,
                    kinds=(model.kind,),
                    frozen=True,
                    created_at=None,
                    directory=self._masks_plate,
                ),
                available=True,
                default=position == 0,
            )
            for position, model in enumerate(TIMED_MASK_MODELS)
            if self._covers(model, field)
        ]

    @staticmethod
    def _model(model_tag: str) -> TimedMaskModel:
        for model in TIMED_MASK_MODELS:
            if model.tag == model_tag:
                return model
        raise MaskNotFoundError(f"Unknown mask model: {model_tag}")

    @staticmethod
    def _require_timepoint(t: int | None) -> int:
        if t is None:
            raise MaskRequestError("TIMED masks have one plane per timepoint: give t")
        return t

    def _array(self, model: TimedMaskModel, field: MaskField) -> TimedMaskArray:
        missing = f"TIMED field does not exist: {field.well} site {field.site}"
        image = self._image_field(field)
        if image is None:
            raise MaskNotFoundError(missing)
        try:
            shape, t_indices = timed_field_geometry(image)
        except OmeZarrError:
            raise MaskNotFoundError(missing) from None
        array = self._masks.timed_arrays.get(
            f"{model.tag}/{self._masks_plate.name}/{self._folder(field)}",
            self._array_dir(model, field),
            self._masks_root,
            shape,
            t_indices,
        )
        if array is None:
            raise MaskNotFoundError(f"No masks for {field.well} site {field.site}")
        return array

    def _plane(self, model: TimedMaskModel, field: MaskField, t: int) -> TimedMaskPlane:
        array = self._array(model, field)
        if t >= array.timepoints:
            raise MaskRequestError(
                f"Timepoint {t} is outside this field's {array.timepoints} timepoints "
                f"(0 to {array.timepoints - 1})"
            )
        return TimedMaskPlane(self._masks, array, model.kind, t)

    def counts(self, model_tag: str, field: MaskField, t: int | None = None) -> dict[str, int]:
        model = self._model(model_tag)
        timepoint = self._require_timepoint(t)
        return {model.kind: self._plane(model, field, timepoint).labels().count}

    def open_plane(
        self,
        model_tag: str,
        field: MaskField,
        kind: str,
        t: int | None = None,
    ) -> TimedMaskPlane:
        model = self._model(model_tag)
        if kind != model.kind:
            raise MaskNotFoundError(f"No {kind} mask for {field.well} site {field.site}")
        return self._plane(model, field, self._require_timepoint(t))


class TimedCollectionReader(NativeStoreReader):
    """A TIMED plate folder; each field appears as a virtual OME-Zarr group."""

    def _virtual_resource(self, resource_path: str) -> VirtualResource | None:
        return collection_resource(self.dataset.path, resource_path)

    def mask_source(self) -> TimedMaskSource | None:
        located = mirrored_plate(self.ctx.timed_masks, self.dataset.path)
        if located is None:
            return None
        mapping, masks_plate = located
        return TimedMaskSource(self.ctx.masks, self.dataset.path, masks_plate, mapping.masks_root)
