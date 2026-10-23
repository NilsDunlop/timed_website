"""Pinned and inferred profiles for packed microscopy indexes."""

from __future__ import annotations

import io
import os
import re
import stat
import tarfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from .packed import PackedCatalogue, PackedMember

MAX_PACKED_MEMBER_BYTES = 64 * 1024 * 1024
MAX_PACKED_FIELD_BYTES = 256 * 1024 * 1024
MAX_PACKED_CHANNELS = 256
MAX_GENERIC_PLATE_AXIS = 4096
MAX_GENERIC_PLATE_CELLS = 65_536
GENERIC_CHUNK_EDGE = 1080
_TAR_BLOCK_SIZE = 512
_WELL_PATTERN = re.compile(r"(?P<row>[A-Za-z]+)(?P<column>[0-9]+)")
_OS_PREAD = getattr(os, "pread", None)


class PackedProfileError(ValueError):
    """Raised when a safe generic profile cannot be inferred."""


@dataclass(frozen=True, slots=True)
class DisplayWindow:
    """OMERO display bounds for one channel and cell line."""

    cell_line: str
    min: int
    max: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class PackedChannel:
    """One indexed image channel and its display defaults."""

    index: int
    label: str
    color: str | None
    active: bool
    windows: tuple[DisplayWindow, ...]

    def window_for(self, cell_line: str) -> DisplayWindow | None:
        return next((window for window in self.windows if window.cell_line == cell_line), None)


@dataclass(frozen=True, slots=True)
class PackedGeometry:
    """Image, chunk, site, and plate geometry for a packed dataset."""

    image_shape: tuple[int, int]
    chunk_shape: tuple[int, int]
    sites: tuple[int, ...]
    plate_rows: tuple[str, ...]
    plate_columns: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PackedCodec:
    """Pinned Zarr chunk codec settings for a packed dataset."""

    cname: str = "zstd"
    clevel: int = 3
    shuffle: str = "shuffle"
    blocksize: int = 0
    typesize: int = 2


@dataclass(frozen=True, slots=True)
class PackedDatasetProfile:
    """Immutable description of a packed index and its facade defaults."""

    index_filename: str
    row_count: int
    geometry: PackedGeometry
    channels: tuple[PackedChannel, ...]
    experiment_cell_lines: tuple[tuple[str, str], ...]
    metadata_path: str | None
    codec: PackedCodec = PackedCodec()
    dtype: str = "uint16"
    plate_prefix: str = "SQ000"
    member_prefix: str = "idr0080/raw/"
    auto_contrast: bool = False

    def cell_line_for(self, experiment: str) -> str | None:
        return next(
            (
                cell_line
                for candidate, cell_line in self.experiment_cell_lines
                if candidate == experiment
            ),
            None,
        )

    def channel(self, index: int) -> PackedChannel | None:
        return next((channel for channel in self.channels if channel.index == index), None)

    def plate_name(self, plate: int) -> str:
        return f"{self.plate_prefix}{plate}"


@dataclass(frozen=True, slots=True)
class PackedImageSpec:
    """TIFF facts that cannot be inferred from index columns alone."""

    image_shape: tuple[int, int]
    dtype: str
    member_prefix: str


@dataclass(frozen=True, slots=True)
class PackedProbeSignature:
    """Revision of the member whose TIFF facts define a generic profile."""

    tar: Path
    device: int
    inode: int
    mtime_ns: int
    size: int
    offset: int
    member_size: int
    relpath: str


def _window(cell_line: str, maximum: int, start: int, end: int) -> DisplayWindow:
    return DisplayWindow(cell_line=cell_line, min=0, max=maximum, start=start, end=end)


_IDR0080_CHANNELS = (
    PackedChannel(
        index=1,
        label="DNA",
        color="0000FF",
        active=True,
        windows=(
            _window("A549", 27_000, 292, 11_552),
            _window("ES2", 27_000, 234, 12_971),
            _window("HCC44", 27_000, 254, 13_104),
        ),
    ),
    PackedChannel(
        index=2,
        label="ER",
        color="00FF00",
        active=True,
        windows=(
            _window("A549", 45_000, 438, 17_554),
            _window("ES2", 45_000, 309, 18_238),
            _window("HCC44", 45_000, 318, 22_196),
        ),
    ),
    PackedChannel(
        index=3,
        label="RNA",
        color="00FFFF",
        active=False,
        windows=(
            _window("A549", 26_000, 554, 9_283),
            _window("ES2", 26_000, 375, 10_793),
            _window("HCC44", 26_000, 497, 12_756),
        ),
    ),
    PackedChannel(
        index=4,
        label="AGP",
        color="FF0000",
        active=True,
        windows=(
            _window("A549", 14_000, 151, 5_035),
            _window("ES2", 14_000, 108, 5_778),
            _window("HCC44", 14_000, 117, 6_968),
        ),
    ),
    PackedChannel(
        index=5,
        label="Mito",
        color="FF00FF",
        active=False,
        windows=(
            _window("A549", 15_000, 324, 4_066),
            _window("ES2", 15_000, 334, 7_120),
            _window("HCC44", 15_000, 394, 5_711),
        ),
    ),
)


IDR0080_PROFILE = PackedDatasetProfile(
    index_filename="idr0080-raw.index.parquet",
    row_count=155_520,
    geometry=PackedGeometry(
        image_shape=(2160, 2160),
        chunk_shape=(1080, 1080),
        sites=tuple(range(1, 10)),
        plate_rows=tuple("ABCDEFGHIJKLMNOP"),
        plate_columns=tuple(range(1, 25)),
    ),
    channels=_IDR0080_CHANNELS,
    experiment_cell_lines=(
        ("idr0080-A549", "A549"),
        ("idr0080-ES2", "ES2"),
        ("idr0080-HCC44", "HCC44"),
    ),
    metadata_path="metadata/metadata_idr0080.csv",
)


IDR0080_FIXTURE_PROFILE = PackedDatasetProfile(
    index_filename="idr0080-raw.index.parquet",
    row_count=150,
    geometry=PackedGeometry(
        image_shape=(64, 64),
        chunk_shape=(32, 32),
        sites=(1, 2),
        plate_rows=tuple("ABCDEFGHIJKLMNOP"),
        plate_columns=tuple(range(1, 25)),
    ),
    channels=tuple(
        PackedChannel(
            index=channel.index,
            label=channel.label,
            color=channel.color,
            active=channel.active,
            windows=channel.windows[:2],
        )
        for channel in _IDR0080_CHANNELS
    ),
    experiment_cell_lines=(
        ("idr0080-A549", "A549"),
        ("idr0080-ES2", "ES2"),
    ),
    metadata_path="metadata/metadata_idr0080.csv",
)


_KNOWN_PROFILES = (IDR0080_FIXTURE_PROFILE, IDR0080_PROFILE)


def profile_for_index(
    filename: str | Path,
    row_count: int,
) -> PackedDatasetProfile | None:
    """Return a pinned profile only when filename and row count both match."""
    name = Path(filename).name
    return next(
        (
            profile
            for profile in _KNOWN_PROFILES
            if profile.index_filename == name and profile.row_count == row_count
        ),
        None,
    )


def _first_member(catalogue: PackedCatalogue) -> PackedMember:
    if not catalogue.fields:
        raise PackedProfileError("Packed catalogue has no fields to probe")
    field_key = min(catalogue.fields)
    members = catalogue.fields[field_key]
    if not members:
        raise PackedProfileError(f"Packed catalogue field has no channels: {field_key}")
    return members[min(members)]


def _read_exact(fd: int, offset: int, size: int, path: Path) -> bytes:
    output = bytearray()
    try:
        if _OS_PREAD is not None:
            while len(output) < size:
                chunk = _OS_PREAD(fd, size - len(output), offset + len(output))
                if not chunk:
                    break
                output.extend(chunk)
        else:
            os.lseek(fd, offset, os.SEEK_SET)
            while len(output) < size:
                chunk = os.read(fd, size - len(output))
                if not chunk:
                    break
                output.extend(chunk)
    except OSError as exc:
        raise PackedProfileError(
            f"Could not read generic packed member from {path}: {exc}"
        ) from exc
    if len(output) != size:
        raise PackedProfileError(
            f"Short generic packed read from {path}: expected {size}, got {len(output)}"
        )
    return bytes(output)


def _same_file(path_stat: os.stat_result, descriptor_stat: os.stat_result) -> bool:
    return (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mtime_ns,
        path_stat.st_size,
    ) == (
        descriptor_stat.st_dev,
        descriptor_stat.st_ino,
        descriptor_stat.st_mtime_ns,
        descriptor_stat.st_size,
    )


def _probe_bytes(member: PackedMember) -> tuple[bytes, bytes]:
    if member.offset < _TAR_BLOCK_SIZE:
        raise PackedProfileError(
            f"Generic packed member offset must be at least {_TAR_BLOCK_SIZE}: {member.offset}"
        )
    if member.size <= 0:
        raise PackedProfileError(f"Generic packed member size must be positive: {member.size}")
    if member.size > MAX_PACKED_MEMBER_BYTES:
        raise PackedProfileError(
            f"Generic packed member exceeds the {MAX_PACKED_MEMBER_BYTES}-byte probe limit: "
            f"{member.size}"
        )

    flags = os.O_RDONLY
    for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, flag_name, 0)
    fd: int | None = None
    try:
        fd = os.open(member.tar, flags)
        descriptor_stat = os.fstat(fd)
        path_stat = os.stat(member.tar, follow_symlinks=False)
        if not stat.S_ISREG(descriptor_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise PackedProfileError(f"Generic packed shard is not a regular file: {member.tar}")
        if not _same_file(path_stat, descriptor_stat):
            raise PackedProfileError(f"Generic packed shard changed while probing: {member.tar}")
        if member.offset + member.size > descriptor_stat.st_size:
            raise PackedProfileError(
                f"Generic packed member exceeds tar bounds: {member.offset} + {member.size} "
                f"> {descriptor_stat.st_size}"
            )
        header = _read_exact(fd, member.offset - _TAR_BLOCK_SIZE, _TAR_BLOCK_SIZE, member.tar)
        payload = _read_exact(fd, member.offset, member.size, member.tar)
        return header, payload
    except PackedProfileError:
        raise
    except OSError as exc:
        raise PackedProfileError(
            f"Could not open generic packed shard {member.tar}: {exc}"
        ) from exc
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)


def _member_prefix(header: bytes, member: PackedMember) -> str:
    try:
        if header[257:265] != tarfile.POSIX_MAGIC:
            raise ValueError("header does not contain POSIX USTAR magic")
        info = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
        if not info.isreg():
            raise ValueError(f"member is not a regular file: {info.name!r}")
        if info.size != member.size:
            raise ValueError(f"member size mismatch: expected {member.size}, got {info.size}")
        if not info.name.endswith(member.relpath):
            raise ValueError(
                f"member name {info.name!r} does not end with relpath {member.relpath!r}"
            )
        prefix = info.name[: -len(member.relpath)]
        if prefix and not prefix.endswith("/"):
            raise ValueError(f"member prefix is not path-delimited: {prefix!r}")
        return prefix
    except (tarfile.HeaderError, UnicodeError, ValueError) as exc:
        raise PackedProfileError(f"Invalid generic packed USTAR header: {exc}") from exc


def _image_spec(payload: bytes, prefix: str) -> PackedImageSpec:
    try:
        with tifffile.TiffFile(io.BytesIO(payload)) as image:
            if len(image.pages) != 1:
                raise PackedProfileError(
                    f"Generic packed TIFF must have one page, got {len(image.pages)}"
                )
            page = image.pages[0]
            if int(page.compression) != int(tifffile.COMPRESSION.NONE):
                raise PackedProfileError(
                    f"Generic packed TIFF must be uncompressed, got {page.compression.name}"
                )
            shape = tuple(int(value) for value in page.shape)
            dtype = np.dtype(page.dtype)
            byte_counts = tuple(int(value) for value in page.databytecounts)
            data_offsets = tuple(int(value) for value in page.dataoffsets)
    except PackedProfileError:
        raise
    except Exception as exc:
        raise PackedProfileError(f"Could not inspect generic packed TIFF: {exc}") from exc
    if len(shape) != 2 or any(value <= 0 for value in shape):
        raise PackedProfileError(f"Generic packed TIFF must be a non-empty 2-D image, got {shape}")
    if dtype.kind not in "uif" or dtype.itemsize <= 0 or dtype.itemsize > 255:
        raise PackedProfileError(f"Generic packed TIFF has unsupported dtype: {dtype}")
    decoded_bytes = shape[0] * shape[1] * dtype.itemsize
    if decoded_bytes > MAX_PACKED_MEMBER_BYTES:
        raise PackedProfileError(
            "Generic packed TIFF decoded plane exceeds the "
            f"{MAX_PACKED_MEMBER_BYTES}-byte limit: {decoded_bytes}"
        )
    if len(byte_counts) != len(data_offsets) or sum(byte_counts) != decoded_bytes:
        raise PackedProfileError(
            "Generic packed TIFF byte counts do not match its decoded plane size"
        )
    if any(
        offset < 0 or count < 0 or offset + count > len(payload)
        for offset, count in zip(data_offsets, byte_counts, strict=True)
    ):
        raise PackedProfileError("Generic packed TIFF data lies outside its member payload")
    return PackedImageSpec(
        image_shape=(shape[0], shape[1]),
        dtype=dtype.name,
        member_prefix=prefix,
    )


def probe_packed_image(catalogue: PackedCatalogue) -> PackedImageSpec:
    """Synchronously inspect the first indexed TIFF for generic profile facts.

    Callers serving async requests must run this bounded, read-only probe in a
    worker thread and cache its result with the catalogue revision.
    """
    member = _first_member(catalogue)
    header, payload = _probe_bytes(member)
    return _image_spec(payload, _member_prefix(header, member))


def packed_probe_signature(catalogue: PackedCatalogue) -> PackedProbeSignature:
    """Return the current filesystem identity of the generic probe member."""
    member = _first_member(catalogue)
    try:
        member_stat = os.stat(member.tar, follow_symlinks=False)
    except OSError as exc:
        raise PackedProfileError(
            f"Could not stat generic packed shard {member.tar}: {exc}"
        ) from exc
    if not stat.S_ISREG(member_stat.st_mode):
        raise PackedProfileError(f"Generic packed shard is not a regular file: {member.tar}")
    return PackedProbeSignature(
        tar=member.tar,
        device=member_stat.st_dev,
        inode=member_stat.st_ino,
        mtime_ns=member_stat.st_mtime_ns,
        size=member_stat.st_size,
        offset=member.offset,
        member_size=member.size,
        relpath=member.relpath,
    )


def _row_order(row: str) -> tuple[int, str, str]:
    number = 0
    for character in row.upper():
        number = number * 26 + ord(character) - ord("A") + 1
    return number, row.casefold(), row


def _row_name(number: int) -> str:
    result = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _catalogue_geometry(
    catalogue: PackedCatalogue,
    image: PackedImageSpec,
) -> tuple[PackedGeometry, tuple[int, ...], tuple[str, ...]]:
    if not catalogue.fields:
        raise PackedProfileError("Packed catalogue has no fields")
    row_numbers: set[int] = set()
    columns: set[int] = set()
    sites: set[int] = set()
    channels: set[int] = set()
    experiments: set[str] = set()
    well_coordinates: dict[tuple[str, int, int, int], str] = {}
    expected_channels: tuple[int, ...] | None = None
    for (experiment, _plate, well, site), members in catalogue.fields.items():
        match = _WELL_PATTERN.fullmatch(well)
        if match is None:
            raise PackedProfileError(f"Cannot infer packed plate geometry from well {well!r}")
        row_number = _row_order(match.group("row"))[0]
        column = int(match.group("column"))
        if row_number <= 0 or column <= 0:
            raise PackedProfileError(f"Packed well must have positive coordinates: {well!r}")
        if row_number > MAX_GENERIC_PLATE_AXIS or column > MAX_GENERIC_PLATE_AXIS:
            raise PackedProfileError(f"Packed well exceeds generic plate limits: {well!r}")
        if _plate < 0 or site < 0:
            raise PackedProfileError(
                f"Packed plate and site identifiers must be non-negative: {_plate}/{site}"
            )
        coordinate = (experiment, _plate, row_number, column)
        existing_well = well_coordinates.setdefault(coordinate, well)
        if existing_well != well:
            raise PackedProfileError(
                f"Packed wells {existing_well!r} and {well!r} use the same coordinates"
            )
        field_channels = tuple(sorted(members))
        if any(channel < 0 for channel in field_channels):
            raise PackedProfileError("Packed channel identifiers must be non-negative")
        if expected_channels is None:
            expected_channels = field_channels
        elif field_channels != expected_channels:
            raise PackedProfileError(
                "Generic packed fields must have one consistent channel set"
            )
        row_numbers.add(row_number)
        columns.add(column)
        sites.add(site)
        channels.update(members)
        experiments.add(experiment)
    if not channels:
        raise PackedProfileError("Packed catalogue has no channels")
    maximum_row = max(row_numbers)
    maximum_column = max(columns)
    if maximum_row * maximum_column > MAX_GENERIC_PLATE_CELLS:
        raise PackedProfileError("Inferred generic plate grid is too large")
    chunk_shape = tuple(min(GENERIC_CHUNK_EDGE, value) for value in image.image_shape)
    geometry = PackedGeometry(
        image_shape=image.image_shape,
        chunk_shape=(chunk_shape[0], chunk_shape[1]),
        sites=tuple(sorted(sites)),
        plate_rows=tuple(_row_name(number) for number in range(1, maximum_row + 1)),
        plate_columns=tuple(range(1, maximum_column + 1)),
    )
    return geometry, tuple(sorted(channels)), tuple(sorted(experiments))


def generic_profile_for_catalogue(
    catalogue: PackedCatalogue,
    image: PackedImageSpec,
) -> PackedDatasetProfile:
    """Build a conservative auto-contrast profile from catalogue dimensions."""
    dtype = np.dtype(image.dtype)
    if dtype.kind not in "uif" or dtype.itemsize <= 0 or dtype.itemsize > 255:
        raise PackedProfileError(f"Generic packed image has unsupported dtype: {dtype}")
    if len(image.image_shape) != 2 or any(value <= 0 for value in image.image_shape):
        raise PackedProfileError(
            f"Generic packed image must have a non-empty 2-D shape: {image.image_shape}"
        )
    decoded_bytes = image.image_shape[0] * image.image_shape[1] * dtype.itemsize
    if decoded_bytes > MAX_PACKED_MEMBER_BYTES:
        raise PackedProfileError(
            "Generic packed image decoded plane exceeds the "
            f"{MAX_PACKED_MEMBER_BYTES}-byte limit: {decoded_bytes}"
        )
    geometry, channel_indexes, experiments = _catalogue_geometry(catalogue, image)
    if len(channel_indexes) > MAX_PACKED_CHANNELS:
        raise PackedProfileError(
            f"Generic packed image exceeds the {MAX_PACKED_CHANNELS}-channel limit"
        )
    if decoded_bytes * len(channel_indexes) > MAX_PACKED_FIELD_BYTES:
        raise PackedProfileError(
            "Generic packed field exceeds the "
            f"{MAX_PACKED_FIELD_BYTES}-byte decoded limit"
        )
    return PackedDatasetProfile(
        index_filename=catalogue.index_path.name,
        row_count=catalogue.row_count,
        geometry=geometry,
        channels=tuple(
            PackedChannel(
                index=index,
                label=str(index),
                color=None,
                active=True,
                windows=(),
            )
            for index in channel_indexes
        ),
        experiment_cell_lines=tuple((experiment, experiment) for experiment in experiments),
        metadata_path=None,
        codec=PackedCodec(typesize=dtype.itemsize),
        dtype=dtype.name,
        plate_prefix="",
        member_prefix=image.member_prefix,
        auto_contrast=True,
    )


def profile_for_catalogue(
    catalogue: PackedCatalogue,
    *,
    image: PackedImageSpec | None = None,
) -> PackedDatasetProfile:
    """Resolve an exact pin or synchronously infer a safe generic profile."""
    known = profile_for_index(catalogue.index_path.name, catalogue.row_count)
    if known is not None:
        return known
    return generic_profile_for_catalogue(catalogue, image or probe_packed_image(catalogue))
