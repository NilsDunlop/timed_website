"""Metadata-only OME-Zarr inspection and TIMED collection adaptation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .models import (
    AcquisitionSummary,
    AxisSummary,
    ChannelSummary,
    FieldSummary,
    ImageSummary,
    OmeZarrInfo,
    PlateSummary,
    ResolutionLevel,
    WellFieldsResponse,
    WellSummary,
    WindowSummary,
)
from .paths import PathForbiddenError

FIELD_NAME = re.compile(r"^(?P<row>[A-Za-z]+)(?P<column>\d+)_s(?P<site>\d+)\.zarr$")
METADATA_LIMIT = 8 * 1024 * 1024
PIXEL_SIZE_MICROMETERS = 0.3


class OmeZarrError(ValueError):
    """Raised for malformed or unsupported OME-Zarr metadata."""


@dataclass(frozen=True)
class StoreDescriptor:
    """The small amount of metadata needed to classify a store."""

    info: OmeZarrInfo
    layout: Literal["native", "timed-collection", "packed"]
    metadata_prefix: str = ""


@dataclass(frozen=True)
class VirtualResource:
    """In-memory metadata or a mapped path for the TIMED collection facade."""

    content: bytes | None = None
    content_type: str = "application/json"
    mapped_path: str | None = None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > METADATA_LIMIT:
            raise OmeZarrError(f"Metadata file is too large: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OmeZarrError(f"Could not read metadata {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OmeZarrError(f"Metadata must be a JSON object: {path}")
    return value


def _metadata_child(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative:
        raise OmeZarrError(f"Invalid metadata path: {relative!r}")
    logical = PurePosixPath(relative)
    if logical.is_absolute() or ".." in logical.parts:
        raise OmeZarrError(f"Metadata path escapes its store: {relative}")
    candidate = root.joinpath(*[part for part in logical.parts if part not in ("", ".")])
    resolved_root = root.resolve(strict=True)
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(resolved_root):
        raise OmeZarrError(f"Metadata path escapes its store: {relative}")
    return candidate


def _normal_attributes(attributes: Any) -> dict[str, Any]:
    if not isinstance(attributes, dict):
        return {}
    ome = attributes.get("ome")
    if not isinstance(ome, dict):
        return attributes
    merged = {key: value for key, value in attributes.items() if key != "ome"}
    merged.update(ome)
    merged["_ome_namespace"] = True
    return merged


def _group_metadata(path: Path) -> tuple[int, dict[str, Any]]:
    v3 = path / "zarr.json"
    if v3.is_file():
        metadata = _read_json(v3)
        if metadata.get("node_type") != "group":
            raise OmeZarrError(f"Not a Zarr group: {path}")
        return 3, _normal_attributes(metadata.get("attributes"))
    v2_group = path / ".zgroup"
    v2_attrs = path / ".zattrs"
    if v2_group.is_file() or v2_attrs.is_file():
        group = _read_json(v2_group) if v2_group.is_file() else {}
        attributes = _read_json(v2_attrs) if v2_attrs.is_file() else {}
        return int(group.get("zarr_format", 2)), _normal_attributes(attributes)
    raise OmeZarrError(f"No Zarr group metadata at {path}")


def _array_metadata(path: Path) -> tuple[int, dict[str, Any]]:
    v3 = path / "zarr.json"
    if v3.is_file():
        metadata = _read_json(v3)
        if metadata.get("node_type") != "array":
            raise OmeZarrError(f"Not a Zarr array: {path}")
        return 3, metadata
    v2 = path / ".zarray"
    if v2.is_file():
        return 2, _read_json(v2)
    raise OmeZarrError(f"No Zarr array metadata at {path}")


def _kind(attributes: dict[str, Any]) -> Literal["plate", "well", "image"] | None:
    if isinstance(attributes.get("plate"), dict):
        return "plate"
    if isinstance(attributes.get("well"), dict):
        return "well"
    if isinstance(attributes.get("multiscales"), list):
        return "image"
    return None


def _version(attributes: dict[str, Any], zarr_format: int) -> str:
    value = attributes.get("version")
    if value is not None and attributes.get("_ome_namespace"):
        return str(value)
    for key in ("plate", "well"):
        section = attributes.get(key)
        if isinstance(section, dict) and section.get("version") is not None:
            return str(section["version"])
    multiscales = attributes.get("multiscales")
    if isinstance(multiscales, list):
        for multiscale in multiscales:
            if isinstance(multiscale, dict) and multiscale.get("version") is not None:
                return str(multiscale["version"])
    return "0.5" if zarr_format == 3 else "0.4"


def _is_array(path: Path) -> bool:
    try:
        _array_metadata(path)
    except OmeZarrError:
        return False
    return True


def _collection_fields(path: Path) -> list[tuple[str, str, int, Path]]:
    fields: list[tuple[str, str, int, Path]] = []
    try:
        resolved_path = path.resolve(strict=True)
    except OSError:
        return fields
    try:
        children = sorted(path.iterdir(), key=lambda child: child.name.casefold())
    except OSError:
        return fields
    for child in children:
        match = FIELD_NAME.fullmatch(child.name)
        if match is None or not child.is_dir():
            continue
        try:
            if not child.resolve(strict=True).is_relative_to(resolved_path):
                continue
        except OSError:
            continue
        fields.append(
            (
                match.group("row").upper(),
                match.group("column"),
                int(match.group("site")),
                child,
            )
        )
    return fields


def inspect_store(path: Path) -> StoreDescriptor | None:
    """Classify native OME-Zarr groups and TIMED bare-array collections."""
    if not path.is_dir():
        return None
    try:
        zarr_format, attributes = _group_metadata(path)
    except OmeZarrError:
        pass
    else:
        kind = _kind(attributes)
        if kind is not None:
            return StoreDescriptor(
                OmeZarrInfo(
                    kind=kind,
                    version=_version(attributes, zarr_format),
                    zarr_format=zarr_format,
                ),
                "native",
            )

        # bioformats2raw roots may keep the image group below OME/.
        if "bioformats2raw.layout" in attributes and (path / "OME").is_dir():
            nested = inspect_store(path / "OME")
            if nested is not None and nested.layout == "native":
                return StoreDescriptor(nested.info, "native", metadata_prefix="OME")

    fields = _collection_fields(path)
    if fields and _is_array(fields[0][3]):
        return StoreDescriptor(
            OmeZarrInfo(kind="plate", version="0.5", zarr_format=3),
            "timed-collection",
        )
    return None


def _axes(values: Any) -> list[AxisSummary]:
    if not isinstance(values, list):
        return []
    result: list[AxisSummary] = []
    for value in values:
        if isinstance(value, str):
            result.append(AxisSummary(name=value))
        elif isinstance(value, dict) and value.get("name") is not None:
            result.append(
                AxisSummary(
                    name=str(value["name"]),
                    type=str(value["type"]) if value.get("type") is not None else None,
                    unit=str(value["unit"]) if value.get("unit") is not None else None,
                )
            )
    return result


def _channels(omero: Any) -> list[ChannelSummary]:
    if not isinstance(omero, dict) or not isinstance(omero.get("channels"), list):
        return []
    result: list[ChannelSummary] = []
    for index, value in enumerate(omero["channels"]):
        if not isinstance(value, dict):
            continue
        raw_window = value.get("window")
        window = None
        if isinstance(raw_window, dict):
            window = WindowSummary(
                min=raw_window.get("min"),
                max=raw_window.get("max"),
                start=raw_window.get("start"),
                end=raw_window.get("end"),
            )
        result.append(
            ChannelSummary(
                name=str(value.get("label", f"Channel {index + 1}")),
                color=str(value["color"]) if value.get("color") is not None else None,
                window=window,
            )
        )
    return result


def _sharding_shape(metadata: dict[str, Any]) -> tuple[list[int], list[int] | None]:
    grid = metadata.get("chunk_grid")
    configuration = grid.get("configuration", {}) if isinstance(grid, dict) else {}
    outer = configuration.get("chunk_shape") if isinstance(configuration, dict) else None
    chunks = list(outer) if isinstance(outer, list) else []
    codecs = metadata.get("codecs")
    if isinstance(codecs, list):
        for codec in codecs:
            if not isinstance(codec, dict) or codec.get("name") != "sharding_indexed":
                continue
            shard = list(chunks)
            codec_configuration = codec.get("configuration")
            if isinstance(codec_configuration, dict):
                inner = codec_configuration.get("chunk_shape")
                if isinstance(inner, list):
                    chunks = list(inner)
            return chunks, shard
    return chunks, None


def _level(path: Path, relative: str, transformations: Any) -> ResolutionLevel:
    zarr_format, metadata = _array_metadata(path)
    if zarr_format == 3:
        chunks, shard_shape = _sharding_shape(metadata)
        dtype = metadata.get("data_type", metadata.get("dtype"))
    else:
        chunks = list(metadata.get("chunks", []))
        shard_shape = None
        dtype = metadata.get("dtype")
    return ResolutionLevel(
        path=relative,
        shape=[int(value) for value in metadata.get("shape", [])],
        chunks=[int(value) for value in chunks],
        shard_shape=[int(value) for value in shard_shape] if shard_shape else None,
        dtype=str(dtype),
        coordinate_transformations=transformations if isinstance(transformations, list) else [],
    )


def native_image_summary(store: Path, image_path: str = "") -> ImageSummary:
    image_root = _metadata_child(store, image_path) if image_path else store
    zarr_format, attributes = _group_metadata(image_root)
    multiscales = attributes.get("multiscales")
    if not isinstance(multiscales, list) or not multiscales or not isinstance(multiscales[0], dict):
        raise OmeZarrError(f"Image has no multiscales metadata: {image_root}")
    multiscale = multiscales[0]
    datasets = multiscale.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise OmeZarrError(f"Image has no resolution levels: {image_root}")
    levels: list[ResolutionLevel] = []
    for dataset in datasets:
        if not isinstance(dataset, dict) or not isinstance(dataset.get("path"), str):
            raise OmeZarrError(f"Invalid multiscale dataset in {image_root}")
        level_path = dataset["path"]
        levels.append(
            _level(
                _metadata_child(image_root, level_path),
                level_path,
                dataset.get("coordinateTransformations"),
            )
        )
    return ImageSummary(
        name=str(multiscale.get("name") or image_root.name),
        path=image_path,
        axes=_axes(multiscale.get("axes")),
        levels=levels,
        channels=_channels(attributes.get("omero")),
        ome_zarr_version=_version(attributes, zarr_format),
        zarr_format=zarr_format,
    )


def _bare_image_summary(store: Path, field: Path) -> ImageSummary:
    _, metadata = _array_metadata(field)
    raw_attributes = metadata.get("attributes")
    timed = raw_attributes.get("timed", {}) if isinstance(raw_attributes, dict) else {}
    axes = timed.get("axes") if isinstance(timed, dict) else None
    wavelengths = timed.get("channels_nm") if isinstance(timed, dict) else None
    chunks, shard_shape = _sharding_shape(metadata)
    channels = []
    colors = ("0000FF", "00FF00", "FF0000")
    if isinstance(wavelengths, list):
        channels = [
            ChannelSummary(
                name=f"{wavelength} nm",
                color=colors[index % len(colors)],
                window=WindowSummary(min=0, max=65535, start=100, end=8000),
            )
            for index, wavelength in enumerate(wavelengths)
        ]
    transformations = [
        {
            "type": "scale",
            "scale": [1.0, 1.0, PIXEL_SIZE_MICROMETERS, PIXEL_SIZE_MICROMETERS],
        }
    ]
    axis_types = {
        "t": ("time", "hour"),
        "c": ("channel", None),
        "z": ("space", "micrometer"),
        "y": ("space", "micrometer"),
        "x": ("space", "micrometer"),
    }
    image_axes = []
    if isinstance(axes, list):
        for axis in axes:
            name = str(axis)
            axis_type, unit = axis_types.get(name, (None, None))
            image_axes.append(AxisSummary(name=name, type=axis_type, unit=unit))
    return ImageSummary(
        name=field.stem,
        path=field.name,
        axes=image_axes,
        levels=[
            ResolutionLevel(
                path="s0",
                shape=[int(value) for value in metadata.get("shape", [])],
                chunks=[int(value) for value in chunks],
                shard_shape=[int(value) for value in shard_shape] if shard_shape else None,
                dtype=str(metadata.get("data_type")),
                coordinate_transformations=transformations,
            )
        ],
        channels=channels,
        ome_zarr_version="0.5",
        zarr_format=3,
    )


def _native_well_fields(store: Path, well_path: str) -> WellFieldsResponse:
    well_root = _metadata_child(store, well_path)
    _, attributes = _group_metadata(well_root)
    well = attributes.get("well")
    if not isinstance(well, dict) or not isinstance(well.get("images"), list):
        raise OmeZarrError(f"Not an OME-Zarr well: {well_path}")
    fields: list[FieldSummary] = []
    for image in well["images"]:
        if not isinstance(image, dict) or not isinstance(image.get("path"), str):
            continue
        relative = str(PurePosixPath(well_path) / PurePosixPath(image["path"]))
        _metadata_child(store, relative)
        fields.append(FieldSummary(path=relative, acquisition=image.get("acquisition")))
    return WellFieldsResponse(well_path=well_path, fields=fields)


def well_fields(store: Path, descriptor: StoreDescriptor, well_path: str) -> WellFieldsResponse:
    if descriptor.layout == "native":
        prefix = descriptor.metadata_prefix
        native_path = str(PurePosixPath(prefix) / well_path) if prefix else well_path
        response = _native_well_fields(store, native_path)
        return WellFieldsResponse(well_path=well_path, fields=response.fields)

    if "/" in well_path or "\\" in well_path or ".." in PurePosixPath(well_path).parts:
        raise PathForbiddenError("Invalid TIMED well path")
    matches = []
    for row, column, _site, field in _collection_fields(store):
        token = f"{row}{column}"
        if token.casefold() == well_path.casefold():
            matches.append(FieldSummary(path=field.name))
    if not matches:
        raise OmeZarrError(f"Unknown well: {well_path}")
    return WellFieldsResponse(well_path=well_path, fields=matches)


def _native_plate_summary(store: Path, descriptor: StoreDescriptor) -> PlateSummary:
    plate_root = (
        _metadata_child(store, descriptor.metadata_prefix) if descriptor.metadata_prefix else store
    )
    zarr_format, attributes = _group_metadata(plate_root)
    plate = attributes.get("plate")
    if not isinstance(plate, dict):
        raise OmeZarrError(f"Not an OME-Zarr plate: {store}")
    rows = [
        str(row.get("name", index))
        for index, row in enumerate(plate.get("rows", []))
        if isinstance(row, dict)
    ]
    columns = [
        str(column.get("name", index))
        for index, column in enumerate(plate.get("columns", []))
        if isinstance(column, dict)
    ]
    acquisitions = [
        AcquisitionSummary(id=value.get("id"), name=value.get("name"))
        for value in plate.get("acquisitions", [])
        if isinstance(value, dict)
    ]
    wells: list[WellSummary] = []
    for value in plate.get("wells", []):
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            continue
        row_index = value.get("rowIndex")
        column_index = value.get("columnIndex")
        row = (
            rows[row_index]
            if isinstance(row_index, int) and row_index < len(rows)
            else str(row_index)
        )
        column = (
            columns[column_index]
            if isinstance(column_index, int) and column_index < len(columns)
            else str(column_index)
        )
        wells.append(WellSummary(row=row, column=column, path=value["path"]))
    first_image = None
    if wells:
        prefixed_well = (
            str(PurePosixPath(descriptor.metadata_prefix) / wells[0].path)
            if descriptor.metadata_prefix
            else wells[0].path
        )
        fields = _native_well_fields(store, prefixed_well)
        if fields.fields:
            first_image = native_image_summary(store, fields.fields[0].path)
    return PlateSummary(
        name=str(plate.get("name") or store.name),
        path="",
        rows=rows,
        columns=columns,
        acquisitions=acquisitions,
        wells=wells,
        first_image=first_image,
        ome_zarr_version=_version(attributes, zarr_format),
        zarr_format=zarr_format,
    )


def _collection_plate_summary(store: Path) -> PlateSummary:
    fields = _collection_fields(store)
    if not fields:
        raise OmeZarrError(f"TIMED collection has no fields: {store}")
    seen: set[tuple[str, str]] = set()
    wells: list[WellSummary] = []
    for row, column, _site, _field in fields:
        key = (row, column)
        if key in seen:
            continue
        seen.add(key)
        wells.append(WellSummary(row=row, column=str(int(column)), path=f"{row}{column}"))
    rows = list(dict.fromkeys(well.row for well in wells))
    columns = sorted({well.column for well in wells}, key=lambda value: int(value))
    return PlateSummary(
        name=store.name,
        path="",
        rows=rows,
        columns=columns,
        acquisitions=[],
        wells=wells,
        first_image=_bare_image_summary(store, fields[0][3]),
        ome_zarr_version="0.5",
        zarr_format=3,
    )


def dataset_summary(store: Path, descriptor: StoreDescriptor) -> ImageSummary | PlateSummary:
    if descriptor.layout == "timed-collection":
        return _collection_plate_summary(store)
    if descriptor.info.kind == "plate":
        return _native_plate_summary(store, descriptor)
    if descriptor.info.kind == "image":
        return native_image_summary(store, descriptor.metadata_prefix)
    raise OmeZarrError("Well groups cannot be linked as datasets")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def collection_resource(store: Path, relative: str) -> VirtualResource | None:
    """Map a facade request to virtual OME metadata or unchanged shard bytes."""
    parts = PurePosixPath(relative).parts
    if not parts:
        return None
    match = FIELD_NAME.fullmatch(parts[0])
    if match is None or not (store / parts[0]).is_dir():
        return None
    field = store / parts[0]
    if len(parts) == 2 and parts[1] == "zarr.json":
        image = _bare_image_summary(store, field)
        metadata = {
            "attributes": {
                "ome": {
                    "version": "0.5",
                    "multiscales": [
                        {
                            "name": image.name,
                            "axes": [axis.model_dump(exclude_none=True) for axis in image.axes],
                            "datasets": [
                                {
                                    "path": "s0",
                                    "coordinateTransformations": (
                                        image.levels[0].coordinate_transformations
                                    ),
                                }
                            ],
                        }
                    ],
                    "omero": {
                        "channels": [
                            {
                                "label": channel.name,
                                "color": channel.color,
                                "window": (
                                    channel.window.model_dump(exclude_none=True)
                                    if channel.window
                                    else None
                                ),
                            }
                            for channel in image.channels
                        ]
                    },
                }
            },
            "zarr_format": 3,
            "node_type": "group",
        }
        return VirtualResource(content=_json_bytes(metadata))
    if len(parts) == 3 and parts[1:] == ("s0", "zarr.json"):
        metadata = _read_json(field / "zarr.json")
        raw_attributes = metadata.get("attributes")
        timed = raw_attributes.get("timed", {}) if isinstance(raw_attributes, dict) else {}
        if isinstance(timed, dict) and isinstance(timed.get("axes"), list):
            metadata["dimension_names"] = timed["axes"]
        return VirtualResource(content=_json_bytes(metadata))
    if len(parts) >= 3 and parts[1] == "s0":
        mapped = str(PurePosixPath(parts[0]).joinpath(*parts[2:]))
        return VirtualResource(mapped_path=mapped, content_type="application/octet-stream")
    return None
