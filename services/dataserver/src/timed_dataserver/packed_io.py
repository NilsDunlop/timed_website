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
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import numpy as np
import tifffile
from numcodecs import Blosc

from .packed import IndexSignature, PackedCatalogue, PackedMember
from .packed_profiles import PackedCodec, PackedDatasetProfile

DEFAULT_PACKED_CACHE_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_TAR_IO = 4
_SOURCE_SNAPSHOT_ATTEMPTS = 3
_TAR_BLOCK_SIZE = 512
_OS_PREAD = getattr(os, "pread", None)
_NUMCODECS_VERSION = version("numcodecs")
_LOG = logging.getLogger(__name__)


class PackedReadError(RuntimeError):
    """Raised when packed bytes cannot be read or decoded safely."""


class PackedFieldNotFoundError(PackedReadError):
    """Raised when a requested field or channel is absent."""


class PackedChunkKeyError(PackedReadError):
    """Raised when a requested chunk coordinate is outside the packed image."""


@dataclass(frozen=True, slots=True)
class TarRevision:
    """Stable file attributes used by field cache keys and chunk validators."""

    path: Path
    device: int
    inode: int
    mtime_ns: int
    size: int


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


class PackedFieldCache:
    """Thread-safe byte-bounded LRU with one in-flight loader per key."""

    def __init__(self, max_bytes: int = DEFAULT_PACKED_CACHE_BYTES) -> None:
        if max_bytes < 0:
            raise ValueError("Packed cache size cannot be negative")
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, DecodedField] = OrderedDict()
        self._inflight: dict[Hashable, Future[DecodedField]] = {}
        self._current_bytes = 0

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return self._current_bytes

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._current_bytes = 0

    def get_or_load(
        self,
        key: Hashable,
        loader: Callable[[], DecodedField],
    ) -> tuple[DecodedField, Literal["hit", "miss"]]:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached, "hit"
            future = self._inflight.get(key)
            leader = future is None
            if future is None:
                future = Future()
                self._inflight[key] = future

        if not leader:
            return future.result(), "hit"

        try:
            loaded = loader()
        except BaseException as exc:
            with self._lock:
                self._inflight.pop(key, None)
                future.set_exception(exc)
            raise

        with self._lock:
            self._entries[key] = loaded
            self._entries.move_to_end(key)
            self._current_bytes += loaded.nbytes
            while self._entries and self._current_bytes > self.max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._current_bytes -= evicted.nbytes
            self._inflight.pop(key, None)
            future.set_result(loaded)
        return loaded, "miss"


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
class _TarIdentity:
    path: Path
    device: int
    inode: int
    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class _HeaderIdentity:
    tar: _TarIdentity
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
        if set(field) != set(expected_channels) or len(field) != len(expected_channels):
            raise PackedReadError(
                f"Packed field has channels {sorted(field)}, expected {list(expected_channels)}"
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
        if chunk.dtype != np.dtype("uint16"):
            raise PackedReadError(f"Packed chunk has unexpected dtype: {chunk.dtype}")
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
                    raise PackedReadError(f"Could not stat packed tar {canonical}: {exc}") from exc
                if _same_file_revision(path_stat, descriptor_stat):
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
        flags = os.O_RDONLY
        for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW"):
            flags |= getattr(os, flag_name, 0)
        for _ in range(3):
            fd: int | None = None
            try:
                fd = os.open(path, flags)
                descriptor_stat = os.fstat(fd)
                path_stat = os.stat(path, follow_symlinks=False)
                if not stat.S_ISREG(descriptor_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
                    raise PackedReadError(f"Packed tar is not a regular file: {path}")
                if _same_file_revision(path_stat, descriptor_stat):
                    descriptor = _Descriptor(path, fd, threading.Lock())
                    fd = None
                    return descriptor
            except OSError as exc:
                raise PackedReadError(f"Could not open packed tar {path}: {exc}") from exc
            finally:
                if fd is not None:
                    with suppress(OSError):
                        os.close(fd)
        raise PackedReadError(f"Packed tar changed repeatedly while opening: {path}")

    def _member_source(self, channel: int, member: PackedMember) -> _MemberSource:
        descriptor = self._descriptor(member.tar)
        try:
            file_stat = os.fstat(descriptor.fd)
        except OSError as exc:
            raise PackedReadError(f"Could not stat packed tar {descriptor.path}: {exc}") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise PackedReadError(f"Packed tar is not a regular file: {descriptor.path}")
        if member.offset < _TAR_BLOCK_SIZE:
            raise PackedReadError(
                f"Packed member offset must be at least {_TAR_BLOCK_SIZE}: {member.offset}"
            )
        if member.size <= 0:
            raise PackedReadError(f"Packed member size must be positive: {member.size}")
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
        output = bytearray()
        try:
            if _OS_PREAD is not None:
                while len(output) < size:
                    chunk = _OS_PREAD(
                        descriptor.fd,
                        size - len(output),
                        offset + len(output),
                    )
                    if not chunk:
                        break
                    output.extend(chunk)
            else:
                with descriptor.position_lock:
                    os.lseek(descriptor.fd, offset, os.SEEK_SET)
                    while len(output) < size:
                        chunk = os.read(descriptor.fd, size - len(output))
                        if not chunk:
                            break
                        output.extend(chunk)
        except OSError as exc:
            raise PackedReadError(
                f"Could not read packed tar {descriptor.path} at {offset}: {exc}"
            ) from exc
        if len(output) != size:
            raise PackedReadError(
                f"Short packed read from {descriptor.path}: expected {size}, got {len(output)}"
            )
        return bytes(output)

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
            combined = self._read_exact_at(first.descriptor, start, end - start)
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
        if tuple(plane.shape) != profile.geometry.image_shape:
            raise PackedReadError(
                f"Packed TIFF shape {tuple(plane.shape)} does not match "
                f"{profile.geometry.image_shape}: {source.member.relpath}"
            )
        if plane.dtype != np.dtype("uint16"):
            raise PackedReadError(
                f"Packed TIFF dtype {plane.dtype} is not uint16: {source.member.relpath}"
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
            if len(header) != _TAR_BLOCK_SIZE:
                raise ValueError(f"header has {len(header)} bytes")
            if header[257:265] != tarfile.POSIX_MAGIC:
                raise ValueError("header does not contain POSIX USTAR magic")
            info = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
            expected_name = f"{profile.member_prefix}{source.member.relpath}"
            if not info.isreg():
                raise ValueError(f"member is not a regular file: {info.name!r}")
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
            raise PackedReadError(f"Packed tar is refused until restart: {path}: {reason}")

    def _refuse_locked(self, path: Path, reason: str) -> None:
        self._refused_tars[path] = reason
        self._logger.error("Refusing packed tar %s until restart: %s", path, reason)
        raise PackedReadError(f"Packed tar is refused until restart: {path}: {reason}")


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
        tar=_TarIdentity(
            source.revision.path,
            source.revision.device,
            source.revision.inode,
            source.revision.mtime_ns,
            source.revision.size,
        ),
        offset=source.member.offset,
        size=source.member.size,
        name=f"{profile.member_prefix}{source.member.relpath}",
    )


def _same_file_revision(path_stat: os.stat_result, descriptor_stat: os.stat_result) -> bool:
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
