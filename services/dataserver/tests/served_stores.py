"""Small inline stores that the generated test data does not contain."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import zarr
from zarr.codecs import BloscCodec


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_berzelius_plate(root: Path) -> tuple[Path, np.ndarray]:
    """Write a Berzelius-layout TIMED plate and its metadata below ``root``."""
    plate = root / "PB100140_P2.1.zarr"
    plate.mkdir()
    write_json(
        plate / "zarr.json",
        {
            "attributes": {
                "timed_acquisition": {
                    "plate_name": "PB100140_P2.1",
                    "project": "TIMED_MOAC_LGCP_EXP01",
                }
            },
            "zarr_format": 3,
            "node_type": "group",
        },
    )
    data = np.arange(2 * 3 * 16 * 16, dtype=np.uint16).reshape(2, 3, 16, 16)
    field = zarr.create_array(
        plate / "C03_s1.zarr",
        shape=data.shape,
        dtype="uint16",
        chunks=(1, 1, 16, 16),
        shards=data.shape,
        compressors=[BloscCodec(cname="zstd", clevel=5, shuffle="shuffle")],
        attributes={
            "timed": {
                "well": "C03",
                "site": 1,
                "axes": ["t", "c", "y", "x"],
                "channels_nm": [405, 505, 625],
                "t_indices": [0, 1],
                "site_position_fix": {
                    "grid_x": 1,
                    "repaired_timepoints": [0],
                    "reason": "synthetic site swap",
                    "by": "test fixture",
                },
            }
        },
        overwrite=True,
    )
    field[:] = data

    metadata = root / "metadata"
    (metadata / "acquisition").mkdir(parents=True)
    (metadata / "csv").mkdir()
    write_json(
        metadata / "acquisition" / "PB100140_P2.1.json",
        {
            "project_name": "TIMED_MOAC_LGCP_EXP01",
            "plate_name": "PB100140_P2.1",
            "cell_line": "U2OS_CLEO3",
            "grid": {
                "num_x": 2,
                "delta_x_mm": 0.9,
                "num_y": 1,
                "delta_y_mm": 0.9,
                "num_t": 2,
                "delta_t_hours": 1,
            },
            "channels": [
                {"name": "405 nm", "enabled": True},
                {"name": "505 nm", "enabled": True},
                {"name": "625 nm", "enabled": True},
            ],
        },
    )
    (metadata / "csv" / "PB100140_P2.1.csv").write_text(
        "plate_id,plate_series,plate_replicate,project,plate_barcode,experiment_date,"
        "well,well_type,cell_line,cell_modification,passage,"
        "seeding_density_cells_per_well,dye_program,cmpdname,"
        "target_compound_concentration_uM,moa,target,pathway,mechanism_of_action\n"
        "P2.1,P2,1,TIMED_MOAC_LGCP_EXP01,PB100140,2026-07-10,C03,compound,"
        "U2OS_Cleo3,plasmid4_clone1,14+9,1200,NONE,Compound Test,1.0,Test,"
        "Target,Pathway,Mechanism\n",
        encoding="utf-8",
    )
    return plate, data


def write_native_plate(plate: Path) -> Path:
    """Write a metadata-only OME-Zarr 0.4 (Zarr v2) plate with one well and field."""
    write_json(plate / ".zgroup", {"zarr_format": 2})
    write_json(
        plate / ".zattrs",
        {
            "plate": {
                "version": "0.4",
                "name": "Native test plate",
                "rows": [{"name": "A"}],
                "columns": [{"name": "1"}],
                "wells": [{"path": "A/1", "rowIndex": 0, "columnIndex": 0}],
                "acquisitions": [{"id": 7, "name": "hourly"}],
            }
        },
    )
    write_json(plate / "A" / "1" / ".zgroup", {"zarr_format": 2})
    write_json(
        plate / "A" / "1" / ".zattrs",
        {"well": {"version": "0.4", "images": [{"path": "0", "acquisition": 7}]}},
    )
    write_json(plate / "A" / "1" / "0" / ".zgroup", {"zarr_format": 2})
    write_json(
        plate / "A" / "1" / "0" / ".zattrs",
        {
            "multiscales": [
                {
                    "version": "0.4",
                    "name": "field zero",
                    "axes": [
                        {"name": "t", "type": "time"},
                        {"name": "c", "type": "channel"},
                        {"name": "y", "type": "space"},
                        {"name": "x", "type": "space"},
                    ],
                    "datasets": [{"path": "0"}],
                }
            ]
        },
    )
    write_json(
        plate / "A" / "1" / "0" / "0" / ".zarray",
        {
            "zarr_format": 2,
            "shape": [2, 1, 32, 32],
            "chunks": [1, 1, 16, 16],
            "dtype": "<u2",
        },
    )
    return plate
