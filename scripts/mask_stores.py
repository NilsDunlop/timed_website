"""Deterministic tar+wal/v1 mask stores for the idr0080-like packed fixture.

The stores follow the mask-store contract in docs/data/IDR0080.md. A model is a
folder with ``manifest.json``. Each plate has a tar of 512-byte USTAR members, a
JSON Lines log with one committed field per complete line and a writer lease;
a finalised plate also has an index snapshot and a QC table, and its tar ends
with end-of-archive padding. Labels are known discs (nuclei) and squares
(cells) numbered contiguously from 1, so every object count is known.

``write_plate_store`` also writes the irregular stores the readers must
handle: a field rewritten later, a half-written last log line with its members
already in the tar, a tar shorter than its log's committed length, and an
unreadable line in the middle of a log.
"""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import tifffile

DEFAULT_MODEL = "idr0080_cpsam_B90"
STOCK_MODEL = "cpsam_stock_R2_top999"
OTHER_FORMAT_MODEL = "fixture_zarr_masks"
KINDS = ("nuclei", "cells", "cytoplasm")
STORE_FORMAT = "tar+wal/v1"
GIT_SHA = "f1c7a5e0d2b4c6a8e0f1c7a5e0d2b4c6a8e0f1c7"
TAR_BLOCK = 512
TAR_RECORD = 20 * TAR_BLOCK
GRID_CENTERS = (11, 32, 53)
GRID_POSITIONS = tuple((y, x) for y in GRID_CENTERS for x in GRID_CENTERS)
NUCLEUS_RADIUS = 5
CELL_HALF_SIZES = {DEFAULT_MODEL: 8, STOCK_MODEL: 9}

# The fixture's irregular fields: a rewrite on 14610 and a write in progress on 14611.
REWRITTEN_FIELD = (14610, "B01", 2)
IN_PROGRESS_FIELD = (14611, "A02", 1)
IN_PROGRESS_PLATE = 14611

_MANIFESTS = {
    DEFAULT_MODEL: {
        "created_at": "2026-10-15T15:54:43+0200",
        "recipe": {"name": "IDR0080_B90", "cells": [["ER"], ["AGP"]], "nuclei": [["DNA"]]},
    },
    STOCK_MODEL: {
        "created_at": "2026-10-14T03:08:39+0200",
        "recipe": {"name": "R2", "cells": [["ER"], ["AGP"], ["DNA"]], "nuclei": [["DNA"]]},
    },
}


@dataclass(frozen=True)
class FieldMasks:
    """One field's label images by kind; 0 is background, objects run from 1."""

    labels: Mapping[str, np.ndarray]

    @property
    def counts(self) -> dict[str, int]:
        return {kind: int(self.labels[kind].max()) for kind in KINDS}


@dataclass(frozen=True)
class PlateStore:
    """What one written plate store contains, for tests to compare against."""

    model: str
    experiment: str
    plate: int
    directory: Path
    records: tuple[dict[str, Any], ...]
    counts: Mapping[tuple[str, int], Mapping[str, int]]
    pending_line: bytes | None = None
    partial_bytes: int = 0

    @property
    def stem(self) -> str:
        return f"Plate{self.plate}"

    @property
    def tar(self) -> Path:
        return self.directory / f"{self.stem}.masks.tar"

    @property
    def log(self) -> Path:
        return self.directory / f"{self.stem}.wal.jsonl"

    @property
    def index(self) -> Path:
        return self.directory / f"{self.stem}.index.parquet"

    @property
    def qc(self) -> Path:
        return self.directory / f"{self.stem}.qc.parquet"

    @property
    def lease(self) -> Path:
        return self.directory / f"{self.stem}.lease"


@dataclass
class _StoreBuilder:
    experiment: str
    plate: int
    tar: bytearray = field(default_factory=bytearray)
    lines: list[bytes] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)

    def append_members(self, well: str, site: int, masks: FieldMasks) -> dict[str, Any]:
        members = []
        for kind in KINDS:
            payload = _tiff_bytes(masks.labels[kind])
            name = f"{self.experiment}/Plate{self.plate}/{well}_s{site}_{kind}.tiff"
            offset = len(self.tar) + TAR_BLOCK
            self.tar += _tar_member(name, payload)
            members.append({"kind": kind, "offset": offset, "size": len(payload)})
        counts = masks.counts
        return {
            "well": well,
            "site": site,
            "tar_end": len(self.tar),
            "masks": members,
            "qc": {
                "experiment": self.experiment,
                "plate": self.plate,
                "well": well,
                "site": site,
                "nuclei_final": counts["nuclei"],
                "cell_final": counts["cells"],
                "cytoplasm_final": counts["cytoplasm"],
            },
            "git_sha": GIT_SHA,
        }

    def commit(self, well: str, site: int, masks: FieldMasks) -> None:
        record = self.append_members(well, site, masks)
        self.records.append(record)
        self.lines.append(_log_line(record))


def _tiff_bytes(labels: np.ndarray) -> bytes:
    output = io.BytesIO()
    tifffile.imwrite(
        output,
        labels,
        photometric="minisblack",
        compression="zlib",
        metadata=None,
    )
    return output.getvalue()


def _tar_member(name: str, payload: bytes) -> bytes:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    header = info.tobuf(format=tarfile.USTAR_FORMAT, encoding="utf-8", errors="strict")
    return header + payload + b"\0" * ((-len(payload)) % TAR_BLOCK)


def _end_of_archive(length: int) -> bytes:
    """Two zero blocks, then zeros up to the next 10,240-byte record, as tarfile writes."""
    padded = length + 2 * TAR_BLOCK
    return b"\0" * (2 * TAR_BLOCK + (-padded) % TAR_RECORD)


def _log_line(record: Mapping[str, Any]) -> bytes:
    return (json.dumps(record) + "\n").encode("utf-8")


def _render(
    shape: tuple[int, int],
    nuclei: Sequence[tuple[int, int]],
    cells: Sequence[tuple[int, int]],
    cell_half: int,
) -> FieldMasks:
    y, x = np.indices(shape)
    nucleus_labels = np.zeros(shape, dtype=np.uint16)
    for label, (center_y, center_x) in enumerate(nuclei, start=1):
        disc = (y - center_y) ** 2 + (x - center_x) ** 2 <= NUCLEUS_RADIUS**2
        nucleus_labels[disc] = label
    cell_labels = np.zeros(shape, dtype=np.uint16)
    for label, (center_y, center_x) in enumerate(cells, start=1):
        cell_labels[
            center_y - cell_half : center_y + cell_half + 1,
            center_x - cell_half : center_x + cell_half + 1,
        ] = label
    cytoplasm = np.where(nucleus_labels == 0, cell_labels, 0).astype(np.uint16)
    return FieldMasks({"nuclei": nucleus_labels, "cells": cell_labels, "cytoplasm": cytoplasm})


def field_masks(
    model: str,
    plate: int,
    well_index: int,
    site: int,
    shape: tuple[int, int],
) -> FieldMasks:
    """The committed masks of one field.

    Nuclei sit on a 3 x 3 grid in a field-specific order. The default model gives
    some nuclei no cell, as the real one does; the stock model adds cells without
    a nucleus. Cytoplasm is each cell minus its nucleus, under the cell's label.
    """
    seed = (plate - 14600) * 7 + well_index * 3 + site
    order = [GRID_POSITIONS[(seed + step) % len(GRID_POSITIONS)] for step in range(9)]
    nuclei = 3 + seed % 5
    if model == STOCK_MODEL:
        cells = min(len(order), nuclei + 1 + seed % 2)
    else:
        cells = nuclei - 1 - seed % 2
    return _render(shape, order[:nuclei], order[:cells], CELL_HALF_SIZES[model])


def superseded_masks(shape: tuple[int, int]) -> FieldMasks:
    """A first segmentation that a later record replaces: one small object."""
    return _render(shape, GRID_POSITIONS[:1], GRID_POSITIONS[:1], 6)


def in_progress_masks(shape: tuple[int, int]) -> FieldMasks:
    """A rewrite whose members are in the tar but whose log line is unfinished."""
    return _render(shape, GRID_POSITIONS, GRID_POSITIONS, 8)


def write_manifest(model_dir: Path, model: str, *, store_format: str = STORE_FORMAT) -> None:
    facts = _MANIFESTS.get(model, _MANIFESTS[DEFAULT_MODEL])
    manifest = {
        "model_tag": model,
        "created_at": facts["created_at"],
        "frozen": False,
        "model": {"name": "cpsam"},
        "recipe": facts["recipe"],
        "schema": {
            "mask_dtype": "uint16",
            "kinds": list(KINDS),
            "store_format": store_format,
            "qc_schema_version": 1,
        },
    }
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _final_records(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    final: dict[tuple[str, int], Mapping[str, Any]] = {}
    for record in records:
        key = (record["well"], record["site"])
        final.pop(key, None)
        final[key] = record
    return list(final.values())


def _write_snapshots(store: PlateStore, records: Sequence[Mapping[str, Any]]) -> None:
    final = _final_records(records)
    index_rows = [
        {
            "experiment": store.experiment,
            "plate": store.plate,
            "well": record["well"],
            "site": record["site"],
            "kind": member["kind"],
            "offset": member["offset"],
            "size": member["size"],
        }
        for record in final
        for member in record["masks"]
    ]
    index_schema = pa.schema(
        [
            ("experiment", pa.string()),
            ("plate", pa.int64()),
            ("well", pa.string()),
            ("site", pa.int64()),
            ("kind", pa.string()),
            ("offset", pa.int64()),
            ("size", pa.int64()),
        ]
    )
    pq.write_table(pa.Table.from_pylist(index_rows, schema=index_schema), store.index)
    qc_rows = [dict(record["qc"]) for record in final]
    qc_schema = pa.schema(
        [
            ("experiment", pa.string()),
            ("plate", pa.int64()),
            ("well", pa.string()),
            ("site", pa.int64()),
            ("nuclei_final", pa.int64()),
            ("cell_final", pa.int64()),
            ("cytoplasm_final", pa.int64()),
        ]
    )
    pq.write_table(pa.Table.from_pylist(qc_rows, schema=qc_schema), store.qc)


def _write_lease(store: PlateStore) -> None:
    lease = {
        "pid": 4242,
        "host": "fixture-node",
        "slurm_job_id": f"42{store.plate}",
        "slurm_array_task_id": "0",
        "acquired_at": "2026-10-15T15:54:39+0200",
    }
    store.lease.write_text(json.dumps(lease) + "\n", encoding="utf-8")


def write_plate_store(
    model_dir: Path,
    model: str,
    experiment: str,
    plate: int,
    fields: Sequence[tuple[str, int, FieldMasks]],
    *,
    in_progress: tuple[str, int, FieldMasks] | None = None,
    finalised: bool = True,
    tar_shortfall: int = 0,
    broken_line: int | None = None,
) -> PlateStore:
    """Write one plate store from fields in commit order.

    A field listed twice is rewritten: its later record wins. ``in_progress``
    appends a field's members, then only the first half of its log line.
    ``finalised`` adds end-of-archive padding and the two snapshots.
    ``tar_shortfall`` cuts the tar that many bytes short of the log's committed
    length, and ``broken_line`` replaces that log line with invalid JSON; both
    make the store corrupt.
    """
    builder = _StoreBuilder(experiment, plate)
    for well, site, masks in fields:
        builder.commit(well, site, masks)
    pending_line: bytes | None = None
    partial_bytes = 0
    if in_progress is not None:
        well, site, masks = in_progress
        pending_line = _log_line(builder.append_members(well, site, masks))
        partial_bytes = len(pending_line) // 2

    counts: dict[tuple[str, int], Mapping[str, int]] = {}
    for well, site, masks in fields:
        counts[(well, site)] = masks.counts
    store = PlateStore(
        model=model,
        experiment=experiment,
        plate=plate,
        directory=model_dir / experiment,
        records=tuple(builder.records),
        counts=counts,
        pending_line=pending_line,
        partial_bytes=partial_bytes,
    )
    store.directory.mkdir(parents=True, exist_ok=True)

    tar = bytes(builder.tar)
    if finalised and in_progress is None:
        tar += _end_of_archive(len(tar))
    if tar_shortfall:
        committed = max(int(record["tar_end"]) for record in builder.records)
        tar = tar[: committed - tar_shortfall]
    store.tar.write_bytes(tar)

    lines = list(builder.lines)
    if broken_line is not None:
        lines[broken_line] = b'{"well": "A01", "site": \n'
    log = b"".join(lines)
    if pending_line is not None:
        log += pending_line[:partial_bytes]
    store.log.write_bytes(log)

    if finalised and in_progress is None:
        _write_snapshots(store, builder.records)
    _write_lease(store)
    return store


def write_fixture_mask_stores(
    root: Path,
    *,
    plates: Sequence[tuple[str, int, str]],
    wells: Sequence[str],
    sites: Sequence[int],
    shape: tuple[int, int],
) -> dict[tuple[str, int], PlateStore]:
    """Write the fixture's mask models under ``root/masks``.

    The default model covers every plate: on 14610 one field is rewritten, and on
    14611 a rewrite is in progress. The stock model covers 14610 only, and a
    third folder declares another store format, which readers skip.
    """
    masks_root = root / "masks"
    stores: dict[tuple[str, int], PlateStore] = {}
    for model in (DEFAULT_MODEL, STOCK_MODEL):
        write_manifest(masks_root / model, model)
    write_manifest(masks_root / OTHER_FORMAT_MODEL, OTHER_FORMAT_MODEL, store_format="zarr/v0")

    for experiment, plate, _cell_line in plates:
        models = (DEFAULT_MODEL, STOCK_MODEL) if plate == 14610 else (DEFAULT_MODEL,)
        for model in models:
            fields = [
                (well, site, field_masks(model, plate, well_index, site, shape))
                for well_index, well in enumerate(wells)
                for site in sites
            ]
            in_progress = None
            if model == DEFAULT_MODEL and plate == REWRITTEN_FIELD[0]:
                _plate, well, site = REWRITTEN_FIELD
                position = next(
                    index
                    for index, (candidate_well, candidate_site, _masks) in enumerate(fields)
                    if (candidate_well, candidate_site) == (well, site)
                )
                final = fields[position]
                fields[position] = (well, site, superseded_masks(shape))
                fields.append(final)
            if model == DEFAULT_MODEL and plate == IN_PROGRESS_PLATE:
                _plate, well, site = IN_PROGRESS_FIELD
                in_progress = (well, site, in_progress_masks(shape))
            stores[(model, plate)] = write_plate_store(
                masks_root / model,
                model,
                experiment,
                plate,
                fields,
                in_progress=in_progress,
                finalised=in_progress is None,
            )
    return stores


def write_reader_decoys(root: Path) -> None:
    """Files beside the stores that a mask reader must never open."""
    exclusions = (
        root
        / "profiles"
        / DEFAULT_MODEL
        / "per_experiment"
        / "idr0080-A549_plate14610_mad_exclusions.json"
    )
    exclusions.parent.mkdir(parents=True)
    exclusions.write_text(
        json.dumps({"excluded_fields": [["A01", 1]], "decoy": True}) + "\n",
        encoding="utf-8",
    )
    (root / "run_ledger.jsonl").write_text(
        json.dumps({"run": "fixture", "decoy": True}) + "\n",
        encoding="utf-8",
    )
