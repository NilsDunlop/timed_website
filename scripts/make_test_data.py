#!/usr/bin/env python3
"""Generate deterministic, small microscopy data matching the M1 inventory."""

import argparse
import csv
import hashlib
import io
import json
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import tifffile
import zarr
from ome_zarr.format import FormatV04, FormatV05
from ome_zarr.writer import write_image
from zarr.codecs import BloscCodec

TIMEPOINTS = 6
HEIGHT = 512
WIDTH = 512
SEED = 20261005
PIXEL_SIZE_MICROMETERS = 0.3
FIELD_STORES = (
    ("A01", 1),
    ("A01", 2),
    ("A02", 1),
    ("A02", 2),
    ("B01", 1),
    ("B01", 2),
)
COMPARISON_FIELD_STORES = (("B01", 1), ("B01", 2))
COMPARISON_PLATES = (
    ("test_plate_2", "S1.2", 2, "SYNTH002", "2026-10-06"),
    ("test_plate_3", "S1.3", 3, "SYNTH003", "2026-10-07"),
    ("test_plate_4", "S1.4", 4, "SYNTH004", "2026-10-08"),
)
GENERATED_STORES = (
    "test_plate.zarr",
    "test_plate_2.zarr",
    "test_plate_3.zarr",
    "test_plate_4.zarr",
    "test_image_v04.zarr",
    "test_image_v05.zarr",
)
GENERATED_METADATA_DIRECTORY = "metadata"
GENERATED_PACKED_DIRECTORY = "packed"
GENERATED_QC_EXPORT_DIRECTORY = "qc-export"
GENERATED_NEXT_QC_EXPORT_DIRECTORY = "qc-export-next"
DEFAULT_INVENTORY = Path(__file__).parents[1] / "docs" / "data" / "arrhenius-inventory.json"

PACKED_FIXTURE_DIRECTORY = "idr0080-like"
PACKED_INDEX_NAME = "idr0080-raw.index.parquet"
PACKED_HEIGHT = 64
PACKED_WIDTH = 64
PACKED_CHANNELS = tuple(range(1, 6))
PACKED_SITES = (1, 2)
PACKED_CHANNEL_LABELS = ("DNA", "ER", "AGP", "RNA", "Mito")
PACKED_PLATES = (
    ("idr0080-A549", 14610, "A549"),
    ("idr0080-A549", 14611, "A549"),
    ("idr0080-ES2", 14613, "ES2"),
)
IDR0080_METADATA_COLUMNS = (
    "well_id",
    "experiment_name",
    "plate",
    "address",
    "gene",
    "treatment",
    "SMILES",
    "concentration",
    "perturbation_type",
    "cell_type",
    "well_type_label",
    "idr_plate_barcode",
    "idr_cell_line",
    "idr_guide",
    "idr_gene_original",
    "idr_gene_identifier",
    "idr_profile_id",
    "idr_control_category",
    "idr_reagent_id",
)


@dataclass(frozen=True)
class DataProfile:
    """Relevant, uniform properties extracted from the Arrhenius inventory."""

    zarr_format: int
    axes: tuple[str, ...]
    dtype: str
    channel_wavelengths: tuple[int, ...]
    source_shape: tuple[int, ...]
    source_chunks: tuple[int, ...]
    compressor_name: str
    compression_level: int
    shuffle: str

    @property
    def synthetic_shape(self) -> tuple[int, ...]:
        return (TIMEPOINTS, len(self.channel_wavelengths), HEIGHT, WIDTH)

    @property
    def synthetic_chunks(self) -> tuple[int, ...]:
        y_ratio = self.source_shape[-2] // self.source_chunks[-2]
        x_ratio = self.source_shape[-1] // self.source_chunks[-1]
        if y_ratio < 1 or x_ratio < 1 or HEIGHT % y_ratio or WIDTH % x_ratio:
            raise ValueError("Inventory chunk proportions do not divide synthetic dimensions")
        return (1, 1, HEIGHT // y_ratio, WIDTH // x_ratio)


@dataclass(frozen=True)
class PackedWell:
    """One representative well from the idr0080 plate map."""

    address: str
    gene: str
    treatment: str
    well_type_label: str
    guide: str
    gene_identifier: str
    control_category: str


PACKED_WELLS = (
    PackedWell("A01", "EMPTY_control", "EMPTY_control", "", "EMPTY", "", "EMPTY"),
    PackedWell("A02", "Chr2", "Chr2_guide_1", "Cutting controls", "Chr2-1", "", "Chr2"),
    PackedWell(
        "B01",
        "LacZ",
        "LacZ_guide_1",
        "Non-cutting controls",
        "LacZ-1",
        "",
        "LacZ",
    ),
    PackedWell(
        "C01",
        "KRAS",
        "KRAS_guide_1",
        "Query guides",
        "KRAS-1",
        "ENSG00000133703",
        "targeting",
    ),
    PackedWell(
        "P24",
        "KRAS",
        "KRAS_guide_2",
        "Query guides",
        "KRAS-2",
        "ENSG00000133703",
        "targeting",
    ),
)


def _default_profile() -> DataProfile:
    return DataProfile(
        zarr_format=3,
        axes=("t", "c", "y", "x"),
        dtype="uint16",
        channel_wavelengths=(405, 505, 625),
        source_shape=(48, 3, 2500, 2500),
        source_chunks=(1, 1, 625, 625),
        compressor_name="zstd",
        compression_level=5,
        shuffle="shuffle",
    )


def _codec_settings(signature: dict[str, Any]) -> tuple[str, int, str]:
    codecs = signature.get("codecs") or []
    for codec in codecs:
        if not isinstance(codec, dict) or codec.get("name") != "sharding_indexed":
            continue
        configuration = codec.get("configuration") or {}
        for inner in configuration.get("codecs") or []:
            if isinstance(inner, dict) and inner.get("name") == "blosc":
                settings = inner.get("configuration") or {}
                return (
                    str(settings.get("cname", "zstd")),
                    int(settings.get("clevel", 5)),
                    str(settings.get("shuffle", "shuffle")),
                )
    raise ValueError("Inventory signature has no Blosc codec inside sharding_indexed")


def load_profile(inventory_path: Path) -> DataProfile:
    """Load the uniform field signature, falling back to the recorded M1 values."""
    if not inventory_path.is_file():
        return _default_profile()
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    items = inventory.get("items") or []
    if len(items) != 8 or inventory.get("errors") or inventory.get("skipped"):
        raise ValueError("Arrhenius inventory must contain eight error-free plate collections")
    signatures = []
    for item in items:
        if item.get("kind") != "collection" or item.get("errors") or item.get("field_error_count"):
            raise ValueError("Every Arrhenius item must be an error-free collection")
        item_signatures = item.get("signatures") or []
        if len(item_signatures) != 1:
            raise ValueError("Every plate collection must have one uniform field signature")
        signatures.append(item_signatures[0]["signature"])
    canonical = json.dumps(signatures[0], sort_keys=True)
    if any(json.dumps(signature, sort_keys=True) != canonical for signature in signatures[1:]):
        raise ValueError("Arrhenius plate field signatures are not uniform")

    first_field = items[0]["first_field"]
    timed = first_field["array"]["attributes"]["timed"]
    compressor_name, compression_level, shuffle = _codec_settings(signatures[0])
    return DataProfile(
        zarr_format=int(signatures[0]["zarr_format"]),
        axes=tuple(str(axis) for axis in timed["axes"]),
        dtype=str(signatures[0]["dtype"]),
        channel_wavelengths=tuple(int(channel) for channel in timed["channels_nm"]),
        source_shape=tuple(int(size) for size in signatures[0]["shape"]),
        source_chunks=tuple(int(size) for size in signatures[0]["chunks"]),
        compressor_name=compressor_name,
        compression_level=compression_level,
        shuffle=shuffle,
    )


def make_field_data(seed: int, height: int = HEIGHT, width: int = WIDTH) -> np.ndarray:
    """Make drifting, growing Gaussian cells with channel-specific signal and noise."""
    rng = np.random.default_rng(seed)
    channel_count = 3
    cell_count = 11
    margin = min(height, width) * 0.12
    centers = np.column_stack(
        (
            rng.uniform(margin, width - margin, cell_count),
            rng.uniform(margin, height - margin, cell_count),
        )
    )
    velocities = rng.uniform(-2.8, 2.8, size=(cell_count, 2))
    starting_sigma = rng.uniform(5.0, 11.0, size=cell_count)
    growth = rng.uniform(0.35, 1.0, size=cell_count)
    amplitudes = rng.uniform(1800, 9000, size=(cell_count, channel_count))
    amplitudes *= rng.uniform(0.15, 1.0, size=(cell_count, channel_count))
    y_coordinates, x_coordinates = np.ogrid[:height, :width]
    result = np.empty((TIMEPOINTS, channel_count, height, width), dtype=np.uint16)

    for time_index in range(TIMEPOINTS):
        frame = rng.normal(180.0, 35.0, size=(channel_count, height, width)).astype(np.float32)
        for cell_index in range(cell_count):
            center_x = centers[cell_index, 0] + velocities[cell_index, 0] * time_index
            center_y = centers[cell_index, 1] + velocities[cell_index, 1] * time_index
            sigma = starting_sigma[cell_index] + growth[cell_index] * time_index
            distance = (x_coordinates - center_x) ** 2 + (y_coordinates - center_y) ** 2
            gaussian = np.exp(-distance / (2.0 * sigma**2), dtype=np.float32)
            frame += amplitudes[cell_index, :, None, None] * gaussian
        result[time_index] = np.clip(frame, 0, np.iinfo(np.uint16).max).astype(np.uint16)
    return result


def _remove_generated_store(output: Path, name: str) -> Path:
    target = output / name
    resolved_output = output.resolve()
    resolved_target = target.resolve()
    if resolved_target.parent != resolved_output or name not in GENERATED_STORES:
        raise ValueError(f"Refusing to replace unexpected path: {target}")
    if target.exists():
        shutil.rmtree(target)
    return target


def _replace_metadata_directory(output: Path) -> Path:
    target = output / GENERATED_METADATA_DIRECTORY
    resolved_output = output.resolve()
    resolved_target = target.resolve()
    if resolved_target.parent != resolved_output or target.name != GENERATED_METADATA_DIRECTORY:
        raise ValueError(f"Refusing to replace unexpected path: {target}")
    if target.exists():
        shutil.rmtree(target)
    (target / "acquisition").mkdir(parents=True)
    (target / "csv").mkdir()
    return target


def _replace_packed_fixture_directory(output: Path) -> Path:
    packed = output / GENERATED_PACKED_DIRECTORY
    target = packed / PACKED_FIXTURE_DIRECTORY
    resolved_output = output.resolve()
    resolved_packed = packed.resolve()
    resolved_target = target.resolve()
    if (
        resolved_packed.parent != resolved_output
        or resolved_target.parent != resolved_packed
        or target.name != PACKED_FIXTURE_DIRECTORY
    ):
        raise ValueError(f"Refusing to replace unexpected path: {target}")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def make_packed_plane(
    plate: int,
    well_index: int,
    site: int,
    channel: int,
) -> np.ndarray:
    """Make one deterministic, nonuniform idr0080-like image plane."""
    y_coordinates, x_coordinates = np.indices((PACKED_HEIGHT, PACKED_WIDTH), dtype=np.uint32)
    center_x = 12 + (well_index * 9 + site * 5 + channel * 3) % 40
    center_y = 12 + (well_index * 7 + site * 3 + channel * 5) % 40
    radius = 6 + channel
    x_distance = x_coordinates.astype(np.int32) - center_x
    y_distance = y_coordinates.astype(np.int32) - center_y
    signal = x_distance**2 + y_distance**2 <= radius**2
    values = (
        150
        + (plate - 14600) * 31
        + well_index * 313
        + site * 79
        + channel * 701
        + x_coordinates * (channel + 3)
        + y_coordinates * (well_index + 5)
        + signal * (1_500 + channel * 250)
    )
    return np.asarray(np.clip(values, 0, np.iinfo(np.uint16).max), dtype=np.uint16)


def _packed_relpath(plate: int, well: str, site: int, channel: int) -> str:
    row = ord(well[0]) - ord("A") + 1
    column = int(well[1:])
    filename = f"r{row:02d}c{column:02d}f{site:02d}_ch{channel}.tiff"
    return f"source/SQ000{plate}/Images/{filename}"


def _tiff_bytes(plane: np.ndarray) -> bytes:
    output = io.BytesIO()
    tifffile.imwrite(
        output,
        plane,
        photometric="minisblack",
        compression=None,
        metadata=None,
    )
    return output.getvalue()


def _write_packed_shard(
    root: Path,
    *,
    experiment: str,
    plate: int,
) -> list[dict[str, object]]:
    shard = f"idr0080-raw-SQ000{plate}.tar"
    shard_path = root / shard
    pending: list[dict[str, object]] = []
    with tarfile.open(shard_path, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for well_index, well in enumerate(PACKED_WELLS):
            for site in PACKED_SITES:
                for channel in PACKED_CHANNELS:
                    relpath = _packed_relpath(plate, well.address, site, channel)
                    member_name = f"idr0080/raw/{relpath}"
                    payload = _tiff_bytes(make_packed_plane(plate, well_index, site, channel))
                    member = tarfile.TarInfo(member_name)
                    member.size = len(payload)
                    member.mode = 0o444
                    member.mtime = 0
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    archive.addfile(member, io.BytesIO(payload))
                    pending.append(
                        {
                            "relpath": relpath,
                            "experiment": experiment,
                            "plate": plate,
                            "well": well.address,
                            "site": site,
                            "channel": channel,
                            "shard": shard,
                            "member_name": member_name,
                        }
                    )

    # TarInfo.offset_data is populated only while reading an existing archive.
    with tarfile.open(shard_path, mode="r:") as archive:
        members = {member.name: member for member in archive.getmembers()}

    rows: list[dict[str, object]] = []
    for pending_row in pending:
        member_name = str(pending_row.pop("member_name"))
        member = members[member_name]
        rows.append(
            {
                **pending_row,
                "offset": member.offset_data,
                "size": member.size,
            }
        )
    return rows


def _packed_index_schema() -> pa.Schema:
    return pa.schema(
        [
            ("relpath", pa.string()),
            ("experiment", pa.string()),
            ("plate", pa.int64()),
            ("well", pa.string()),
            ("site", pa.int64()),
            ("channel", pa.int64()),
            ("offset", pa.int64()),
            ("size", pa.int64()),
            ("shard", pa.string()),
        ]
    )


def _write_parquet(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=[
            name for name in ("experiment", "well", "shard", "kind") if name in table.column_names
        ],
    )


def _write_packed_metadata(root: Path) -> None:
    metadata = root / "metadata"
    metadata.mkdir()
    path = metadata / "metadata_idr0080.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IDR0080_METADATA_COLUMNS)
        writer.writeheader()
        for experiment, plate, cell_line in PACKED_PLATES:
            barcode = f"SQ000{plate}"
            for well_index, well in enumerate(PACKED_WELLS, start=1):
                writer.writerow(
                    {
                        "well_id": f"{experiment}_{plate}_{well.address}",
                        "experiment_name": experiment,
                        "plate": str(plate),
                        "address": well.address,
                        "gene": well.gene,
                        "treatment": well.treatment,
                        "SMILES": "",
                        "concentration": "",
                        "perturbation_type": "CRISPR",
                        "cell_type": cell_line,
                        "well_type_label": well.well_type_label,
                        "idr_plate_barcode": barcode,
                        "idr_cell_line": cell_line,
                        "idr_guide": well.guide,
                        "idr_gene_original": well.gene.replace("_control", ""),
                        "idr_gene_identifier": well.gene_identifier,
                        "idr_profile_id": f"fixture_profile_{well_index:03d}",
                        "idr_control_category": well.control_category,
                        "idr_reagent_id": well.guide,
                    }
                )


def _write_packed_decoys(root: Path, index: pa.Table) -> None:
    fragment_row_count = len(PACKED_WELLS) * len(PACKED_SITES) * len(PACKED_CHANNELS)
    fragment = index.slice(0, fragment_row_count)
    _write_parquet(fragment, root / "index_fragments" / "SQ00014610.index.parquet")

    mask_schema = pa.schema(
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
    mask_index = pa.Table.from_pylist(
        [
            {
                "experiment": "idr0080-A549",
                "plate": 14610,
                "well": "A01",
                "site": 1,
                "kind": "nuclei",
                "offset": 512,
                "size": 128,
            }
        ],
        schema=mask_schema,
    )
    _write_parquet(mask_index, root / "masks" / "fixture" / "Plate14610.index.parquet")


def _write_packed_fixture(output: Path) -> Path:
    root = _replace_packed_fixture_directory(output)
    rows: list[dict[str, object]] = []
    for experiment, plate, _cell_line in PACKED_PLATES:
        rows.extend(_write_packed_shard(root, experiment=experiment, plate=plate))
    index = pa.Table.from_pylist(rows, schema=_packed_index_schema())
    _write_parquet(index, root / PACKED_INDEX_NAME)
    _write_packed_metadata(root)
    _write_packed_decoys(root, index)
    return root


QC_EXPORT_FILES = (
    "site_metrics.csv",
    "columns.csv",
    "channels.csv",
    "labels.csv",
    "review_list.csv",
    "triage_plate_channel.csv",
    "display_ranges.csv",
)
QC_DICTIONARY_FIELDS = (
    "file",
    "column",
    "label",
    "metric",
    "version",
    "channel",
    "scale",
    "worse",
    "layer",
    "gate",
    "status",
    "question",
    "blind",
    "primary",
    "default_flag",
    "markers",
)
QC_REVIEW_HIDDEN_COLUMNS = tuple(f"fixture_hidden_review_{index:02d}" for index in range(1, 11))
QC_ANNOTATION_LABELS = (
    ("ok", "No image-quality problem is visible."),
    ("blurred", "The whole field is out of focus."),
    ("partial_blur", "Part of the field is out of focus."),
    ("saturated", "Bright regions are clipped."),
    ("debris", "Non-cellular objects are visible."),
    ("bright_biological", "Bright cells or structures are biological."),
    ("dark_or_bubble", "A dark region, bubble, or meniscus is visible."),
    ("uneven_illumination", "Illumination varies across the field."),
    ("dim_or_staining", "One or more channels are dim or unevenly stained."),
    ("noisy", "The image has low signal-to-noise."),
    ("sparse_biological", "The field contains few cells for biological reasons."),
    ("known_fixed_shadow", "The known fixed instrument shadow is visible."),
    ("other", "Another problem is visible and should be described."),
    ("unsure", "The reviewer cannot decide."),
)


def _replace_qc_export_directory(output: Path, directory: str) -> Path:
    target = output / directory
    resolved_output = output.resolve()
    resolved_target = target.resolve()
    allowed = {GENERATED_QC_EXPORT_DIRECTORY, GENERATED_NEXT_QC_EXPORT_DIRECTORY}
    if resolved_target.parent != resolved_output or target.name not in allowed:
        raise ValueError(f"Refusing to replace unexpected path: {target}")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def _write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _dictionary_row(
    file: str,
    column: str,
    *,
    label: str = "",
    metric: str = "",
    version: int | str = "",
    channel: str = "",
    scale: str = "",
    worse: str = "",
    layer: str,
    gate: str = "",
    status: str = "",
    question: str,
    blind: str = "show",
    primary: bool = False,
    default_flag: str = "",
    markers: str = "",
) -> dict[str, object]:
    return {
        "file": file,
        "column": column,
        "label": label,
        "metric": metric,
        "version": version,
        "channel": channel,
        "scale": scale,
        "worse": worse,
        "layer": layer,
        "gate": gate,
        "status": status,
        "question": question,
        "blind": blind,
        "primary": primary,
        "default_flag": default_flag,
        "markers": markers,
    }


def _qc_site_dictionary(variant: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    keys = {
        "experiment": "Packed experiment identifier.",
        "cell_line": "Cell line for the packed experiment.",
        "plate": "Numeric plate identifier.",
        "plate_barcode": "Plate barcode.",
        "well": "Canonical well address.",
        "row": "Well row.",
        "column": "Well column.",
        "site": "Packed acquisition site.",
        "site_position": "Position in the fixture site mosaic.",
    }
    rows.extend(
        _dictionary_row(
            "site_metrics.csv",
            column,
            layer="key",
            question=question,
        )
        for column, question in keys.items()
    )
    rows.extend(
        _dictionary_row(
            "site_metrics.csv",
            f"member_{channel}",
            channel=channel,
            layer="key",
            question=f"Packed {channel} member name.",
        )
        for channel in PACKED_CHANNEL_LABELS
    )
    rows.extend(
        (
            _dictionary_row(
                "site_metrics.csv",
                "fixture_context_count",
                metric="fixture_context_count",
                version=1,
                worse="context",
                layer="context",
                question="Synthetic fixture count used as visible context.",
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_hidden_attribute",
                layer="context",
                status="fixture_hidden_future_status",
                question="Synthetic attribute hidden by blinded responses.",
                blind="hide",
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_protection_state",
                layer="context",
                question="Synthetic export-defined protection category.",
                blind="hide",
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_focus__DNA__raw",
                metric="fixture_focus",
                version=2,
                channel="DNA",
                scale="raw",
                worse="low",
                layer="site_review",
                gate="review",
                status="pending_review",
                question="Is the synthetic DNA field sharply focused?",
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_focus__DNA__control_z",
                metric="fixture_focus",
                version=2,
                channel="DNA",
                scale="control_z",
                worse="low",
                layer="site_review",
                gate="review",
                status="pending_review",
                question="Is the synthetic DNA focus below the review bound?",
                primary=True,
                default_flag="< -2.0",
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_intensity__AGP__raw",
                metric="fixture_intensity",
                version=1,
                channel="AGP",
                scale="raw",
                worse="high",
                layer="site_review",
                gate="none",
                status="kept",
                question="How bright is the synthetic AGP field?",
                primary=True,
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_automatic__site__raw",
                metric="fixture_automatic",
                version=1,
                channel="site",
                scale="raw",
                worse="high",
                layer="site_review",
                gate="automatic",
                status="registered",
                question="Did the registered synthetic rule flag this field?",
                primary=True,
                markers='[{"label":"registered","value":0.75}]',
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_retired__RNA__raw",
                metric="fixture_retired",
                version=1,
                channel="RNA",
                scale="raw",
                worse="high",
                layer="retired",
                gate="none",
                status="retired",
                question="Synthetic retired metric.",
                primary=True,
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_dropped__ER__raw",
                metric="fixture_dropped",
                version=1,
                channel="ER",
                scale="raw",
                worse="low",
                layer="site_review",
                gate="none",
                status="dropped",
                question="Synthetic dropped metric.",
                primary=True,
            ),
            _dictionary_row(
                "site_metrics.csv",
                "fixture_future__Mito__raw",
                metric="fixture_future",
                version=1,
                channel="Mito",
                scale="raw",
                worse="both",
                layer="site_review",
                gate="none",
                status="fixture_future_status",
                question="Synthetic metric with a deliberately unknown status.",
                primary=True,
            ),
        )
    )
    if variant == "next":
        rows.append(
            _dictionary_row(
                "site_metrics.csv",
                "fixture_texture__ER__raw",
                metric="fixture_texture",
                version=3,
                channel="ER",
                scale="raw",
                worse="both",
                layer="site_review",
                gate="review",
                status="pending_review",
                question="Is the synthetic ER texture outside the review interval?",
                primary=True,
                default_flag="< -1.5",
            )
        )
    return rows


def _qc_review_dictionary() -> list[dict[str, object]]:
    questions = {
        "scope": "Review entry scope.",
        "plate": "Review entry plate.",
        "cell_line": "Review entry cell line.",
        "well": "Review entry well.",
        "site": "Review entry site.",
        "channel": "Review entry channel.",
        "metric": "Metric behind the review entry.",
        "metric_version": "Metric definition version.",
        "value": "Metric value behind the review entry.",
        "unit": "Display unit for the metric value.",
        "entry_id": "Stable blinded queue identity.",
        "review_group": "Stable blinded queue group.",
    }
    rows = [
        _dictionary_row(
            "review_list.csv",
            column,
            layer="review",
            question=question,
        )
        for column, question in questions.items()
    ]
    rows.extend(
        _dictionary_row(
            "review_list.csv",
            column,
            layer="review",
            question="Synthetic queue attribute hidden by blinded responses.",
            blind="hide",
        )
        for column in QC_REVIEW_HIDDEN_COLUMNS
    )
    return rows


def _qc_triage_dictionary() -> list[dict[str, object]]:
    return [
        _dictionary_row(
            "triage_plate_channel.csv",
            "plate",
            layer="triage",
            question="Numeric plate identifier.",
        ),
        _dictionary_row(
            "triage_plate_channel.csv",
            "channel",
            layer="triage",
            question="Stain or field-level site summary.",
        ),
        _dictionary_row(
            "triage_plate_channel.csv",
            "fixture_triage_share",
            metric="fixture_triage_share",
            version=1,
            scale="ratio",
            worse="high",
            layer="site_review",
            gate="review",
            status="pending_review",
            question="Synthetic share of fields selected for review.",
        ),
        _dictionary_row(
            "triage_plate_channel.csv",
            "fixture_automatic_share",
            metric="fixture_automatic_share",
            version=1,
            scale="ratio",
            worse="high",
            layer="site_review",
            gate="automatic",
            status="registered",
            question="Synthetic share flagged by a registered rule.",
            markers='[{"label":"registered","value":0.2}]',
        ),
        _dictionary_row(
            "triage_plate_channel.csv",
            "fixture_panel_flag",
            metric="fixture_panel_flag",
            version=1,
            worse="high",
            layer="panel",
            gate="none",
            status="kept",
            question="Synthetic plate-panel flag.",
        ),
        _dictionary_row(
            "triage_plate_channel.csv",
            "fixture_hidden_triage_note",
            layer="panel",
            question="Synthetic triage note hidden by blinded responses.",
            blind="hide",
        ),
    ]


def _qc_site_rows(variant: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for plate_index, (experiment, plate, cell_line) in enumerate(PACKED_PLATES):
        for well_index, well in enumerate(PACKED_WELLS):
            for site in PACKED_SITES:
                row_number = ord(well.address[0]) - ord("A") + 1
                seed = plate_index * 100 + well_index * 10 + site
                row: dict[str, object] = {
                    "experiment": experiment,
                    "cell_line": cell_line,
                    "plate": plate,
                    "plate_barcode": f"SQ000{plate}",
                    "well": well.address,
                    "row": well.address[0],
                    "column": int(well.address[1:]),
                    "site": site,
                    "site_position": f"{row_number}:{site}",
                    "fixture_context_count": 20 + seed,
                    "fixture_hidden_attribute": f"fixture-category-{well_index % 3}",
                    "fixture_protection_state": (
                        "synthetic-state-a" if well_index % 2 else "synthetic-state-b"
                    ),
                    "fixture_focus__DNA__raw": round(0.8 + seed / 100.0, 3),
                    "fixture_focus__DNA__control_z": round(-2.5 + seed / 90.0, 3),
                    "fixture_intensity__AGP__raw": 500 + seed * 13,
                    "fixture_automatic__site__raw": round((seed % 9) / 8.0, 3),
                    "fixture_retired__RNA__raw": round(seed / 7.0, 3),
                    "fixture_dropped__ER__raw": round(seed / 8.0, 3),
                    "fixture_future__Mito__raw": round(seed / 9.0, 3),
                }
                for slot, channel in enumerate(PACKED_CHANNEL_LABELS, start=1):
                    relpath = _packed_relpath(plate, well.address, site, slot)
                    row[f"member_{channel}"] = f"idr0080/raw/{relpath}"
                if variant == "next":
                    row["fixture_texture__ER__raw"] = round(-2.0 + seed / 80.0, 3)
                rows.append(row)
    return rows


def _qc_review_rows(site_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in site_rows:
        plate = int(row["plate"])
        well = str(row["well"])
        site = int(row["site"])
        group = f"fixture:{plate}:{well}"
        rows.append(
            {
                "scope": "field",
                "plate": plate,
                "cell_line": row["cell_line"],
                "well": well,
                "site": site,
                "channel": "DNA",
                "metric": "fixture_focus",
                "metric_version": 2,
                "value": row["fixture_focus__DNA__control_z"],
                "unit": "z",
                "entry_id": f"fixture-field:{plate}:{well}:{site}",
                "review_group": group,
                **{
                    column: f"fixture-field-value-{index:02d}-{site}"
                    for index, column in enumerate(QC_REVIEW_HIDDEN_COLUMNS, start=1)
                },
            }
        )
        if site == PACKED_SITES[0]:
            rows.append(
                {
                    "scope": "well",
                    "plate": plate,
                    "cell_line": row["cell_line"],
                    "well": well,
                    "site": "",
                    "channel": "AGP",
                    "metric": "fixture_intensity",
                    "metric_version": 1,
                    "value": row["fixture_intensity__AGP__raw"],
                    "unit": "intensity",
                    "entry_id": f"fixture-well:{plate}:{well}",
                    "review_group": group,
                    **{
                        column: f"fixture-well-value-{index:02d}"
                        for index, column in enumerate(QC_REVIEW_HIDDEN_COLUMNS, start=1)
                    },
                }
            )
    return rows


def _qc_triage_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for plate_index, (_experiment, plate, _cell_line) in enumerate(PACKED_PLATES):
        for channel_index, channel in enumerate((*PACKED_CHANNEL_LABELS, "site")):
            score = (plate_index + channel_index + 1) / 20
            rows.append(
                {
                    "plate": plate,
                    "channel": channel,
                    "fixture_triage_share": round(score, 3),
                    "fixture_automatic_share": round(score / 2, 3),
                    "fixture_panel_flag": score > 0.25,
                    "fixture_hidden_triage_note": f"fixture-triage-{plate_index}-{channel_index}",
                }
            )
    return rows


def _qc_display_range_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for plate_index, (_experiment, plate, _cell_line) in enumerate(PACKED_PLATES):
        for slot, channel in enumerate(PACKED_CHANNEL_LABELS, start=1):
            rows.append(
                {
                    "plate": plate,
                    "plate_barcode": f"SQ000{plate}",
                    "channel": channel,
                    "display_lo": 100 + plate_index * 10 + slot,
                    "display_hi": 5_000 + plate_index * 100 + slot * 250,
                    "display_mode": "fixed per plate x channel",
                    "quantile_lo": 0.01,
                    "quantile_hi": 0.995,
                    "reference_fields": len(PACKED_WELLS) * len(PACKED_SITES),
                }
            )
    return rows


def _qc_channel_rows() -> list[dict[str, object]]:
    phenix_channels = (1, 2, 4, 3, 5)
    exposures = (0.02, 0.02, 0.02, 0.01, 0.02)
    rows: list[dict[str, object]] = []
    for slot, (channel, phenix_channel, exposure) in enumerate(
        zip(PACKED_CHANNEL_LABELS, phenix_channels, exposures, strict=True),
        start=1,
    ):
        rows.append(
            {
                "channel": channel,
                "stain": channel,
                "dye": f"fixture-dye-{slot}",
                "dye_source": "synthetic fixture",
                "target": f"fixture-target-{slot}",
                "packed_slot": slot,
                "phenix_channel_id": phenix_channel,
                "packed_member_suffix": f"_ch{slot}.tiff",
                "phenix_channel_name": f"fixture-channel-{phenix_channel}",
                "idr_file_token": f"fixture-token-{phenix_channel}",
                "excitation_nm": 350.0 + slot * 50,
                "emission_nm": 400.0 + slot * 50,
                "exposure_s": exposure,
                "identity_evidence": "Synthetic fixture identity mapping.",
            }
        )
    return rows


def _write_qc_export_fixture(
    output: Path,
    *,
    directory: str = GENERATED_QC_EXPORT_DIRECTORY,
    variant: str = "primary",
) -> Path:
    if variant not in {"primary", "next"}:
        raise ValueError(f"Unknown QC fixture variant: {variant}")
    root = _replace_qc_export_directory(output, directory)
    site_rows = _qc_site_rows(variant)
    dictionary_rows = [
        *_qc_site_dictionary(variant),
        *_qc_review_dictionary(),
        *_qc_triage_dictionary(),
    ]
    payloads = {
        "site_metrics.csv": (tuple(site_rows[0]), site_rows),
        "columns.csv": (QC_DICTIONARY_FIELDS, dictionary_rows),
        "channels.csv": (tuple(_qc_channel_rows()[0]), _qc_channel_rows()),
        "labels.csv": (
            ("label", "description"),
            [
                {"label": label, "description": description}
                for label, description in QC_ANNOTATION_LABELS
            ],
        ),
        "review_list.csv": (
            tuple(_qc_review_rows(site_rows)[0]),
            _qc_review_rows(site_rows),
        ),
        "triage_plate_channel.csv": (
            tuple(_qc_triage_rows()[0]),
            _qc_triage_rows(),
        ),
        "display_ranges.csv": (
            tuple(_qc_display_range_rows()[0]),
            _qc_display_range_rows(),
        ),
    }
    for filename, (fieldnames, rows) in payloads.items():
        _write_csv(root / filename, fieldnames, rows)

    hashes = {
        filename: hashlib.sha256((root / filename).read_bytes()).hexdigest()
        for filename in QC_EXPORT_FILES
    }
    registry_payload = "".join(f"{name}:{hashes[name]}\n" for name in QC_EXPORT_FILES)
    version = {
        "id": f"fixture-qc-{variant}-20261029",
        "label": f"Deterministic QC fixture ({variant})",
        "created_utc": "2026-10-29T12:00:00Z",
        "definitions_version": 4 if variant == "next" else 3,
        "fields": len(site_rows),
        "registry_sha256": hashlib.sha256(registry_payload.encode()).hexdigest(),
        "files": hashes,
    }
    (root / "version.json").write_text(
        json.dumps(version, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _write_bare_field(
    path: Path,
    data: np.ndarray,
    well: str,
    site: int,
    profile: DataProfile,
    *,
    site_position_fix: dict[str, Any] | None = None,
) -> None:
    compressor = BloscCodec(
        cname=profile.compressor_name,
        clevel=profile.compression_level,
        shuffle=profile.shuffle,
    )
    timed_attributes: dict[str, Any] = {
        "well": well,
        "site": site,
        "axes": list(profile.axes),
        "channels_nm": list(profile.channel_wavelengths),
        "t_indices": list(range(TIMEPOINTS)),
    }
    if site_position_fix is not None:
        timed_attributes["site_position_fix"] = site_position_fix
    array = zarr.create_array(
        path,
        shape=profile.synthetic_shape,
        dtype=profile.dtype,
        chunks=profile.synthetic_chunks,
        shards=profile.synthetic_shape,
        compressors=[compressor],
        attributes={"timed": timed_attributes},
        overwrite=True,
    )
    array[:] = data


def _omero_metadata(profile: DataProfile) -> dict[str, Any]:
    colors = ("0000FF", "00FF00", "FF0000")
    channels = []
    for index, wavelength in enumerate(profile.channel_wavelengths):
        channels.append(
            {
                "label": f"{wavelength} nm",
                "color": colors[index % len(colors)],
                "window": {"min": 0, "max": 65535, "start": 100, "end": 8000},
            }
        )
    return {"channels": channels, "rdefs": {"model": "color"}}


def _write_ome_image(path: Path, data: np.ndarray, profile: DataProfile, version: str) -> None:
    if version == "0.4":
        image_format = FormatV04()
        zarr_format = 2
    elif version == "0.5":
        image_format = FormatV05()
        zarr_format = 3
    else:
        raise ValueError(f"Unsupported OME-Zarr version: {version}")
    group = zarr.open_group(path, mode="w", zarr_format=zarr_format)
    write_image(
        data,
        group,
        scale_factors=[],
        name=f"TIMED synthetic image v{version}",
        fmt=image_format,
        axes=list(profile.axes),
        scale={"t": 1.0, "c": 1.0, "y": PIXEL_SIZE_MICROMETERS, "x": PIXEL_SIZE_MICROMETERS},
        axes_units={"t": "hour", "y": "micrometer", "x": "micrometer"},
        storage_options={"chunks": profile.synthetic_chunks},
        omero=_omero_metadata(profile),
    )


def _write_plate_metadata(
    root: Path,
    profile: DataProfile,
    *,
    plate_name: str,
    plate_id: str,
    plate_replicate: int,
    plate_barcode: str,
    experiment_date: str,
    rows: list[dict[str, str]],
) -> None:
    acquisition = {
        "project_name": "TIMED synthetic test data",
        "plate_name": plate_name,
        "cell_line": "Synthetic cells",
        "grid": {
            "num_x": 2,
            "delta_x_mm": 0.8,
            "num_y": 1,
            "delta_y_mm": 0.8,
            "num_t": TIMEPOINTS,
            "delta_t": {"h": 1, "m": 0, "s": 0},
        },
        "wellplate_type": {
            "Manufacturer": "TIMED",
            "Model_name": "Synthetic 4-well plate",
            "Offset_bottom_mm": 0.5,
        },
        "channels": [
            {
                "name": f"Fluorescence {wavelength} nm Ex",
                "enabled": True,
            }
            for wavelength in profile.channel_wavelengths
        ],
        "autofocus_enabled": True,
        "timestamp": f"{experiment_date}T12:00:00",
    }
    (root / "acquisition" / f"{plate_name}.json").write_text(
        json.dumps(acquisition, indent=2) + "\n",
        encoding="utf-8",
    )

    fieldnames = [
        "plate_id",
        "plate_series",
        "plate_replicate",
        "project",
        "plate_barcode",
        "experiment_date",
        "well",
        "well_type",
        "cell_line",
        "cell_modification",
        "passage",
        "seeding_density_cells_per_well",
        "dye_program",
        "cmpdname",
        "target_compound_concentration_uM",
        "moa",
        "target",
        "pathway",
        "mechanism_of_action",
    ]
    shared = {
        "plate_id": plate_id,
        "plate_series": "S1",
        "plate_replicate": str(plate_replicate),
        "project": "TIMED synthetic",
        "plate_barcode": plate_barcode,
        "experiment_date": experiment_date,
        "cell_line": "Synthetic cells",
        "cell_modification": "Reporter",
        "passage": "1",
        "seeding_density_cells_per_well": "1200",
        "dye_program": "Three-channel",
    }
    with (root / "csv" / f"{plate_name}.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**shared, **row})


def _write_collection_acquisition(
    plate: Path,
    profile: DataProfile,
    *,
    plate_name: str,
    wells: list[str],
) -> None:
    acquisition = {
        "project_name": "TIMED synthetic test data",
        "plate_name": plate_name,
        "grid": {"num_x": 2, "num_y": 1, "num_t": TIMEPOINTS, "delta_t_hours": 1.0},
        "wells": wells,
        "channels_nm": list(profile.channel_wavelengths),
    }
    (plate / "acquisition_config.json").write_text(
        json.dumps(acquisition, indent=2) + "\n", encoding="utf-8"
    )


def generate_test_data(
    output: Path,
    inventory_path: Path = DEFAULT_INVENTORY,
    *,
    include_packed: bool = True,
) -> None:
    """Generate the TIMED/native fixtures and, by default, the packed fixture."""
    profile = load_profile(inventory_path)
    if profile.zarr_format != 3 or profile.axes != ("t", "c", "y", "x"):
        raise ValueError("M2 currently expects the verified Zarr v3 (t,c,y,x) TIMED signature")
    output.mkdir(parents=True, exist_ok=True)
    plate = _remove_generated_store(output, "test_plate.zarr")
    comparison_plates = [
        (
            plate_spec,
            _remove_generated_store(output, plate_spec[0] + ".zarr"),
        )
        for plate_spec in COMPARISON_PLATES
    ]
    image_v04 = _remove_generated_store(output, "test_image_v04.zarr")
    image_v05 = _remove_generated_store(output, "test_image_v05.zarr")
    metadata = _replace_metadata_directory(output)
    plate.mkdir()
    for _plate_spec, comparison_plate in comparison_plates:
        comparison_plate.mkdir()

    first_field = None
    for field_index, (well, site) in enumerate(FIELD_STORES):
        data = make_field_data(SEED + field_index * 101)
        _write_bare_field(plate / f"{well}_s{site}.zarr", data, well, site, profile)
        if first_field is None:
            first_field = data

    for plate_index, (_plate_spec, comparison_plate) in enumerate(
        comparison_plates,
        start=1,
    ):
        for field_index, (well, site) in enumerate(COMPARISON_FIELD_STORES):
            data = make_field_data(SEED + plate_index * 10_000 + field_index * 101)
            _write_bare_field(
                comparison_plate / f"{well}_s{site}.zarr",
                data,
                well,
                site,
                profile,
                site_position_fix=(
                    {
                        "grid_x": 1,
                        "repaired_timepoints": [0],
                        "reason": "synthetic Berzelius site-position repair",
                        "by": "make_test_data.py",
                    }
                    if plate_index == len(comparison_plates) and field_index == 0
                    else None
                ),
            )

    _write_collection_acquisition(
        plate,
        profile,
        plate_name="test_plate",
        wells=["A01", "A02", "B01"],
    )
    for (plate_name, *_metadata), comparison_plate in comparison_plates:
        _write_collection_acquisition(
            comparison_plate,
            profile,
            plate_name=plate_name,
            wells=["B01"],
        )

    compound_rows = [
        {
            "well": "A01",
            "well_type": "compound",
            "cmpdname": "Compound Alpha",
            "target_compound_concentration_uM": "1.25",
            "moa": "AKT",
            "target": "AKT1",
            "pathway": "PI3K/Akt/mTOR",
            "mechanism_of_action": "AKT inhibitor",
        },
        {
            "well": "A02",
            "well_type": "compound",
            "cmpdname": "Compound Beta",
            "target_compound_concentration_uM": "5.0",
            "moa": "MAPK",
            "target": "MAPK1",
            "pathway": "MAPK/ERK Pathway",
            "mechanism_of_action": "MAPK inhibitor",
        },
    ]
    dmso_row = {
        "well": "B01",
        "well_type": "solvent_control",
        "cmpdname": "DMSO",
        "target_compound_concentration_uM": "0",
        "moa": "",
        "target": "",
        "pathway": "",
        "mechanism_of_action": "",
    }
    _write_plate_metadata(
        metadata,
        profile,
        plate_name="test_plate",
        plate_id="S1.1",
        plate_replicate=1,
        plate_barcode="SYNTH001",
        experiment_date="2026-10-05",
        rows=[*compound_rows, dmso_row],
    )
    for (
        plate_name,
        plate_id,
        plate_replicate,
        plate_barcode,
        experiment_date,
    ) in COMPARISON_PLATES:
        _write_plate_metadata(
            metadata,
            profile,
            plate_name=plate_name,
            plate_id=plate_id,
            plate_replicate=plate_replicate,
            plate_barcode=plate_barcode,
            experiment_date=experiment_date,
            rows=[dmso_row],
        )
    if first_field is None:
        raise RuntimeError("No synthetic fields were generated")
    _write_ome_image(image_v04, first_field, profile, "0.4")
    _write_ome_image(image_v05, first_field, profile, "0.5")
    if include_packed:
        _write_packed_fixture(output)
        _write_qc_export_fixture(output)
        _write_qc_export_fixture(
            output,
            directory=GENERATED_NEXT_QC_EXPORT_DIRECTORY,
            variant="next",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "test-data",
        help="Destination directory (default: repository test-data)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="Arrhenius inventory used to derive the data profile",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    started = time.monotonic()
    generate_test_data(args.output, args.inventory)
    elapsed = time.monotonic() - started
    print(f"Generated TIMED fixtures in {args.output.resolve()} ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
