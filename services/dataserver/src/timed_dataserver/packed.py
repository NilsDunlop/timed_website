"""Detection and catalogue primitives for packed microscopy indexes.

The HTTP adapter deliberately lives elsewhere.  This module owns the blocking
Parquet work and exposes async wrappers so callers can keep it off the event
loop.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import anyio
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .paths import PathForbiddenError, PathNotFoundError, RootGuard

PACKED_INDEX_SUFFIX = ".index.parquet"
REQUIRED_INDEX_COLUMNS = (
    "relpath",
    "experiment",
    "plate",
    "well",
    "site",
    "channel",
    "offset",
    "size",
)
_STRING_COLUMNS = ("relpath", "experiment", "well")
_INTEGER_COLUMNS = ("plate", "site", "channel", "offset", "size")
_DICTIONARY_COLUMNS = frozenset(("experiment", "well", "shard"))
_WINDOWS_DEVICE_NAMES = frozenset(("CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"))
_WINDOWS_NUMBERED_DEVICE = re.compile(r"(?:COM|LPT)[1-9]")


class PackedIndexError(ValueError):
    """Raised when a detected packed index cannot form a safe catalogue."""


@dataclass(frozen=True, slots=True)
class IndexSignature:
    """The file identity used by the detection and catalogue caches."""

    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class PackedMember:
    """One channel member in a packed tar shard."""

    tar: Path
    offset: int
    size: int
    relpath: str


@dataclass(frozen=True, slots=True)
class PackedPlate:
    """The dimensions present for one experiment and plate."""

    wells: tuple[str, ...]
    sites: tuple[int, ...]
    channels: tuple[int, ...]


FieldKey = tuple[str, int, str, int]
PlateKey = tuple[str, int]


@dataclass(frozen=True, slots=True)
class PackedCatalogue:
    """Immutable grouped view of one packed index."""

    index_path: Path
    signature: IndexSignature
    row_count: int
    plates: Mapping[PlateKey, PackedPlate]
    fields: Mapping[FieldKey, Mapping[int, PackedMember]]

    @property
    def experiments(self) -> tuple[str, ...]:
        return tuple(sorted({experiment for experiment, _ in self.plates}))

    def plates_for(self, experiment: str) -> tuple[int, ...]:
        return tuple(sorted(plate for candidate, plate in self.plates if candidate == experiment))

    def plate(self, experiment: str, plate: int) -> PackedPlate | None:
        return self.plates.get((experiment, plate))

    def field(
        self, experiment: str, plate: int, well: str, site: int
    ) -> Mapping[int, PackedMember] | None:
        return self.fields.get((experiment, plate, well, site))


@dataclass(frozen=True, slots=True)
class PackedVirtualPath:
    """A validated path into an index's virtual experiment/plate hierarchy."""

    index_path: Path
    catalogue: PackedCatalogue
    experiment: str | None = None
    plate: int | None = None

    @property
    def registry_key(self) -> str:
        parts = [str(self.index_path)]
        if self.experiment is not None:
            parts.append(self.experiment)
        if self.plate is not None:
            parts.append(str(self.plate))
        return os.path.join(*parts)


def _signature(path: Path) -> IndexSignature:
    stat = path.stat()
    return IndexSignature(mtime_ns=stat.st_mtime_ns, size=stat.st_size)


def _is_string_type(value: pa.DataType) -> bool:
    if pa.types.is_string(value) or pa.types.is_large_string(value):
        return True
    return pa.types.is_dictionary(value) and (
        pa.types.is_string(value.value_type) or pa.types.is_large_string(value.value_type)
    )


def _valid_schema(schema: pa.Schema) -> bool:
    try:
        names = tuple(schema.names)
        if len(names) != len(set(names)):
            return False
        if any(name not in names for name in REQUIRED_INDEX_COLUMNS):
            return False
        if any(not _is_string_type(schema.field(name).type) for name in _STRING_COLUMNS):
            return False
        if any(not pa.types.is_integer(schema.field(name).type) for name in _INTEGER_COLUMNS):
            return False
        return "shard" not in names or _is_string_type(schema.field("shard").type)
    except (IndexError, KeyError, TypeError, ValueError, pa.ArrowException):
        return False


def _fallback_tar(index_path: Path) -> Path:
    name = index_path.name.removesuffix(PACKED_INDEX_SUFFIX)
    return index_path.with_name(f"{name}.tar")


def _adjacent_file(index_path: Path, name: str) -> Path | None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    candidate = index_path.parent / name
    try:
        resolved = candidate.resolve(strict=True)
        parent = index_path.parent.resolve(strict=True)
    except OSError:
        return None
    if resolved.parent != parent or not resolved.is_file():
        return None
    return resolved


def _tar_layout(
    index_path: Path, shard_values: list[str | None] | None
) -> tuple[Path | None, Mapping[str, Path]] | None:
    if shard_values:
        names = set(shard_values)
        if None not in names:
            shards = {name: _adjacent_file(index_path, name) for name in names if name is not None}
            if shards and all(path is not None for path in shards.values()):
                return None, MappingProxyType(
                    {name: path for name, path in shards.items() if path is not None}
                )

    fallback = _adjacent_file(index_path, _fallback_tar(index_path).name)
    if fallback is not None:
        return fallback, MappingProxyType({})
    return None


def _detect_uncached(path: Path) -> bool:
    if not path.name.endswith(PACKED_INDEX_SUFFIX) or not path.is_file():
        return False
    try:
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        if parquet.metadata.num_rows == 0 or not _valid_schema(schema):
            return False
        shards: list[str | None] | None = None
        if "shard" in schema.names:
            shard_column = pq.read_table(
                path,
                columns=["shard"],
                read_dictionary=["shard"],
            )["shard"]
            shards = pc.unique(shard_column).to_pylist()
        return _tar_layout(path, shards) is not None
    except (IndexError, KeyError, OSError, ValueError, pa.ArrowException):
        return False


@lru_cache(maxsize=256)
def _detect_cached(path: str, mtime_ns: int, size: int) -> bool:
    del mtime_ns, size
    return _detect_uncached(Path(path))


def is_packed_index(path: Path) -> bool:
    """Return whether *path* is a packed index, cached by path and file stat."""
    try:
        resolved = path.expanduser().resolve(strict=True)
        signature = _signature(resolved)
    except OSError:
        return False
    return _detect_cached(str(resolved), signature.mtime_ns, signature.size)


def clear_detection_cache() -> None:
    """Clear packed-index detection state (primarily useful to isolated tests)."""
    _detect_cached.cache_clear()


def _column_values(table: pa.Table, name: str) -> list[object]:
    values = table[name].combine_chunks()
    if name in _DICTIONARY_COLUMNS and not pa.types.is_dictionary(values.type):
        values = values.dictionary_encode()
    return values.to_pylist()


def _dictionary_encode_columns(table: pa.Table) -> pa.Table:
    """Guarantee compact Arrow dictionaries for repeated catalogue strings."""
    for name in _DICTIONARY_COLUMNS.intersection(table.column_names):
        index = table.schema.get_field_index(name)
        column = table.column(index)
        if not pa.types.is_dictionary(column.type):
            table = table.set_column(index, name, pc.dictionary_encode(column))
    return table


def _group_plate_dimensions(table: pa.Table) -> dict[PlateKey, PackedPlate]:
    """Group plate dimensions with Arrow rather than row-wise Python sets."""
    grouping_columns: dict[str, pa.ChunkedArray] = {}
    for name in ("experiment", "plate", "well", "site", "channel"):
        column = table[name]
        grouping_columns[name] = (
            pc.dictionary_decode(column) if pa.types.is_dictionary(column.type) else column
        )
    grouped = (
        pa.table(grouping_columns)
        .group_by(["experiment", "plate"], use_threads=False)
        .aggregate(
            [
                ("well", "hash_distinct"),
                ("site", "hash_distinct"),
                ("channel", "hash_distinct"),
            ]
        )
    )
    return {
        (str(row["experiment"]), int(row["plate"])): PackedPlate(
            wells=tuple(sorted(str(value) for value in row["well_distinct"])),
            sites=tuple(sorted(int(value) for value in row["site_distinct"])),
            channels=tuple(sorted(int(value) for value in row["channel_distinct"])),
        )
        for row in grouped.to_pylist()
    }


def _is_safe_component(value: str) -> bool:
    if (
        not value
        or value in {".", ".."}
        or value.endswith((" ", "."))
        or any(character in value for character in ("/", "\\", ":", "\x00"))
    ):
        return False
    device_stem = value.rstrip(" .").split(".", maxsplit=1)[0].upper()
    return (
        device_stem not in _WINDOWS_DEVICE_NAMES
        and _WINDOWS_NUMBERED_DEVICE.fullmatch(device_stem) is None
    )


def _is_safe_relpath(value: str) -> bool:
    if not value or "\\" in value or "\x00" in value:
        return False
    logical = PurePosixPath(value)
    return not logical.is_absolute() and all(
        part not in {"", ".", ".."} for part in value.split("/")
    )


def _load_catalogue(index_path: Path, signature: IndexSignature) -> PackedCatalogue:
    try:
        parquet = pq.ParquetFile(index_path)
        schema = parquet.schema_arrow
        if not _valid_schema(schema):
            raise PackedIndexError(f"Packed index schema changed while loading: {index_path}")
        columns = list(REQUIRED_INDEX_COLUMNS)
        if "shard" in schema.names:
            columns.append("shard")
        dictionaries = sorted(_DICTIONARY_COLUMNS.intersection(columns))
        table = _dictionary_encode_columns(
            pq.read_table(index_path, columns=columns, read_dictionary=dictionaries)
        )
    except PackedIndexError:
        raise
    except (IndexError, KeyError, OSError, ValueError, pa.ArrowException) as exc:
        raise PackedIndexError(f"Could not read packed index {index_path}: {exc}") from exc

    null_columns = [name for name in REQUIRED_INDEX_COLUMNS if table[name].null_count]
    if null_columns:
        joined = ", ".join(null_columns)
        raise PackedIndexError(f"Packed index has null required values in {joined}: {index_path}")

    values = {name: _column_values(table, name) for name in columns}
    shard_values = values.get("shard")
    layout = _tar_layout(
        index_path,
        shard_values if isinstance(shard_values, list) else None,
    )
    if layout is None:
        raise PackedIndexError(f"Packed index has no adjacent tar shards: {index_path}")
    fallback_tar, shard_paths = layout

    mutable_fields: dict[FieldKey, dict[int, PackedMember]] = {}
    seen: set[tuple[str, int, str, int, int]] = set()
    for row in range(table.num_rows):
        experiment = str(values["experiment"][row])
        plate = int(values["plate"][row])
        well = str(values["well"][row])
        site = int(values["site"][row])
        channel = int(values["channel"][row])
        relpath = str(values["relpath"][row])
        if not _is_safe_component(experiment):
            raise PackedIndexError(
                f"Packed index has unsafe experiment component {experiment!r} at row {row}"
            )
        if not _is_safe_component(well):
            raise PackedIndexError(f"Packed index has unsafe well component {well!r} at row {row}")
        if not _is_safe_relpath(relpath):
            raise PackedIndexError(f"Packed index has unsafe relpath {relpath!r} at row {row}")
        member_key = (experiment, plate, well, site, channel)
        if member_key in seen:
            raise PackedIndexError(f"Packed index has duplicate field/channel key {member_key}")
        seen.add(member_key)

        if fallback_tar is not None:
            tar = fallback_tar
        else:
            shard = values["shard"][row]
            if not isinstance(shard, str) or shard not in shard_paths:
                raise PackedIndexError(f"Packed index row has no adjacent shard: {member_key}")
            tar = shard_paths[shard]
        field_key = (experiment, plate, well, site)
        mutable_fields.setdefault(field_key, {})[channel] = PackedMember(
            tar=tar,
            offset=int(values["offset"][row]),
            size=int(values["size"][row]),
            relpath=relpath,
        )

    plates = MappingProxyType(_group_plate_dimensions(table))
    fields = MappingProxyType(
        {
            key: MappingProxyType(dict(sorted(channels.items())))
            for key, channels in mutable_fields.items()
        }
    )
    return PackedCatalogue(
        index_path=index_path,
        signature=signature,
        row_count=table.num_rows,
        plates=plates,
        fields=fields,
    )


@dataclass(slots=True)
class _CatalogueEntry:
    lock: threading.Lock
    signature: IndexSignature | None = None
    catalogue: PackedCatalogue | None = None


class PackedCatalogueCache:
    """Thread-safe, lazy catalogue cache with stat-based invalidation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[Path, _CatalogueEntry] = {}

    def get(self, index_path: Path) -> PackedCatalogue:
        try:
            resolved = index_path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise PackedIndexError(f"Packed index does not exist: {index_path}") from exc
        if not is_packed_index(resolved):
            raise PackedIndexError(f"Not a packed image index: {resolved}")
        with self._lock:
            entry = self._entries.setdefault(resolved, _CatalogueEntry(threading.Lock()))

        with entry.lock:
            for _ in range(3):
                try:
                    before = _signature(resolved)
                except OSError as exc:
                    raise PackedIndexError(
                        f"Packed index disappeared while loading: {resolved}"
                    ) from exc
                if entry.signature == before and entry.catalogue is not None:
                    return entry.catalogue
                catalogue = _load_catalogue(resolved, before)
                try:
                    after = _signature(resolved)
                except OSError as exc:
                    raise PackedIndexError(
                        f"Packed index disappeared while loading: {resolved}"
                    ) from exc
                if before == after:
                    entry.signature = after
                    entry.catalogue = catalogue
                    return catalogue
            raise PackedIndexError(f"Packed index changed repeatedly while loading: {resolved}")

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


PACKED_CATALOGUES = PackedCatalogueCache()


def get_catalogue(
    index_path: Path, *, cache: PackedCatalogueCache = PACKED_CATALOGUES
) -> PackedCatalogue:
    """Load or retrieve a catalogue synchronously.

    HTTP call sites should use :func:`get_catalogue_in_thread` instead.
    """
    return cache.get(index_path)


async def get_catalogue_in_thread(
    index_path: Path, *, cache: PackedCatalogueCache = PACKED_CATALOGUES
) -> PackedCatalogue:
    """Load or retrieve a catalogue without blocking the event loop."""
    return await anyio.to_thread.run_sync(partial(cache.get, index_path))


def _split_virtual_path(raw: str) -> tuple[str, tuple[str, ...]] | None:
    if "\x00" in raw or ".." in raw.replace("\\", "/").split("/"):
        raise PathForbiddenError("Parent path segments are not allowed")
    path = Path(raw).expanduser()
    parts = path.parts
    index_part = next(
        (
            position
            for position in range(len(parts) - 1, -1, -1)
            if parts[position].endswith(PACKED_INDEX_SUFFIX)
        ),
        None,
    )
    if index_part is None:
        return None
    tail = tuple(parts[index_part + 1 :])
    return str(Path(*parts[: index_part + 1])), tail


def resolve_packed_virtual_path(
    raw: str,
    guard: RootGuard,
    *,
    cache: PackedCatalogueCache = PACKED_CATALOGUES,
) -> PackedVirtualPath | None:
    """Resolve and validate an index, experiment, or plate virtual path.

    ``None`` means the raw path contains no packed-index component and should
    follow the ordinary filesystem path.
    """
    split = _split_virtual_path(raw)
    if split is None:
        return None
    index_raw, tail = split
    index_path = guard.resolve(index_raw)
    if len(tail) > 2 or any(part in {"", ".", ".."} for part in tail):
        raise PathNotFoundError(f"Unknown packed dataset path: {raw}")
    if not is_packed_index(index_path):
        raise PathNotFoundError(f"Not a packed image index: {index_path}")
    catalogue = cache.get(index_path)
    if not tail:
        return PackedVirtualPath(index_path=index_path, catalogue=catalogue)

    experiment = tail[0]
    if experiment not in catalogue.experiments:
        raise PathNotFoundError(f"Unknown packed experiment: {experiment}")
    if len(tail) == 1:
        return PackedVirtualPath(
            index_path=index_path,
            catalogue=catalogue,
            experiment=experiment,
        )

    if re.fullmatch(r"[0-9]+", tail[1]) is None:
        raise PathNotFoundError(f"Unknown packed plate: {tail[1]}")
    plate = int(tail[1])
    if catalogue.plate(experiment, plate) is None:
        raise PathNotFoundError(f"Unknown packed plate: {tail[1]}")
    return PackedVirtualPath(
        index_path=index_path,
        catalogue=catalogue,
        experiment=experiment,
        plate=plate,
    )


async def resolve_packed_virtual_path_in_thread(
    raw: str,
    guard: RootGuard,
    *,
    cache: PackedCatalogueCache = PACKED_CATALOGUES,
) -> PackedVirtualPath | None:
    """Worker-thread wrapper around :func:`resolve_packed_virtual_path`."""
    return await anyio.to_thread.run_sync(
        partial(resolve_packed_virtual_path, raw, guard, cache=cache)
    )
