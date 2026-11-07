"""One plate's tar+wal/v1 mask store: its revision, offset table and members.

The log is authoritative. Each complete line commits one field, and the last
record wins for each well and site. A final line without a newline is a write
in progress and is ignored, as are tar bytes after the largest committed
``tar_end``. Any other unreadable line, or a tar shorter than the committed
length, makes the store corrupt. Every file is opened read-only, without
following a final symlink, and no lock is taken.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import stat
import tarfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import tifffile

from .mask_catalogue import KIND_PATTERN, MaskPlateFiles
from .paths import os_error_reason
from .revisions import UnstableRevisionError, load_stable
from .tar_io import (
    TAR_BLOCK_SIZE,
    NotRegularFileError,
    ShortReadError,
    open_flags,
    parse_ustar_header,
    read_exact_at,
    regular_stats,
)

MAX_MASK_MEMBER_BYTES = 64 * 1024 * 1024
MAX_MASK_LOG_BYTES = 1024 * 1024 * 1024
MAX_MASK_SNAPSHOT_BYTES = 64 * 1024 * 1024
_ZLIB_COMPRESSIONS = frozenset((8, 32946))
_WELL_PATTERN = re.compile(r"[A-Za-z]+[0-9]+")
_SNAPSHOT_COLUMNS = ("well", "site", "kind", "offset", "size")
_LOG = logging.getLogger(__name__)


class MaskStoreError(Exception):
    """Raised when a plate store is corrupt, unreadable or keeps changing."""


@dataclass(frozen=True, slots=True)
class StoreRevision:
    """The contract's revision: the log's size and mtime, and the tar's size."""

    log_size: int
    log_mtime_ns: int
    tar_size: int


@dataclass(frozen=True, slots=True)
class _StoreState:
    revision: StoreRevision
    log_identity: tuple[int, int]
    tar_identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class MaskMember:
    """One committed mask: its kind and where its data lies in the tar."""

    kind: str
    offset: int
    size: int


@dataclass(frozen=True, slots=True)
class MaskRecord:
    """The last committed record of one field."""

    well: str
    site: int
    tar_end: int
    members: Mapping[str, MaskMember]


@dataclass(frozen=True, slots=True)
class MaskTable:
    """A store's offset table for one revision."""

    revision: StoreRevision
    log_identity: tuple[int, int]
    tar_identity: tuple[int, int]
    committed_length: int
    records: Mapping[tuple[str, int], MaskRecord]

    def record(self, well: str, site: int) -> MaskRecord | None:
        return self.records.get((well, site))


def _corrupt(files: MaskPlateFiles, reason: str) -> MaskStoreError:
    return MaskStoreError(f"Mask store {files.label} is corrupt: {reason}")


def _regular_stat(files: MaskPlateFiles, path_name: str, path: Path) -> os.stat_result:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise MaskStoreError(
            f"Could not read mask store {files.label} {path_name}: {os_error_reason(exc)}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise MaskStoreError(f"Mask store {files.label} {path_name} is not a regular file")
    return metadata


def read_state(files: MaskPlateFiles) -> _StoreState:
    """Stat the log and tar without opening them."""
    log = _regular_stat(files, "log", files.log)
    tar = _regular_stat(files, "tar", files.tar)
    return _StoreState(
        revision=StoreRevision(log.st_size, log.st_mtime_ns, tar.st_size),
        log_identity=(log.st_dev, log.st_ino),
        tar_identity=(tar.st_dev, tar.st_ino),
    )


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _record(files: MaskPlateFiles, number: int, value: object) -> MaskRecord:
    def bad(reason: str) -> MaskStoreError:
        return _corrupt(files, f"log line {number} {reason}")

    if not isinstance(value, dict):
        raise bad("is not a JSON object")
    well, site, tar_end, masks = (value.get(key) for key in ("well", "site", "tar_end", "masks"))
    if not isinstance(well, str) or _WELL_PATTERN.fullmatch(well) is None:
        raise bad("has no valid well")
    if not _integer(site) or site < 0:
        raise bad("has no valid site")
    if not _integer(tar_end) or tar_end < 0 or tar_end % TAR_BLOCK_SIZE:
        raise bad("has a tar_end that is not a multiple of 512")
    if not isinstance(masks, list):
        raise bad("has no mask list")
    members: dict[str, MaskMember] = {}
    for entry in masks:
        if not isinstance(entry, dict):
            raise bad("has a mask that is not an object")
        kind, offset, size = (entry.get(key) for key in ("kind", "offset", "size"))
        if not isinstance(kind, str) or KIND_PATTERN.fullmatch(kind) is None:
            raise bad("has a mask without a valid kind")
        if kind in members:
            raise bad(f"repeats mask kind {kind}")
        if not _integer(offset) or not _integer(size) or offset < TAR_BLOCK_SIZE or size <= 0:
            raise bad(f"has invalid bounds for mask {kind}")
        if offset + size > tar_end:
            raise bad(f"places mask {kind} beyond its tar_end")
        members[kind] = MaskMember(kind=kind, offset=offset, size=size)
    return MaskRecord(well=well, site=site, tar_end=tar_end, members=MappingProxyType(members))


def parse_log(
    files: MaskPlateFiles,
    data: bytes,
    tar_size: int,
) -> tuple[dict[tuple[str, int], MaskRecord], int]:
    """Return the last record per field and the committed tar length."""
    *complete, _in_progress = data.split(b"\n")
    records: dict[tuple[str, int], MaskRecord] = {}
    committed = 0
    for number, line in enumerate(complete, start=1):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _corrupt(files, f"log line {number} is not valid JSON") from None
        record = _record(files, number, value)
        if record.tar_end < committed:
            raise _corrupt(files, f"log line {number} moves tar_end back")
        committed = record.tar_end
        records[(record.well, record.site)] = record
    if committed > tar_size:
        raise _corrupt(
            files,
            f"the tar has {tar_size} bytes but the log commits {committed}",
        )
    return records, committed


def _read_whole(files: MaskPlateFiles, name: str, path: Path, size: int) -> bytes:
    fd: int | None = None
    try:
        fd = os.open(path, open_flags())
        descriptor_stat, _path_stat = regular_stats(fd, path)
        if descriptor_stat.st_size < size:
            raise MaskStoreError(f"Mask store {files.label} {name} shrank while reading")
        return read_exact_at(fd, 0, size)
    except NotRegularFileError:
        raise MaskStoreError(f"Mask store {files.label} {name} is not a regular file") from None
    except ShortReadError as exc:
        raise MaskStoreError(
            f"Short read from mask store {files.label} {name}: "
            f"expected {exc.expected}, got {exc.received}"
        ) from None
    except OSError as exc:
        raise MaskStoreError(
            f"Could not read mask store {files.label} {name}: {os_error_reason(exc)}"
        ) from exc
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)


def _cross_check(files: MaskPlateFiles, records: Mapping[tuple[str, int], MaskRecord]) -> None:
    """Compare the finalised index snapshot with the log; the log always wins."""
    try:
        snapshot = os.stat(files.snapshot, follow_symlinks=False)
    except OSError:
        return
    if not stat.S_ISREG(snapshot.st_mode) or snapshot.st_size > MAX_MASK_SNAPSHOT_BYTES:
        return
    try:
        data = _read_whole(files, "index snapshot", files.snapshot, snapshot.st_size)
        table = pq.read_table(pa.BufferReader(data), columns=list(_SNAPSHOT_COLUMNS))
        indexed = {
            (str(row["well"]), int(row["site"]), str(row["kind"])): (
                int(row["offset"]),
                int(row["size"]),
            )
            for row in table.to_pylist()
        }
    except (MaskStoreError, pa.ArrowException, KeyError, TypeError, ValueError) as exc:
        _LOG.warning("Could not cross-check mask store %s with its index: %s", files.label, exc)
        return
    logged = {
        (well, site, member.kind): (member.offset, member.size)
        for (well, site), record in records.items()
        for member in record.members.values()
    }
    differences = len(set(indexed.items()) ^ set(logged.items()))
    if differences:
        _LOG.warning(
            "Mask store %s index snapshot differs from its log in %d entries; serving the log",
            files.label,
            differences,
        )


def load_table(files: MaskPlateFiles, *, state: _StoreState | None = None) -> MaskTable:
    """Build the offset table against a revision that still holds afterwards."""

    def load(current: _StoreState) -> MaskTable:
        if current.revision.log_size > MAX_MASK_LOG_BYTES:
            raise MaskStoreError(f"Mask store {files.label} log exceeds the size limit")
        data = _read_whole(files, "log", files.log, current.revision.log_size)
        records, committed = parse_log(files, data, current.revision.tar_size)
        _cross_check(files, records)
        return MaskTable(
            revision=current.revision,
            log_identity=current.log_identity,
            tar_identity=current.tar_identity,
            committed_length=committed,
            records=MappingProxyType(records),
        )

    try:
        table, _state = load_stable(lambda: read_state(files), load, first=state)
    except UnstableRevisionError:
        raise MaskStoreError(f"Mask store {files.label} changed repeatedly while loading") from None
    return table


def member_name(files: MaskPlateFiles, well: str, site: int, kind: str) -> str:
    """The USTAR member name the contract gives a field's mask."""
    return f"{files.experiment}/Plate{files.plate}/{well}_s{site}_{kind}.tiff"


def read_member(
    files: MaskPlateFiles,
    table: MaskTable,
    record: MaskRecord,
    member: MaskMember,
) -> bytes:
    """Read one mask's payload after verifying the USTAR header before it."""
    if member.size > MAX_MASK_MEMBER_BYTES:
        raise _corrupt(files, f"mask {member.kind} exceeds {MAX_MASK_MEMBER_BYTES} bytes")
    expected_name = member_name(files, record.well, record.site, member.kind)
    fd: int | None = None
    try:
        fd = os.open(files.tar, open_flags())
        descriptor_stat, _path_stat = regular_stats(fd, files.tar)
        if (descriptor_stat.st_dev, descriptor_stat.st_ino) != table.tar_identity:
            raise MaskStoreError(f"Mask store {files.label} tar was replaced while reading")
        if member.offset + member.size > descriptor_stat.st_size:
            raise _corrupt(files, f"mask {member.kind} lies beyond the tar")
        block = read_exact_at(fd, member.offset - TAR_BLOCK_SIZE, TAR_BLOCK_SIZE + member.size)
    except NotRegularFileError:
        raise MaskStoreError(f"Mask store {files.label} tar is not a regular file") from None
    except ShortReadError as exc:
        raise MaskStoreError(
            f"Short read from mask store {files.label} tar: "
            f"expected {exc.expected}, got {exc.received}"
        ) from None
    except OSError as exc:
        raise MaskStoreError(
            f"Could not read mask store {files.label} tar: {os_error_reason(exc)}"
        ) from exc
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
    try:
        header = parse_ustar_header(block[:TAR_BLOCK_SIZE])
        if header.name != expected_name:
            raise ValueError(f"member name is {header.name!r}, expected {expected_name!r}")
        if header.size != member.size:
            raise ValueError(f"member size is {header.size}, expected {member.size}")
    except (tarfile.HeaderError, UnicodeError, ValueError) as exc:
        raise _corrupt(files, f"header before offset {member.offset}: {exc}") from exc
    return block[TAR_BLOCK_SIZE:]


class _InvalidMask(Exception):
    """A decoded mask that breaks the payload contract."""


def decode_labels(
    files: MaskPlateFiles,
    kind: str,
    payload: bytes,
    shape: tuple[int, int],
) -> np.ndarray:
    """Decode a single-page, zlib-compressed uint16 TIFF at the plane shape, uncast."""
    try:
        with tifffile.TiffFile(io.BytesIO(payload)) as image:
            if len(image.pages) != 1:
                raise _InvalidMask(f"TIFF has {len(image.pages)} pages, expected 1")
            page = image.pages[0]
            if int(page.compression) not in _ZLIB_COMPRESSIONS:
                raise _InvalidMask(f"TIFF compression is {page.compression.name}, expected zlib")
            if tuple(int(value) for value in page.shape) != shape:
                raise _InvalidMask(f"TIFF shape is {tuple(page.shape)}, expected {shape}")
            if np.dtype(page.dtype) != np.dtype(np.uint16):
                raise _InvalidMask(f"TIFF dtype is {page.dtype}, expected uint16")
            labels = page.asarray()
    except _InvalidMask as exc:
        raise _corrupt(files, f"mask {kind}: {exc}") from None
    except Exception as exc:
        raise _corrupt(files, f"mask {kind} could not be decoded: {exc}") from exc
    if not isinstance(labels, np.ndarray) or labels.shape != shape or labels.dtype != np.uint16:
        raise _corrupt(files, f"mask {kind} did not decode to a {shape} uint16 plane")
    labels = np.ascontiguousarray(labels)
    labels.setflags(write=False)
    return labels
