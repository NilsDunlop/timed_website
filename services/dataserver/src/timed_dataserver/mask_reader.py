"""Mask models, decoded labels and object counts for packed plates.

The routes call these blocking methods in worker threads. Each store's offset
table is rebuilt whenever its revision or file identity changes, so a store
that grows is picked up. Decoded labels are kept in a byte-bounded LRU keyed by
the member's place in its tar, which append-only stores never rewrite, so a
growing store keeps the masks already decoded.
"""

from __future__ import annotations

import threading
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .caches import ByteBoundedSingleFlightLru
from .mask_catalogue import (
    MaskCatalogue,
    MaskModel,
    MaskPlateFiles,
    masks_root,
    plate_available,
    plate_files,
)
from .mask_store import (
    MaskMember,
    MaskRecord,
    MaskTable,
    StoreRevision,
    decode_labels,
    load_table,
    read_member,
    read_state,
)
from .packed_profiles import PackedMasks

DEFAULT_MASK_CACHE_BYTES = 512 * 1024 * 1024
DEFAULT_MASK_WORKERS = 4
_STORE_TABLE_ENTRIES = 64


class MaskNotFoundError(Exception):
    """Raised when a model, field, record or kind has no mask to serve."""


@dataclass(frozen=True, slots=True)
class MaskModelEntry:
    """One model as the listing reports it for a plate."""

    model: MaskModel
    available: bool
    default: bool


@dataclass(frozen=True, slots=True)
class DecodedMask:
    """A mask's labels; objects run from 1, so the largest label is the count."""

    labels: np.ndarray
    count: int


@dataclass(frozen=True, slots=True)
class MaskTarget:
    """One committed mask resolved against its store's current revision."""

    files: MaskPlateFiles
    table: MaskTable
    record: MaskRecord
    member: MaskMember

    @property
    def revision(self) -> StoreRevision:
        return self.table.revision


@dataclass(frozen=True, slots=True)
class _MaskKey:
    tar: str
    tar_identity: tuple[int, int]
    offset: int
    size: int
    name: str
    shape: tuple[int, int]


def _decoded_bytes(mask: DecodedMask) -> int:
    return mask.labels.nbytes


class MaskReader:
    """Serve one application's mask stores synchronously and thread-safely."""

    def __init__(
        self,
        *,
        cache_bytes: int = DEFAULT_MASK_CACHE_BYTES,
        max_workers: int = DEFAULT_MASK_WORKERS,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("Mask decoding concurrency must be positive")
        self.cache: ByteBoundedSingleFlightLru[Hashable, DecodedMask] = (
            ByteBoundedSingleFlightLru(cache_bytes, _decoded_bytes)
        )
        self.catalogue = MaskCatalogue()
        self._slots = threading.BoundedSemaphore(max_workers)
        self._tables_lock = threading.Lock()
        self._tables: dict[tuple[Path, Path], MaskTable] = {}
        self._table_locks: dict[tuple[Path, Path], threading.Lock] = {}

    def close(self) -> None:
        """Forget cached tables and labels; called when the application stops."""
        with self._tables_lock:
            self._tables.clear()
            self._table_locks.clear()
        self.cache.clear()

    def models(
        self,
        index_path: Path,
        masks: PackedMasks,
        experiment: str,
        plate: int,
    ) -> list[MaskModelEntry]:
        """The declared default model first, then every other model by tag."""
        root = masks_root(index_path, masks)
        if root is None:
            return []
        entries = [
            MaskModelEntry(
                model=model,
                available=plate_available(plate_files(model, experiment, plate)),
                default=model.tag == masks.default_model,
            )
            for model in self.catalogue.models(root)
        ]
        return sorted(entries, key=lambda entry: (not entry.default, entry.model.tag))

    def plate(
        self,
        index_path: Path,
        masks: PackedMasks,
        model_tag: str,
        experiment: str,
        plate: int,
    ) -> tuple[MaskPlateFiles, MaskTable]:
        """One model's store for a plate and its current offset table."""
        root = masks_root(index_path, masks)
        model = None if root is None else self.catalogue.model(root, model_tag)
        if model is None:
            raise MaskNotFoundError(f"Unknown mask model: {model_tag}")
        files = plate_files(model, experiment, plate)
        if files is None or not plate_available(files):
            raise MaskNotFoundError(f"Mask model {model_tag} does not cover this plate")
        return files, self._table(files)

    def _table(self, files: MaskPlateFiles) -> MaskTable:
        key = (files.log, files.tar)
        with self._tables_lock:
            lock = self._table_locks.setdefault(key, threading.Lock())
        with lock:
            state = read_state(files)
            with self._tables_lock:
                cached = self._tables.get(key)
            if (
                cached is not None
                and cached.revision == state.revision
                and cached.log_identity == state.log_identity
                and cached.tar_identity == state.tar_identity
            ):
                return cached
            table = load_table(files, state=state)
            with self._tables_lock:
                if key not in self._tables and len(self._tables) >= _STORE_TABLE_ENTRIES:
                    evicted = next(iter(self._tables))
                    self._tables.pop(evicted)
                    self._table_locks.pop(evicted, None)
                self._tables[key] = table
            return table

    def target(
        self,
        files: MaskPlateFiles,
        table: MaskTable,
        well: str,
        site: int,
        kind: str,
    ) -> MaskTarget:
        """The committed member for one field and kind."""
        record = self.record(table, well, site)
        member = record.members.get(kind)
        if member is None:
            raise MaskNotFoundError(f"No {kind} mask for {well} site {site}")
        return MaskTarget(files=files, table=table, record=record, member=member)

    @staticmethod
    def record(table: MaskTable, well: str, site: int) -> MaskRecord:
        record = table.record(well, site)
        if record is None:
            raise MaskNotFoundError(f"No masks for {well} site {site}")
        return record

    def labels(self, target: MaskTarget, shape: tuple[int, int]) -> DecodedMask:
        """Decoded labels for one member, through the single-flight LRU."""
        key = _MaskKey(
            tar=str(target.files.tar),
            tar_identity=target.table.tar_identity,
            offset=target.member.offset,
            size=target.member.size,
            name=f"{target.record.well}_s{target.record.site}_{target.member.kind}",
            shape=shape,
        )

        def load() -> DecodedMask:
            with self._slots:
                payload = read_member(target.files, target.table, target.record, target.member)
                labels = decode_labels(target.files, target.member.kind, payload, shape)
            return DecodedMask(labels=labels, count=int(labels.max(initial=0)))

        decoded, _status = self.cache.get_or_load(key, load)
        return decoded

    def counts(
        self,
        files: MaskPlateFiles,
        table: MaskTable,
        well: str,
        site: int,
        shape: tuple[int, int],
    ) -> dict[str, int]:
        """Every kind's object count for one field, decoding into the shared cache."""
        record = self.record(table, well, site)
        return {
            kind: self.labels(MaskTarget(files, table, record, member), shape).count
            for kind, member in record.members.items()
        }
