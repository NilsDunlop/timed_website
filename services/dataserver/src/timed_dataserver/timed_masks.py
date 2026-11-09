"""TIMED segmentation masks: one Zarr v3 label array per field, read plane by plane.

A field's mask array is uint16 (t, y, x), stored as one whole-array shard whose
inner chunks hold parts of single planes, Blosc-compressed, with a CRC32C
index at the end. It must match its image field exactly: the same number of
timepoints, the same ``t_indices`` and the same plane size, so array position
i is image timepoint i. Anything else makes the masks invalid.

An export array declares its axes. A production run's array stores no axes;
its (t, y, x) order is pinned by its layout instead, and the array must store
the plate, well, site and run that its path names.

Every file is opened read-only, without following a final symlink, inside its
configured mask folder. An array is identified by its metadata's and shard's
device, inode, size and mtime, so a regenerated export is picked up on the
next request without a restart.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numcodecs import Blosc
from numcodecs.checksum32 import CRC32C

from .mask_store import MaskStoreError
from .paths import os_error_reason
from .tar_io import NotRegularFileError, ShortReadError, open_flags, read_exact_at, regular_stats

METADATA_NAME = "zarr.json"
MAX_METADATA_BYTES = 1024 * 1024
MAX_INDEX_ENTRIES = 1 << 20
_MISSING = (1 << 64) - 1
_BLOSC_CNAMES = frozenset(("blosclz", "lz4", "lz4hc", "zlib", "zstd"))
_BLOSC_SHUFFLES = frozenset(("noshuffle", "shuffle", "bitshuffle"))
_ARRAY_ENTRIES = 256
_LOG = logging.getLogger(__name__)

type Identity = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """What a production run's array must store about itself, as its path names it."""

    plate: str
    well: str
    site: int
    run_id: str


@dataclass(frozen=True, slots=True)
class TimedMaskArray:
    """One field's validated mask array and its shard index, at one revision."""

    label: str
    metadata: Path
    shard: Path
    metadata_identity: Identity
    shard_identity: Identity
    shape: tuple[int, int, int]
    chunk: tuple[int, int]
    index: np.ndarray

    @property
    def timepoints(self) -> int:
        return self.shape[0]

    @property
    def revision(self) -> tuple[int, ...]:
        """The validator part of cache keys and ETags: both files' identities."""
        return (*self.metadata_identity, *self.shard_identity)


def _identity(metadata: os.stat_result) -> Identity:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _invalid(label: str, reason: str) -> MaskStoreError:
    return MaskStoreError(f"TIMED masks {label} are invalid: {reason}")


def _within(candidate: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(candidate), str(parent))) == str(parent)
    except ValueError:
        return False


def confined(path: Path, masks_root: Path) -> Path | None:
    """The resolved path when it exists inside the mask folder, else None."""
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not _within(resolved, masks_root):
        _LOG.warning("Ignoring a TIMED mask path that leaves its mask folder")
        return None
    return resolved


def has_array(array_dir: Path, masks_root: Path) -> bool:
    """Whether an array's metadata file exists as a regular file in the mask folder."""
    resolved = confined(array_dir / METADATA_NAME, masks_root)
    if resolved is None:
        return False
    with suppress(OSError):
        return stat.S_ISREG(os.stat(resolved, follow_symlinks=False).st_mode)
    return False


def _read_metadata(label: str, path: Path) -> tuple[Identity, dict[str, Any]]:
    fd: int | None = None
    try:
        fd = os.open(path, open_flags())
        descriptor_stat, _path_stat = regular_stats(fd, path)
        if descriptor_stat.st_size > MAX_METADATA_BYTES:
            raise _invalid(label, f"{METADATA_NAME} exceeds {MAX_METADATA_BYTES} bytes")
        content = read_exact_at(fd, 0, descriptor_stat.st_size)
    except NotRegularFileError:
        raise _invalid(label, f"{METADATA_NAME} is not a regular file") from None
    except ShortReadError:
        raise MaskStoreError(f"TIMED masks {label} changed while reading") from None
    except OSError as exc:
        raise MaskStoreError(
            f"Could not read TIMED masks {label}: {os_error_reason(exc)}"
        ) from exc
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid(label, f"{METADATA_NAME} is not valid JSON") from None
    if not isinstance(value, dict):
        raise _invalid(label, f"{METADATA_NAME} is not a JSON object")
    return _identity(descriptor_stat), value


def _positive_ints(value: object, length: int) -> tuple[int, ...] | None:
    if (
        not isinstance(value, list)
        or len(value) != length
        or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value)
    ):
        return None
    return tuple(value)


def _codec(value: object, name: str) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("name") != name:
        return None
    configuration = value.get("configuration", {})
    return configuration if isinstance(configuration, dict) else None


def _shard_key(encoding: object) -> str | None:
    """The single shard's file name for a v2 or default chunk-key encoding."""
    if not isinstance(encoding, dict):
        return None
    configuration = encoding.get("configuration", {})
    if not isinstance(configuration, dict):
        return None
    if encoding.get("name") == "v2":
        separator = configuration.get("separator", ".")
        return separator.join(("0", "0", "0")) if separator in (".", "/") else None
    if encoding.get("name") == "default":
        separator = configuration.get("separator", "/")
        return separator.join(("c", "0", "0", "0")) if separator in (".", "/") else None
    return None


def _require[T](value: T | None, label: str, reason: str) -> T:
    if value is None:
        raise _invalid(label, reason)
    return value


def _check_run(label: str, attributes: dict[str, Any], run: RunIdentity) -> None:
    """A production array must store the plate, well, site and run its path names."""
    if attributes.get("axes", ["t", "y", "x"]) != ["t", "y", "x"]:
        raise _invalid(label, "the axes are not t, y, x")
    for name, expected in (
        ("plate", run.plate),
        ("well", run.well),
        ("site", run.site),
        ("run_id", run.run_id),
    ):
        if name not in attributes:
            raise _invalid(label, f"the {name} attribute is missing")
        stored = attributes[name]
        if type(stored) is not type(expected) or stored != expected:
            shown = repr(stored) if isinstance(stored, str | int) else type(stored).__name__
            raise _invalid(
                label, f"the stored {name} {shown[:60]} differs from its path's {expected!r}"
            )


def _validate(
    label: str,
    metadata: dict[str, Any],
    image_shape: tuple[int, ...],
    image_t_indices: tuple[int, ...],
    run: RunIdentity | None = None,
) -> tuple[tuple[int, int, int], tuple[int, int], str]:
    """Return the array's shape, inner chunk plane and shard key, or raise."""
    if metadata.get("zarr_format") != 3 or metadata.get("node_type") != "array":
        raise _invalid(label, "not a Zarr v3 array")
    shape = _require(
        _positive_ints(metadata.get("shape"), 3), label, "the shape is not three positive integers"
    )
    expected = (image_shape[0], image_shape[-2], image_shape[-1])
    if shape != expected:
        raise _invalid(label, f"the shape is {shape}, but the field needs {expected}")
    if metadata.get("data_type") != "uint16":
        raise _invalid(label, "the data type is not uint16")
    if metadata.get("fill_value") != 0:
        raise _invalid(label, "the fill value is not 0")
    attributes = metadata.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    if run is not None:
        _check_run(label, attributes, run)
    elif attributes.get("axes") != ["t", "y", "x"]:
        raise _invalid(label, "the axes are not t, y, x")
    t_indices = attributes.get("t_indices")
    if (
        not isinstance(t_indices, list)
        or any(isinstance(value, bool) or not isinstance(value, int) for value in t_indices)
        or tuple(t_indices) != image_t_indices
    ):
        raise _invalid(label, "the time indices differ from the field's")
    if metadata.get("dimension_names") not in (None, ["t", "y", "x"]):
        raise _invalid(label, "the dimension names are not t, y, x")
    if metadata.get("storage_transformers"):
        raise _invalid(label, "storage transformers are not supported")
    grid = _codec(metadata.get("chunk_grid"), "regular")
    if grid is None or _positive_ints(grid.get("chunk_shape"), 3) != shape:
        raise _invalid(label, "the masks are not one whole-array shard")
    codecs = metadata.get("codecs")
    if not isinstance(codecs, list) or len(codecs) != 1:
        raise _invalid(label, "the array does not use one sharding codec")
    sharding = _require(
        _codec(codecs[0], "sharding_indexed"), label, "the array does not use one sharding codec"
    )
    inner = _require(
        _positive_ints(sharding.get("chunk_shape"), 3),
        label,
        "inner chunks must each hold part of one plane",
    )
    if inner[0] != 1 or inner[1] > shape[1] or inner[2] > shape[2]:
        raise _invalid(label, "inner chunks must each hold part of one plane")
    inner_codecs = sharding.get("codecs")
    if not isinstance(inner_codecs, list) or len(inner_codecs) != 2:
        raise _invalid(label, "inner chunks need the bytes and Blosc codecs")
    byte_order = _codec(inner_codecs[0], "bytes")
    if byte_order is None or byte_order.get("endian") != "little":
        raise _invalid(label, "inner chunks are not little-endian bytes")
    blosc = _codec(inner_codecs[1], "blosc")
    if (
        blosc is None
        or blosc.get("typesize") != 2
        or blosc.get("cname") not in _BLOSC_CNAMES
        or blosc.get("shuffle") not in _BLOSC_SHUFFLES
    ):
        raise _invalid(label, "inner chunks need uint16 Blosc compression")
    index_codecs = sharding.get("index_codecs")
    index_bytes = (
        _codec(index_codecs[0], "bytes")
        if isinstance(index_codecs, list) and len(index_codecs) == 2
        else None
    )
    if (
        index_bytes is None
        or index_bytes.get("endian") != "little"
        or not isinstance(index_codecs, list)
        or _codec(index_codecs[1], "crc32c") is None
        or sharding.get("index_location", "end") != "end"
    ):
        raise _invalid(label, "the shard index must be little-endian with CRC32C, at the end")
    key = _require(
        _shard_key(metadata.get("chunk_key_encoding")),
        label,
        "the chunk-key encoding is not supported",
    )
    return shape, (inner[1], inner[2]), key


def _grid(shape: tuple[int, int, int], chunk: tuple[int, int]) -> tuple[int, int, int]:
    return shape[0], -(-shape[1] // chunk[0]), -(-shape[2] // chunk[1])


def _read_index(
    label: str,
    shard: Path,
    shape: tuple[int, int, int],
    chunk: tuple[int, int],
) -> tuple[Identity, np.ndarray]:
    grid = _grid(shape, chunk)
    entries = grid[0] * grid[1] * grid[2]
    if entries > MAX_INDEX_ENTRIES:
        raise _invalid(label, "the shard index is too large")
    index_bytes = entries * 16
    fd: int | None = None
    try:
        fd = os.open(shard, open_flags())
        descriptor_stat, _path_stat = regular_stats(fd, shard)
        size = descriptor_stat.st_size
        if size < index_bytes + 4:
            raise _invalid(label, "the shard is shorter than its index")
        tail = read_exact_at(fd, size - index_bytes - 4, index_bytes + 4)
    except NotRegularFileError:
        raise _invalid(label, "the shard is not a regular file") from None
    except ShortReadError:
        raise MaskStoreError(f"TIMED masks {label} changed while reading") from None
    except OSError as exc:
        raise MaskStoreError(
            f"Could not read TIMED masks {label}: {os_error_reason(exc)}"
        ) from exc
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
    try:
        body = bytes(CRC32C().decode(tail))
    except RuntimeError:
        raise _invalid(label, "the shard index fails its CRC32C check") from None
    index = np.frombuffer(body, dtype="<u8").reshape(*grid, 2)
    offsets, lengths = index[..., 0], index[..., 1]
    missing = (offsets == _MISSING) & (lengths == _MISSING)
    present = ~missing
    data_end = size - index_bytes - 4
    if np.any(present & ((lengths == 0) | (lengths > data_end))) or np.any(
        present & (offsets > data_end - np.minimum(lengths, data_end))
    ):
        raise _invalid(label, "the shard index points outside the shard")
    index.setflags(write=False)
    return _identity(descriptor_stat), index


def load_array(
    label: str,
    metadata_path: Path,
    masks_root: Path,
    image_shape: tuple[int, ...],
    image_t_indices: tuple[int, ...],
    run: RunIdentity | None = None,
) -> TimedMaskArray:
    """Validate one field's mask array from its resolved metadata file."""
    metadata_identity, metadata = _read_metadata(label, metadata_path)
    shape, chunk, key = _validate(label, metadata, image_shape, image_t_indices, run)
    shard = confined(metadata_path.parent.joinpath(*key.split("/")), masks_root)
    if shard is None:
        raise _invalid(label, "the shard is missing")
    shard_identity, index = _read_index(label, shard, shape, chunk)
    return TimedMaskArray(
        label=label,
        metadata=metadata_path,
        shard=shard,
        metadata_identity=metadata_identity,
        shard_identity=shard_identity,
        shape=shape,
        chunk=chunk,
        index=index,
    )


def _current_identity(path: Path) -> Identity | None:
    try:
        return _identity(os.stat(path, follow_symlinks=False))
    except OSError:
        return None


class TimedMaskArrays:
    """Validated arrays, reused while both their files keep the same identity."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[
            tuple[Path, tuple[int, ...], tuple[int, ...], RunIdentity | None], TimedMaskArray
        ] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get(
        self,
        label: str,
        array_dir: Path,
        masks_root: Path,
        image_shape: tuple[int, ...],
        image_t_indices: tuple[int, ...],
        run: RunIdentity | None = None,
    ) -> TimedMaskArray | None:
        """One field's array; None when it has no metadata inside the mask folder."""
        metadata_path = confined(array_dir / METADATA_NAME, masks_root)
        key = (array_dir, image_shape, image_t_indices, run)
        if metadata_path is None:
            with self._lock:
                self._entries.pop(key, None)
            return None
        with self._lock:
            cached = self._entries.get(key)
        if (
            cached is not None
            and cached.metadata == metadata_path
            and _current_identity(metadata_path) == cached.metadata_identity
            and _current_identity(cached.shard) == cached.shard_identity
        ):
            return cached
        array = load_array(label, metadata_path, masks_root, image_shape, image_t_indices, run)
        with self._lock:
            if key not in self._entries and len(self._entries) >= _ARRAY_ENTRIES:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = array
        return array


def read_plane(array: TimedMaskArray, t: int) -> np.ndarray:
    """Decode plane ``t`` from the shard; raise MaskStoreError when it is unreadable."""
    _timepoints, height, width = array.shape
    chunk_height, chunk_width = array.chunk
    plane = np.zeros((height, width), dtype=np.uint16)
    codec = Blosc()
    fd: int | None = None
    try:
        fd = os.open(array.shard, open_flags())
        descriptor_stat, _path_stat = regular_stats(fd, array.shard)
        if _identity(descriptor_stat) != array.shard_identity:
            raise MaskStoreError(f"TIMED masks {array.label} changed while reading")
        for row in range(array.index.shape[1]):
            for column in range(array.index.shape[2]):
                offset, length = (int(value) for value in array.index[t, row, column])
                if offset == _MISSING and length == _MISSING:
                    continue
                try:
                    raw = codec.decode(read_exact_at(fd, offset, length))
                except (RuntimeError, ValueError) as exc:
                    raise _invalid(
                        array.label, f"chunk ({t}, {row}, {column}) could not be decoded: {exc}"
                    ) from None
                expected = chunk_height * chunk_width * 2
                if len(raw) != expected:
                    raise _invalid(
                        array.label,
                        f"chunk ({t}, {row}, {column}) has {len(raw)} bytes, expected {expected}",
                    )
                block = np.frombuffer(raw, dtype="<u2").reshape(chunk_height, chunk_width)
                top, left = row * chunk_height, column * chunk_width
                plane[top : top + chunk_height, left : left + chunk_width] = block[
                    : height - top, : width - left
                ]
    except NotRegularFileError:
        raise _invalid(array.label, "the shard is not a regular file") from None
    except ShortReadError:
        raise MaskStoreError(f"TIMED masks {array.label} changed while reading") from None
    except OSError as exc:
        raise MaskStoreError(
            f"Could not read TIMED masks {array.label}: {os_error_reason(exc)}"
        ) from exc
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
    plane.setflags(write=False)
    return plane
