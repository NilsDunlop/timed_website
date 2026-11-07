"""Blocking tar I/O, decoding, and field caching for packed datasets."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import stat
import tarfile
import threading
from collections.abc import Hashable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import numpy as np
import tifffile
from numcodecs import Blosc

from .caches import ByteBoundedSingleFlightLru
from .packed import IndexSignature, PackedCatalogue, PackedMember
from .packed_profiles import (
    MAX_PACKED_CHANNELS,
    MAX_PACKED_FIELD_BYTES,
    MAX_PACKED_MEMBER_BYTES,
    PackedCodec,
    PackedDatasetProfile,
)
from .paths import os_error_reason
from .revisions import FileRevision
from .tar_io import (
    TAR_BLOCK_SIZE,
    NotRegularFileError,
    ShortReadError,
    open_flags,
    parse_ustar_header,
    read_exact_at,
    regular_stats,
    same_file_revision,
)

DEFAULT_PACKED_CACHE_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_TAR_IO = 4
_SOURCE_SNAPSHOT_ATTEMPTS = 3
_TAR_BLOCK_SIZE = TAR_BLOCK_SIZE
_NUMCODECS_VERSION = version("numcodecs")
_LOG = logging.getLogger(__name__)


class PackedReadError(RuntimeError):
    """Raised when packed bytes cannot be read or decoded safely."""


class PackedFieldNotFoundError(PackedReadError):
    """Raised when a requested field or channel is absent."""


class PackedChunkKeyError(PackedReadError):
    """Raised when a requested chunk coordinate is outside the packed image."""


# Stable file attributes used by field cache keys and chunk validators.
TarRevision = FileRevision


@dataclass(frozen=True, slots=True)
class DecodedField:
    """A complete immutable field, ordered by packed channel index."""

    experiment: str
    plate: int
    well: str
    site: int
    channels: tuple[int, ...]
    planes: tuple[np.ndarray, ...]
    members: tuple[PackedMember, ...]
    revisions: tuple[TarRevision, ...]

    def __post_init__(self) -> None:
        count = len(self.channels)
        if not (len(self.planes) == len(self.members) == len(self.revisions) == count):
            raise ValueError("Decoded field channel data has inconsistent lengths")
        for plane in self.planes:
            plane.setflags(write=False)

    @property
    def nbytes(self) -> int:
        return sum(plane.nbytes for plane in self.planes)

    def channel_data(self, channel: int) -> tuple[np.ndarray, PackedMember, TarRevision]:
        try:
            position = self.channels.index(channel)
        except ValueError as exc:
            raise PackedFieldNotFoundError(f"Packed channel does not exist: {channel}") from exc
        return self.planes[position], self.members[position], self.revisions[position]


@dataclass(frozen=True, slots=True)
class PackedChunkResult:
    """One encoded Zarr chunk and its deterministic response metadata."""

    content: bytes
    etag: str
    cache_status: Literal["hit", "miss"]


def _decoded_field_bytes(field: DecodedField) -> int:
    return field.nbytes


class PackedFieldCache(ByteBoundedSingleFlightLru[Hashable, DecodedField]):
    """Thread-safe byte-bounded LRU with one in-flight loader per key."""

    def __init__(self, max_bytes: int = DEFAULT_PACKED_CACHE_BYTES) -> None:
        if max_bytes < 0:
            raise ValueError("Packed cache size cannot be negative")
        super().__init__(max_bytes, _decoded_field_bytes)


@dataclass(slots=True)
class _Descriptor:
    path: Path
    fd: int
    position_lock: threading.Lock


@dataclass(frozen=True, slots=True)
class _MemberSource:
    channel: int
    member: PackedMember
    descriptor: _Descriptor
    revision: TarRevision


@dataclass(frozen=True, slots=True)
class _MemberIdentity:
    channel: int
    tar: str
    tar_device: int
    tar_inode: int
    tar_mtime_ns: int
    tar_size: int
    offset: int
    size: int
    relpath: str


@dataclass(frozen=True, slots=True)
class _FieldCacheKey:
    index_path: str
    index_signature: IndexSignature
    experiment: str
    plate: int
    well: str
    site: int
    profile: tuple[object, ...]
    members: tuple[_MemberIdentity, ...]


@dataclass(frozen=True, slots=True)
class _HeaderIdentity:
    tar: TarRevision
    offset: int
    size: int
    name: str


class PackedReader:
    """Read complete packed fields and serve encoded chunks synchronously."""

    def __init__(
        self,
        *,
        cache_bytes: int = DEFAULT_PACKED_CACHE_BYTES,
        max_io: int = DEFAULT_MAX_TAR_IO,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_io <= 0:
            raise ValueError("Packed I/O concurrency must be positive")
        self.cache = PackedFieldCache(cache_bytes)
        self._max_io = max_io
        self._field_slots = threading.BoundedSemaphore(max_io)
        self._io_slots = threading.BoundedSemaphore(max_io)
        self._descriptors_lock = threading.Lock()
        self._descriptors: dict[Path, _Descriptor] = {}
        self._retired_descriptors: list[_Descriptor] = []
        self._verification_lock = threading.RLock()
        self._verified_headers: set[_HeaderIdentity] = set()
        self._refused_tars: dict[Path, str] = {}
        self._closed = False
        self._logger = logger or _LOG

    def __enter__(self) -> PackedReader:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def descriptor_count(self) -> int:
        with self._descriptors_lock:
            return len(self._descriptors)

    @property
    def refused_tars(self) -> tuple[Path, ...]:
        with self._verification_lock:
            return tuple(sorted(self._refused_tars))

    def close(self) -> None:
        """Close every cached descriptor. This operation is idempotent."""
        with self._descriptors_lock:
            if self._closed:
                return
            self._closed = True
            descriptors = (*self._descriptors.values(), *self._retired_descriptors)
            self._descriptors.clear()
            self._retired_descriptors.clear()
        for descriptor in descriptors:
            try:
                os.close(descriptor.fd)
            except OSError:
                self._logger.exception("Could not close packed tar descriptor %s", descriptor.path)
        self.cache.clear()

    def get_field(
        self,
        catalogue: PackedCatalogue,
        profile: PackedDatasetProfile,
        experiment: str,
        plate: int,
        well: str,
        site: int,
    ) -> tuple[DecodedField, Literal["hit", "miss"]]:
        """Return all channels for one field through the single-flight LRU."""
        self._assert_open()
        field = catalogue.field(experiment, plate, well, site)
        if field is None:
            raise PackedFieldNotFoundError(
                f"Packed field does not exist: {experiment}/{plate}/{well}/s{site}"
            )
        expected_channels = tuple(channel.index for channel in profile.channels)
        if len(expected_channels) > MAX_PACKED_CHANNELS:
            raise PackedReadError(
                f"Packed field exceeds the {MAX_PACKED_CHANNELS}-channel limit"
            )
        height, width = profile.geometry.image_shape
        field_bytes = height * width * np.dtype(profile.dtype).itemsize * len(expected_channels)
        if field_bytes > MAX_PACKED_FIELD_BYTES:
            raise PackedReadError(
                f"Packed field exceeds the {MAX_PACKED_FIELD_BYTES}-byte decoded limit"
            )
        if set(field) != set(expected_channels) or len(field) != len(expected_channels):
            raise PackedReadError(
                f"Packed field has channels {sorted(field)}, expected {list(expected_channels)}"
            )
        encoded_bytes = 0
        for member in field.values():
            if member.size <= 0:
                raise PackedReadError(f"Packed member size must be positive: {member.size}")
            if member.size > MAX_PACKED_MEMBER_BYTES:
                raise PackedReadError(
                    f"Packed member exceeds the {MAX_PACKED_MEMBER_BYTES}-byte limit: "
                    f"{member.size}"
                )
            encoded_bytes += member.size
            if encoded_bytes > MAX_PACKED_FIELD_BYTES:
                raise PackedReadError(
                    f"Packed field exceeds the {MAX_PACKED_FIELD_BYTES}-byte encoded limit"
                )

        sources = self._field_sources(expected_channels, field)
        for source in sources:
            self._raise_if_refused(source.revision.path)
        key = _FieldCacheKey(
            index_path=str(catalogue.index_path),
            index_signature=catalogue.signature,
            experiment=experiment,
            plate=plate,
            well=well,
            site=site,
            profile=(
                profile.index_filename,
                profile.row_count,
                profile.dtype,
                profile.geometry.image_shape,
                profile.geometry.chunk_shape,
                expected_channels,
                profile.codec,
            ),
            members=tuple(
                _MemberIdentity(
                    channel=source.channel,
                    tar=str(source.revision.path),
                    tar_device=source.revision.device,
                    tar_inode=source.revision.inode,
                    tar_mtime_ns=source.revision.mtime_ns,
                    tar_size=source.revision.size,
                    offset=source.member.offset,
                    size=source.member.size,
                    relpath=source.member.relpath,
                )
                for source in sources
            ),
        )
        return self.cache.get_or_load(
            key,
            lambda: self._load_field(
                profile,
                experiment,
                plate,
                well,
                site,
                sources,
            ),
        )

    def _field_sources(
        self,
        channels: tuple[int, ...],
        field: Mapping[int, PackedMember],
    ) -> tuple[_MemberSource, ...]:
        for _attempt in range(_SOURCE_SNAPSHOT_ATTEMPTS):
            sources = tuple(self._member_source(channel, field[channel]) for channel in channels)
            if _sources_are_coherent(sources):
                return sources
        raise PackedReadError("Packed tar changed repeatedly while resolving a field")

    def get_chunk(
        self,
        catalogue: PackedCatalogue,
        profile: PackedDatasetProfile,
        experiment: str,
        plate: int,
        well: str,
        site: int,
        channel: int,
        chunk_y: int,
        chunk_x: int,
    ) -> PackedChunkResult:
        """Slice and encode one ``(1, y, x)`` Zarr v3 chunk."""
        if min(channel, chunk_y, chunk_x) < 0:
            raise PackedChunkKeyError("Packed chunk indices must be non-negative")
        profile_channel = profile.channel(channel)
        if profile_channel is None:
            raise PackedChunkKeyError(f"Packed channel does not exist: {channel}")
        height, width = profile.geometry.image_shape
        chunk_height, chunk_width = profile.geometry.chunk_shape
        if chunk_y * chunk_height >= height or chunk_x * chunk_width >= width:
            raise PackedChunkKeyError(f"Packed chunk does not exist: {channel}/{chunk_y}/{chunk_x}")

        field, cache_status = self.get_field(
            catalogue,
            profile,
            experiment,
            plate,
            well,
            site,
        )
        plane, member, revision = field.channel_data(channel)
        y_start = chunk_y * chunk_height
        x_start = chunk_x * chunk_width
        chunk = np.ascontiguousarray(
            plane[
                y_start : min(y_start + chunk_height, height),
                x_start : min(x_start + chunk_width, width),
            ][np.newaxis, ...]
        )
        expected_dtype = np.dtype(profile.dtype)
        if chunk.dtype != expected_dtype:
            raise PackedReadError(
                f"Packed chunk dtype {chunk.dtype} does not match {expected_dtype}"
            )
        if chunk.dtype.byteorder == ">" or (chunk.dtype.byteorder == "=" and not np.little_endian):
            chunk = chunk.byteswap().view(chunk.dtype.newbyteorder("<"))
        codec = _chunk_codec(profile.codec)
        encoded = bytes(codec.encode(chunk))
        etag = _chunk_etag(
            revision,
            member,
            channel,
            chunk_y,
            chunk_x,
            codec.get_config(),
        )
        return PackedChunkResult(content=encoded, etag=etag, cache_status=cache_status)

    def _assert_open(self) -> None:
        with self._descriptors_lock:
            if self._closed:
                raise PackedReadError("Packed reader is closed")

    def _descriptor(self, path: Path) -> _Descriptor:
        canonical = Path(os.path.abspath(path))
        with self._descriptors_lock:
            if self._closed:
                raise PackedReadError("Packed reader is closed")
            existing = self._descriptors.get(canonical)
            if existing is not None:
                try:
                    path_stat = os.stat(canonical, follow_symlinks=False)
                    descriptor_stat = os.fstat(existing.fd)
                except OSError as exc:
                    raise PackedReadError(
                        f"Could not stat packed tar {canonical.name}: {os_error_reason(exc)}"
                    ) from exc
                if same_file_revision(path_stat, descriptor_stat):
                    return existing
            descriptor = self._open_descriptor(canonical)
            if existing is not None:
                # A concurrent read can still hold this descriptor. Keep it
                # alive until reader shutdown while all new work uses the
                # replacement.
                self._retired_descriptors.append(existing)
            self._descriptors[canonical] = descriptor
            return descriptor

    def _open_descriptor(self, path: Path) -> _Descriptor:
        flags = open_flags()
        for _ in range(3):
            fd: int | None = None
            try:
                fd = os.open(path, flags)
                try:
                    descriptor_stat, path_stat = regular_stats(fd, path)
                except NotRegularFileError:
                    raise PackedReadError(
                        f"Packed tar is not a regular file: {path.name}"
                    ) from None
                if same_file_revision(path_stat, descriptor_stat):
                    descriptor = _Descriptor(path, fd, threading.Lock())
                    fd = None
                    return descriptor
            except OSError as exc:
                raise PackedReadError(
                    f"Could not open packed tar {path.name}: {os_error_reason(exc)}"
                ) from exc
            finally:
                if fd is not None:
                    with suppress(OSError):
                        os.close(fd)
        raise PackedReadError(f"Packed tar changed repeatedly while opening: {path.name}")

    def _member_source(self, channel: int, member: PackedMember) -> _MemberSource:
        descriptor = self._descriptor(member.tar)
        try:
            file_stat = os.fstat(descriptor.fd)
        except OSError as exc:
            raise PackedReadError(
                f"Could not stat packed tar {descriptor.path.name}: {os_error_reason(exc)}"
            ) from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise PackedReadError(f"Packed tar is not a regular file: {descriptor.path.name}")
        if member.offset < _TAR_BLOCK_SIZE:
            raise PackedReadError(
                f"Packed member offset must be at least {_TAR_BLOCK_SIZE}: {member.offset}"
            )
        if member.size <= 0:
            raise PackedReadError(f"Packed member size must be positive: {member.size}")
        if member.size > MAX_PACKED_MEMBER_BYTES:
            raise PackedReadError(
                f"Packed member exceeds the {MAX_PACKED_MEMBER_BYTES}-byte limit: "
                f"{member.size}"
            )
        if member.offset + member.size > file_stat.st_size:
            raise PackedReadError(
                f"Packed member exceeds tar bounds: {member.offset} + {member.size} "
                f"> {file_stat.st_size}"
            )
        revision = TarRevision(
            path=descriptor.path,
            device=file_stat.st_dev,
            inode=file_stat.st_ino,
            mtime_ns=file_stat.st_mtime_ns,
            size=file_stat.st_size,
        )
        return _MemberSource(channel, member, descriptor, revision)

    def _read_exact_at(self, descriptor: _Descriptor, offset: int, size: int) -> bytes:
        with self._io_slots:
            return self._read_exact_at_unlimited(descriptor, offset, size)

    def _read_exact_at_unlimited(self, descriptor: _Descriptor, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0:
            raise PackedReadError("Packed positional reads require non-negative bounds")
        try:
            return read_exact_at(
                descriptor.fd,
                offset,
                size,
                position_lock=descriptor.position_lock,
            )
        except OSError as exc:
            raise PackedReadError(
                f"Could not read packed tar {descriptor.path.name} at {offset}: "
                f"{os_error_reason(exc)}"
            ) from exc
        except ShortReadError as exc:
            raise PackedReadError(
                f"Short packed read from {descriptor.path.name}: "
                f"expected {size}, got {exc.received}"
            ) from None

    def _load_field(
        self,
        profile: PackedDatasetProfile,
        experiment: str,
        plate: int,
        well: str,
        site: int,
        sources: tuple[_MemberSource, ...],
    ) -> DecodedField:
        with self._field_slots:
            payloads = self._read_field_payloads(profile, sources)
            planes = tuple(
                self._decode_plane(payloads[source.channel], source, profile) for source in sources
            )
            return DecodedField(
                experiment=experiment,
                plate=plate,
                well=well,
                site=site,
                channels=tuple(source.channel for source in sources),
                planes=planes,
                members=tuple(source.member for source in sources),
                revisions=tuple(source.revision for source in sources),
            )

    def _read_field_payloads(
        self,
        profile: PackedDatasetProfile,
        sources: tuple[_MemberSource, ...],
    ) -> Mapping[int, bytes]:
        ordered = tuple(sorted(sources, key=lambda source: source.member.offset))
        if _members_are_contiguous(ordered):
            first = ordered[0]
            start = first.member.offset - _TAR_BLOCK_SIZE
            end = ordered[-1].member.offset + ordered[-1].member.size
            read_size = end - start
            maximum_read_size = (
                MAX_PACKED_FIELD_BYTES + len(ordered) * 2 * _TAR_BLOCK_SIZE
            )
            if read_size > maximum_read_size:
                raise PackedReadError(
                    f"Packed contiguous field exceeds the {maximum_read_size}-byte read limit"
                )
            combined = self._read_exact_at(first.descriptor, start, read_size)
            self._verify_header_bytes(
                first,
                profile,
                combined[:_TAR_BLOCK_SIZE],
            )
            return {
                source.channel: combined[
                    source.member.offset - start : source.member.offset - start + source.member.size
                ]
                for source in ordered
            }

        def read_one(source: _MemberSource) -> tuple[int, bytes]:
            self._ensure_header(source, profile)
            return (
                source.channel,
                self._read_exact_at(
                    source.descriptor,
                    source.member.offset,
                    source.member.size,
                ),
            )

        with ThreadPoolExecutor(max_workers=min(len(sources), self._max_io)) as executor:
            return dict(executor.map(read_one, sources))

    def _decode_plane(
        self,
        payload: bytes,
        source: _MemberSource,
        profile: PackedDatasetProfile,
    ) -> np.ndarray:
        expected_shape = profile.geometry.image_shape
        expected_dtype = np.dtype(profile.dtype)
        expected_bytes = expected_shape[0] * expected_shape[1] * expected_dtype.itemsize
        if expected_bytes > MAX_PACKED_MEMBER_BYTES:
            raise PackedReadError(
                f"Packed profile plane exceeds the {MAX_PACKED_MEMBER_BYTES}-byte limit"
            )
        try:
            with tifffile.TiffFile(io.BytesIO(payload)) as image:
                if len(image.pages) != 1:
                    raise PackedReadError(
                        f"Packed TIFF must be single-page: {source.member.relpath}"
                    )
                page = image.pages[0]
                if int(page.compression) != 1:
                    raise PackedReadError(
                        f"Packed TIFF must be uncompressed: {source.member.relpath}"
                    )
                page_shape = tuple(int(value) for value in page.shape)
                if page_shape != expected_shape:
                    raise PackedReadError(
                        f"Packed TIFF shape {page_shape} does not match "
                        f"{expected_shape}: {source.member.relpath}"
                    )
                page_dtype = np.dtype(page.dtype)
                if page_dtype != expected_dtype:
                    raise PackedReadError(
                        f"Packed TIFF dtype {page_dtype} does not match {expected_dtype}: "
                        f"{source.member.relpath}"
                    )
                byte_counts = tuple(int(value) for value in page.databytecounts)
                data_offsets = tuple(int(value) for value in page.dataoffsets)
                if len(byte_counts) != len(data_offsets) or sum(byte_counts) != expected_bytes:
                    raise PackedReadError(
                        "Packed TIFF byte counts do not match its decoded plane size: "
                        f"{source.member.relpath}"
                    )
                if any(
                    offset < 0 or count < 0 or offset + count > len(payload)
                    for offset, count in zip(data_offsets, byte_counts, strict=True)
                ):
                    raise PackedReadError(
                        f"Packed TIFF data lies outside its member payload: {source.member.relpath}"
                    )
                plane = page.asarray()
        except PackedReadError:
            raise
        except Exception as exc:
            self._ensure_header(source, profile, force=True)
            raise PackedReadError(
                f"Could not decode packed TIFF {source.member.relpath}: {exc}"
            ) from exc
        if not isinstance(plane, np.ndarray) or plane.ndim != 2:
            shape = getattr(plane, "shape", None)
            raise PackedReadError(
                f"Packed TIFF must be 2-D, got shape {shape}: {source.member.relpath}"
            )
        if tuple(plane.shape) != expected_shape:
            raise PackedReadError(
                f"Packed TIFF shape {tuple(plane.shape)} does not match "
                f"{expected_shape}: {source.member.relpath}"
            )
        if plane.dtype != expected_dtype:
            raise PackedReadError(
                f"Packed TIFF dtype {plane.dtype} does not match {expected_dtype}: "
                f"{source.member.relpath}"
            )
        plane = np.ascontiguousarray(plane)
        plane.setflags(write=False)
        return plane

    def _ensure_header(
        self,
        source: _MemberSource,
        profile: PackedDatasetProfile,
        *,
        force: bool = False,
    ) -> None:
        identity = _header_identity(source, profile)
        with self._verification_lock:
            self._raise_if_refused_locked(source.revision.path)
            if identity in self._verified_headers and not force:
                return
            header = self._read_exact_at(
                source.descriptor,
                source.member.offset - _TAR_BLOCK_SIZE,
                _TAR_BLOCK_SIZE,
            )
            self._verify_header_bytes_locked(source, profile, header, force=force)

    def _verify_header_bytes(
        self,
        source: _MemberSource,
        profile: PackedDatasetProfile,
        header: bytes,
        *,
        force: bool = False,
    ) -> None:
        with self._verification_lock:
            self._verify_header_bytes_locked(source, profile, header, force=force)

    def _verify_header_bytes_locked(
        self,
        source: _MemberSource,
        profile: PackedDatasetProfile,
        header: bytes,
        *,
        force: bool,
    ) -> None:
        path = source.revision.path
        identity = _header_identity(source, profile)
        self._raise_if_refused_locked(path)
        if identity in self._verified_headers and not force:
            return
        try:
            info = parse_ustar_header(header)
            expected_name = f"{profile.member_prefix}{source.member.relpath}"
            if info.name != expected_name:
                raise ValueError(
                    f"member name mismatch: expected {expected_name!r}, got {info.name!r}"
                )
            if info.size != source.member.size:
                raise ValueError(
                    f"member size mismatch: expected {source.member.size}, got {info.size}"
                )
        except (tarfile.HeaderError, UnicodeError, ValueError) as exc:
            self._refuse_locked(path, str(exc))
        self._verified_headers.add(identity)

    def _raise_if_refused(self, path: Path) -> None:
        with self._verification_lock:
            self._raise_if_refused_locked(path)

    def _raise_if_refused_locked(self, path: Path) -> None:
        reason = self._refused_tars.get(path)
        if reason is not None:
            raise PackedReadError(f"Packed tar is refused until restart: {path.name}: {reason}")

    def _refuse_locked(self, path: Path, reason: str) -> None:
        self._refused_tars[path] = reason
        self._logger.error("Refusing packed tar %s until restart: %s", path, reason)
        raise PackedReadError(f"Packed tar is refused until restart: {path.name}: {reason}")


def _members_are_contiguous(sources: tuple[_MemberSource, ...]) -> bool:
    if not sources:
        return False
    if any(source.descriptor is not sources[0].descriptor for source in sources[1:]):
        return False
    return all(
        following.member.offset
        == current.member.offset
        + ((current.member.size + _TAR_BLOCK_SIZE - 1) // _TAR_BLOCK_SIZE) * _TAR_BLOCK_SIZE
        + _TAR_BLOCK_SIZE
        for current, following in zip(sources, sources[1:], strict=False)
    )


def _sources_are_coherent(sources: tuple[_MemberSource, ...]) -> bool:
    snapshots: dict[Path, tuple[_Descriptor, TarRevision]] = {}
    for source in sources:
        current = (source.descriptor, source.revision)
        previous = snapshots.setdefault(source.revision.path, current)
        if previous[0] is not source.descriptor or previous[1] != source.revision:
            return False
    return True


def _header_identity(
    source: _MemberSource,
    profile: PackedDatasetProfile,
) -> _HeaderIdentity:
    return _HeaderIdentity(
        tar=source.revision,
        offset=source.member.offset,
        size=source.member.size,
        name=f"{profile.member_prefix}{source.member.relpath}",
    )


def _chunk_codec(profile: PackedCodec) -> Blosc:
    shuffle = {
        "noshuffle": Blosc.NOSHUFFLE,
        "shuffle": Blosc.SHUFFLE,
        "bitshuffle": Blosc.BITSHUFFLE,
    }.get(profile.shuffle)
    if shuffle is None:
        raise PackedReadError(f"Unsupported packed Blosc shuffle: {profile.shuffle}")
    return Blosc(
        cname=profile.cname,
        clevel=profile.clevel,
        shuffle=shuffle,
        blocksize=profile.blocksize,
        typesize=profile.typesize,
    )


def _chunk_etag(
    revision: TarRevision,
    member: PackedMember,
    channel: int,
    chunk_y: int,
    chunk_x: int,
    codec: Mapping[str, object],
) -> str:
    identity = json.dumps(
        {
            "tar": str(revision.path),
            "tar_device": revision.device,
            "tar_inode": revision.inode,
            "tar_mtime_ns": revision.mtime_ns,
            "tar_size": revision.size,
            "member_offset": member.offset,
            "member_size": member.size,
            "member_relpath": member.relpath,
            "chunk": [channel, chunk_y, chunk_x],
            "codec": codec,
            "numcodecs": _NUMCODECS_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f'"{hashlib.sha256(identity).hexdigest()}"'
