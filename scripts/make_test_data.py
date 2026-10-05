#!/usr/bin/env python3
"""Generate deterministic, small microscopy data matching the M1 inventory."""

import argparse
import csv
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
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
GENERATED_STORES = ("test_plate.zarr", "test_image_v04.zarr", "test_image_v05.zarr")
GENERATED_METADATA_DIRECTORY = "metadata"
DEFAULT_INVENTORY = Path(__file__).parents[1] / "docs" / "data" / "arrhenius-inventory.json"


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


def _write_bare_field(
    path: Path, data: np.ndarray, well: str, site: int, profile: DataProfile
) -> None:
    compressor = BloscCodec(
        cname=profile.compressor_name,
        clevel=profile.compression_level,
        shuffle=profile.shuffle,
    )
    array = zarr.create_array(
        path,
        shape=profile.synthetic_shape,
        dtype=profile.dtype,
        chunks=profile.synthetic_chunks,
        shards=profile.synthetic_shape,
        compressors=[compressor],
        attributes={
            "timed": {
                "well": well,
                "site": site,
                "axes": list(profile.axes),
                "channels_nm": list(profile.channel_wavelengths),
                "t_indices": list(range(TIMEPOINTS)),
            }
        },
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


def _write_plate_metadata(root: Path, profile: DataProfile) -> None:
    acquisition = {
        "project_name": "TIMED synthetic test data",
        "plate_name": "test_plate",
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
        "timestamp": "2026-10-05T12:00:00",
    }
    (root / "acquisition" / "test_plate.json").write_text(
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
    rows = [
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
        {
            "well": "B01",
            "well_type": "solvent_control",
            "cmpdname": "DMSO",
            "target_compound_concentration_uM": "0",
            "moa": "",
            "target": "",
            "pathway": "",
            "mechanism_of_action": "",
        },
    ]
    shared = {
        "plate_id": "S1.1",
        "plate_series": "S1",
        "plate_replicate": "1",
        "project": "TIMED synthetic",
        "plate_barcode": "SYNTH001",
        "experiment_date": "2026-10-05",
        "cell_line": "Synthetic cells",
        "cell_modification": "Reporter",
        "passage": "1",
        "seeding_density_cells_per_well": "1200",
        "dye_program": "Three-channel",
    }
    with (root / "csv" / "test_plate.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**shared, **row})


def generate_test_data(output: Path, inventory_path: Path = DEFAULT_INVENTORY) -> None:
    """Generate the collection and two native OME-Zarr compatibility fixtures."""
    profile = load_profile(inventory_path)
    if profile.zarr_format != 3 or profile.axes != ("t", "c", "y", "x"):
        raise ValueError("M2 currently expects the verified Zarr v3 (t,c,y,x) TIMED signature")
    output.mkdir(parents=True, exist_ok=True)
    plate = _remove_generated_store(output, "test_plate.zarr")
    image_v04 = _remove_generated_store(output, "test_image_v04.zarr")
    image_v05 = _remove_generated_store(output, "test_image_v05.zarr")
    metadata = _replace_metadata_directory(output)
    plate.mkdir()

    first_field = None
    for field_index, (well, site) in enumerate(FIELD_STORES):
        data = make_field_data(SEED + field_index * 101)
        _write_bare_field(plate / f"{well}_s{site}.zarr", data, well, site, profile)
        if first_field is None:
            first_field = data

    acquisition = {
        "project_name": "TIMED synthetic test data",
        "plate_name": "test_plate",
        "grid": {"num_x": 2, "num_y": 1, "num_t": TIMEPOINTS, "delta_t_hours": 1.0},
        "wells": ["A01", "A02", "B01"],
        "channels_nm": list(profile.channel_wavelengths),
    }
    (plate / "acquisition_config.json").write_text(
        json.dumps(acquisition, indent=2) + "\n", encoding="utf-8"
    )
    _write_plate_metadata(metadata, profile)
    if first_field is None:
        raise RuntimeError("No synthetic fields were generated")
    _write_ome_image(image_v04, first_field, profile, "0.4")
    _write_ome_image(image_v05, first_field, profile, "0.5")


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
