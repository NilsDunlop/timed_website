"""Pure OME-Zarr facade metadata and routing for packed image plates."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Literal

from .models import (
    AxisSummary,
    ChannelSummary,
    FieldSummary,
    ImageSummary,
    PlateSummary,
    ResolutionLevel,
    WellFieldsResponse,
    WellSummary,
    WindowSummary,
)
from .packed import PackedMember, PackedPlate, PackedVirtualPath
from .packed_profiles import PackedDatasetProfile

_WELL_PATTERN = re.compile(r"(?P<row>[A-Za-z]+)(?P<column>[0-9]+)")
_FIELD_PATTERN = re.compile(r"(?P<well>[A-Za-z]+[0-9]+)_s(?P<site>0|[1-9][0-9]*)\.zarr")
_INDEX_PATTERN = re.compile(r"0|[1-9][0-9]*")


class PackedFacadeNotFoundError(LookupError):
    """Raised when a path has no resource in a packed plate facade."""


@dataclass(frozen=True, slots=True)
class PackedFieldRequest:
    """A catalogue-backed field addressed through the facade."""

    experiment: str
    plate: int
    well: str
    site: int

    @property
    def name(self) -> str:
        return f"{self.well}_s{self.site}"

    @property
    def path(self) -> str:
        return f"{self.name}.zarr"


@dataclass(frozen=True, slots=True)
class PackedMetadataRequest:
    """An exact, in-memory metadata response for one packed field."""

    field: PackedFieldRequest
    kind: Literal["group", "array"]
    content: bytes


@dataclass(frozen=True, slots=True)
class PackedChunkRequest:
    """A validated request for one encoded facade chunk."""

    field: PackedFieldRequest
    channel_index: int
    channel: int
    chunk_y: int
    chunk_x: int
    y_slice: slice
    x_slice: slice
    member: PackedMember


type PackedResourceRequest = PackedMetadataRequest | PackedChunkRequest


def _not_found(detail: str) -> PackedFacadeNotFoundError:
    return PackedFacadeNotFoundError(detail)


def _plate_context(packed: PackedVirtualPath) -> tuple[str, int, PackedPlate]:
    experiment = packed.experiment
    plate = packed.plate
    if experiment is None or plate is None:
        raise _not_found("Packed facade resources require a plate")
    plate_info = packed.catalogue.plate(experiment, plate)
    if plate_info is None:
        raise _not_found("Packed plate is not present in the catalogue")
    return experiment, plate, plate_info


def _check_profile(packed: PackedVirtualPath, profile: PackedDatasetProfile) -> None:
    if (
        packed.index_path.name != profile.index_filename
        or packed.catalogue.row_count != profile.row_count
    ):
        raise _not_found("Packed profile does not match the index")


def _cell_line(profile: PackedDatasetProfile, experiment: str) -> str:
    cell_line = profile.cell_line_for(experiment)
    if cell_line is None:
        raise _not_found(f"Packed profile has no cell line for {experiment}")
    return cell_line


def _summary_channels(
    profile: PackedDatasetProfile,
    experiment: str,
) -> list[ChannelSummary]:
    cell_line = _cell_line(profile, experiment)
    result: list[ChannelSummary] = []
    for channel in profile.channels:
        window = channel.window_for(cell_line)
        if window is None:
            raise _not_found(
                f"Packed profile has no {cell_line} window for channel {channel.index}"
            )
        result.append(
            ChannelSummary(
                name=channel.label,
                color=channel.color,
                active=channel.active,
                window=WindowSummary(
                    min=window.min,
                    max=window.max,
                    start=window.start,
                    end=window.end,
                ),
            )
        )
    return result


def _well_parts(well: str, profile: PackedDatasetProfile) -> tuple[str, int]:
    match = _WELL_PATTERN.fullmatch(well)
    if match is None:
        raise _not_found(f"Packed catalogue has an invalid well: {well}")
    row = match.group("row")
    try:
        column = int(match.group("column"))
    except ValueError as exc:
        raise _not_found(f"Packed catalogue has an invalid well: {well}") from exc
    if row not in profile.geometry.plate_rows or column not in profile.geometry.plate_columns:
        raise _not_found(f"Packed well is outside the profile plate: {well}")
    return row, column


def _ordered_wells(plate: PackedPlate, profile: PackedDatasetProfile) -> list[str]:
    row_order = {row: index for index, row in enumerate(profile.geometry.plate_rows)}
    parsed = [(well, *_well_parts(well, profile)) for well in plate.wells]
    parsed.sort(key=lambda value: (row_order[value[1]], value[2], value[0]))
    return [well for well, _row, _column in parsed]


def _field_sites(packed: PackedVirtualPath, well: str) -> list[int]:
    experiment, plate, plate_info = _plate_context(packed)
    if well not in plate_info.wells:
        raise _not_found(f"Unknown packed well: {well}")
    sites = sorted(
        site
        for candidate_experiment, candidate_plate, candidate_well, site in packed.catalogue.fields
        if candidate_experiment == experiment
        and candidate_plate == plate
        and candidate_well == well
    )
    if not sites:
        raise _not_found(f"Packed well has no fields: {well}")
    return sites


def _image_summary(
    profile: PackedDatasetProfile,
    experiment: str,
    field: PackedFieldRequest,
) -> ImageSummary:
    height, width = profile.geometry.image_shape
    chunk_height, chunk_width = profile.geometry.chunk_shape
    return ImageSummary(
        name=field.name,
        path=field.path,
        axes=[
            AxisSummary(name="c", type="channel"),
            AxisSummary(name="y", type="space"),
            AxisSummary(name="x", type="space"),
        ],
        levels=[
            ResolutionLevel(
                path="s0",
                shape=[len(profile.channels), height, width],
                chunks=[1, chunk_height, chunk_width],
                dtype=profile.dtype,
                coordinate_transformations=[{"type": "scale", "scale": [1, 1, 1]}],
            )
        ],
        channels=_summary_channels(profile, experiment),
        ome_zarr_version="0.5",
        zarr_format=3,
    )


def packed_plate_summary(
    packed: PackedVirtualPath,
    profile: PackedDatasetProfile,
) -> PlateSummary:
    """Build the exact sparse-well plate summary for a profiled packed plate."""
    experiment, plate, plate_info = _plate_context(packed)
    _check_profile(packed, profile)
    ordered_wells = _ordered_wells(plate_info, profile)
    wells = []
    for well in ordered_wells:
        row, column = _well_parts(well, profile)
        wells.append(WellSummary(row=row, column=str(column), path=well))
    if not ordered_wells:
        raise _not_found("Packed plate has no wells")
    first_well = ordered_wells[0]
    first_site = _field_sites(packed, first_well)[0]
    first_field = PackedFieldRequest(experiment, plate, first_well, first_site)
    return PlateSummary(
        name=profile.plate_name(plate),
        path="",
        rows=list(profile.geometry.plate_rows),
        columns=[str(column) for column in profile.geometry.plate_columns],
        acquisitions=[],
        wells=wells,
        first_image=_image_summary(profile, experiment, first_field),
        ome_zarr_version="0.5",
        zarr_format=3,
    )


def packed_well_fields(packed: PackedVirtualPath, well: str) -> WellFieldsResponse:
    """List only the sites actually indexed for *well*."""
    sites = _field_sites(packed, well)
    return WellFieldsResponse(
        well_path=well,
        fields=[FieldSummary(path=f"{well}_s{site}.zarr") for site in sites],
    )


def _json_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def packed_group_metadata_bytes(
    profile: PackedDatasetProfile,
    experiment: str,
    field_name: str,
) -> bytes:
    """Return exact OME-Zarr 0.5 group metadata for one facade field."""
    cell_line = _cell_line(profile, experiment)
    channels: list[dict[str, object]] = []
    for channel in profile.channels:
        window = channel.window_for(cell_line)
        if window is None:
            raise _not_found(
                f"Packed profile has no {cell_line} window for channel {channel.index}"
            )
        channels.append(
            {
                "label": channel.label,
                "color": channel.color,
                "active": channel.active,
                "window": {
                    "min": window.min,
                    "max": window.max,
                    "start": window.start,
                    "end": window.end,
                },
            }
        )
    metadata: dict[str, object] = {
        "attributes": {
            "ome": {
                "version": "0.5",
                "multiscales": [
                    {
                        "name": field_name,
                        "axes": [
                            {"name": "c", "type": "channel"},
                            {"name": "y", "type": "space"},
                            {"name": "x", "type": "space"},
                        ],
                        "datasets": [
                            {
                                "path": "s0",
                                "coordinateTransformations": [
                                    {"type": "scale", "scale": [1, 1, 1]}
                                ],
                            }
                        ],
                    }
                ],
                "omero": {"channels": channels},
            }
        },
        "zarr_format": 3,
        "node_type": "group",
    }
    return _json_bytes(metadata)


def packed_array_metadata_bytes(profile: PackedDatasetProfile) -> bytes:
    """Return exact Zarr v3 array metadata for the packed facade's only level."""
    height, width = profile.geometry.image_shape
    chunk_height, chunk_width = profile.geometry.chunk_shape
    codec = profile.codec
    metadata: dict[str, object] = {
        "zarr_format": 3,
        "node_type": "array",
        "shape": [len(profile.channels), height, width],
        "data_type": profile.dtype,
        "chunk_grid": {
            "name": "regular",
            "configuration": {"chunk_shape": [1, chunk_height, chunk_width]},
        },
        "chunk_key_encoding": {
            "name": "default",
            "configuration": {"separator": "/"},
        },
        "fill_value": 0,
        "codecs": [
            {"name": "bytes", "configuration": {"endian": "little"}},
            {
                "name": "blosc",
                "configuration": {
                    "typesize": codec.typesize,
                    "cname": codec.cname,
                    "clevel": codec.clevel,
                    "shuffle": codec.shuffle,
                    "blocksize": codec.blocksize,
                },
            },
        ],
        "dimension_names": ["c", "y", "x"],
        "attributes": {},
    }
    return _json_bytes(metadata)


def _parse_index(value: str, label: str) -> int:
    if _INDEX_PATTERN.fullmatch(value) is None:
        raise _not_found(f"Invalid packed {label}: {value}")
    try:
        return int(value)
    except ValueError as exc:
        raise _not_found(f"Invalid packed {label}: {value}") from exc


def _parse_field(
    packed: PackedVirtualPath,
    value: str,
) -> tuple[PackedFieldRequest, dict[int, PackedMember]]:
    match = _FIELD_PATTERN.fullmatch(value)
    if match is None:
        raise _not_found(f"Invalid packed field: {value}")
    experiment, plate, _plate_info = _plate_context(packed)
    well = match.group("well")
    site = _parse_index(match.group("site"), "site")
    members = packed.catalogue.field(experiment, plate, well, site)
    if members is None:
        raise _not_found(f"Unknown packed field: {value}")
    return PackedFieldRequest(experiment, plate, well, site), dict(members)


def parse_packed_resource(
    packed: PackedVirtualPath,
    profile: PackedDatasetProfile,
    relative: str,
) -> PackedResourceRequest:
    """Parse and validate one exact metadata or chunk key under a packed plate."""
    _plate_context(packed)
    _check_profile(packed, profile)
    if (
        not relative
        or "\\" in relative
        or "\x00" in relative
        or relative.startswith("/")
        or relative.endswith("/")
    ):
        raise _not_found(f"Unknown packed facade resource: {relative}")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _not_found(f"Unknown packed facade resource: {relative}")
    field, members = _parse_field(packed, parts[0])

    if parts[1:] == ["zarr.json"]:
        return PackedMetadataRequest(
            field=field,
            kind="group",
            content=packed_group_metadata_bytes(profile, field.experiment, field.name),
        )
    if parts[1:] == ["s0", "zarr.json"]:
        return PackedMetadataRequest(
            field=field,
            kind="array",
            content=packed_array_metadata_bytes(profile),
        )
    if len(parts) != 6 or parts[1:3] != ["s0", "c"]:
        raise _not_found(f"Unknown packed facade resource: {relative}")

    channel_index = _parse_index(parts[3], "channel index")
    chunk_y = _parse_index(parts[4], "chunk row")
    chunk_x = _parse_index(parts[5], "chunk column")
    channel = channel_index + 1
    if channel_index >= len(profile.channels) or profile.channel(channel) is None:
        raise _not_found(f"Packed channel index is out of range: {channel_index}")
    member = members.get(channel)
    if member is None:
        raise _not_found(f"Packed field has no channel {channel}")

    height, width = profile.geometry.image_shape
    chunk_height, chunk_width = profile.geometry.chunk_shape
    rows = math.ceil(height / chunk_height)
    columns = math.ceil(width / chunk_width)
    if chunk_y >= rows or chunk_x >= columns:
        raise _not_found(f"Packed chunk is out of range: {chunk_y}/{chunk_x}")
    y_start = chunk_y * chunk_height
    x_start = chunk_x * chunk_width
    return PackedChunkRequest(
        field=field,
        channel_index=channel_index,
        channel=channel,
        chunk_y=chunk_y,
        chunk_x=chunk_x,
        y_slice=slice(y_start, min(y_start + chunk_height, height)),
        x_slice=slice(x_start, min(x_start + chunk_width, width)),
        member=member,
    )
