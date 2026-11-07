"""Deterministic TIMED segmentation masks for the synthetic TIMED plates.

The layout mirrors the 2026-11 presentation export (docs/data/CONVERSION.md):
``timed-masks/<plate>.zarr/<well>_s<site>.zarr`` is a Zarr v3 group holding a
``masks_raw`` uint16 (t, y, x) array with the field's ``t_indices``, one
whole-array shard of single-plane inner chunks, ``v2`` chunk keys and a CRC32C
index. Each frame's cells are discs numbered 1..N without gaps. They move from
frame to frame and N changes, so counts and outlines both follow the
timepoint. As zarr writes them, chunks holding only background are left out
of the shard.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import zarr
from zarr.codecs import BloscCodec

TIMED_MASKS_DIRECTORY = "timed-masks"
MASK_ARRAY = "masks_raw"
MASKED_FIELDS = (
    ("test_plate", "A01", 1),
    ("test_plate", "A02", 1),
    ("test_plate", "B01", 1),
    ("test_plate_2", "B01", 1),
)
INNER_CHUNK = (128, 128)
RADIUS = 24
_ROW_SPACING = 96


def cell_count(field_index: int, t: int) -> int:
    """Cells in one frame: two to five, changing every frame."""
    return 2 + (field_index + t) % 4


def frame_labels(field_index: int, t: int, height: int, width: int) -> np.ndarray:
    """One frame's discs, numbered from 1, shifted right by the timepoint."""
    labels = np.zeros((height, width), dtype=np.uint16)
    rows, columns = np.ogrid[:height, :width]
    center_x = RADIUS + 16 + 40 * t + 8 * field_index
    for cell in range(cell_count(field_index, t)):
        center_y = RADIUS + 16 + cell * _ROW_SPACING
        disc = (rows - center_y) ** 2 + (columns - center_x) ** 2 <= RADIUS**2
        labels[disc] = cell + 1
    return labels


def field_labels(field_index: int, timepoints: int, height: int, width: int) -> np.ndarray:
    """Every frame of one field's masks, as (t, y, x)."""
    return np.stack(
        [frame_labels(field_index, t, height, width) for t in range(timepoints)]
    )


def write_mask_array(
    field_group: Path,
    labels: np.ndarray,
    t_indices: Sequence[int],
    *,
    array_name: str = MASK_ARRAY,
    inner_chunk: tuple[int, int] = INNER_CHUNK,
) -> Path:
    """Write one field group with its mask array in the export's layout."""
    group = zarr.open_group(
        field_group,
        mode="a",
        zarr_format=3,
        attributes={"note": "synthetic TIMED masks", "t_indices": list(t_indices)},
    )
    array = group.create_array(
        array_name,
        shape=labels.shape,
        dtype="uint16",
        chunks=(1, *inner_chunk),
        shards=labels.shape,
        compressors=BloscCodec(cname="zstd", clevel=5, shuffle="shuffle"),
        fill_value=0,
        chunk_key_encoding={"name": "v2", "separator": "."},
        attributes={"axes": ["t", "y", "x"], "t_indices": list(t_indices)},
        overwrite=True,
    )
    array[:] = labels
    return field_group / array_name


def write_fixture_timed_masks(
    output: Path,
    *,
    timepoints: int,
    height: int,
    width: int,
) -> Path:
    """Write ``timed-masks/`` beside the TIMED plates; the folder must be new."""
    root = output / TIMED_MASKS_DIRECTORY
    root.mkdir()
    for index, (plate, well, site) in enumerate(MASKED_FIELDS):
        write_mask_array(
            root / f"{plate}.zarr" / f"{well}_s{site}.zarr",
            field_labels(index, timepoints, height, width),
            range(timepoints),
        )
    return root
