"""Deterministic TIMED segmentation masks for the synthetic TIMED plates.

``timed-masks/`` mirrors the 2026-11 presentation export (docs/data/CONVERSION.md):
``<plate>.zarr/<well>_s<site>.zarr`` is a Zarr v3 group holding the uint16
(t, y, x) arrays ``masks_raw`` (cells) and ``nuclei`` with the field's
``t_indices``, each one whole-array shard of single-plane inner chunks, ``v2``
chunk keys and a CRC32C index. ``timed-seg/`` follows the production
segmentation's layout instead,
``<plate>/sites/<well>_s<site>/stage1/<run>/labels/<plate>/<well>_s<site>.zarr``,
one array per run (cells, and the linked classical-nuclei run), stored the
same way but with the run's plate, well, site and run id and no axes.

Each frame's cells are discs numbered 1..N without gaps, and every cell but the
last holds one smaller nucleus. They move from frame to frame and N changes,
so counts and outlines both follow the timepoint; the production discs sit
lower and their count runs the other way, so the two models never agree on a
frame. As zarr writes them, chunks holding only background are left out of the
shard.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import zarr
from zarr.codecs import BloscCodec

TIMED_MASKS_DIRECTORY = "timed-masks"
MASK_ARRAY = "masks_raw"
NUCLEUS_ARRAY = "nuclei"
MASKED_FIELDS = (
    ("test_plate", "A01", 1),
    ("test_plate", "A02", 1),
    ("test_plate", "B01", 1),
    ("test_plate_2", "B01", 1),
)
INNER_CHUNK = (128, 128)
RADIUS = 32
NUCLEUS_RADIUS = 12
_ROW_SPACING = 96

TIMED_SEGMENTATION_DIRECTORY = "timed-seg"
CELL_RUN = "R4_cpsam_dS_corrected"
NUCLEUS_RUN = "R4_cpsam_dS_corrected_nuclei"
# The four exported fields, then one that only the production run covers.
SEGMENTED_FIELDS = (*MASKED_FIELDS, ("test_plate", "A02", 2))
_PRODUCTION_SHIFT = 8


def cell_count(field_index: int, t: int) -> int:
    """Cells in one frame: two to five, changing every frame."""
    return 2 + (field_index + t) % 4


def nucleus_count(field_index: int, t: int) -> int:
    """Nuclei in one frame: every cell but the last has one."""
    return cell_count(field_index, t) - 1


def production_cell_count(field_index: int, t: int) -> int:
    """Production cells in one frame: five down to two, never the export's count."""
    return 5 - (field_index + t) % 4


def production_nucleus_count(field_index: int, t: int) -> int:
    """Production nuclei in one frame: every cell but the last has one."""
    return production_cell_count(field_index, t) - 1


def _discs(
    field_index: int,
    t: int,
    count: int,
    radius: int,
    shape: tuple[int, int],
    shift: int = 0,
) -> np.ndarray:
    labels = np.zeros(shape, dtype=np.uint16)
    rows, columns = np.ogrid[: shape[0], : shape[1]]
    center_x = RADIUS + 16 + 40 * t + 8 * field_index
    for index in range(count):
        center_y = RADIUS + 16 + shift + index * _ROW_SPACING
        disc = (rows - center_y) ** 2 + (columns - center_x) ** 2 <= radius**2
        labels[disc] = index + 1
    return labels


def frame_labels(field_index: int, t: int, height: int, width: int) -> np.ndarray:
    """One frame's cell discs, numbered from 1, shifted right by the timepoint."""
    return _discs(field_index, t, cell_count(field_index, t), RADIUS, (height, width))


def frame_nuclei(field_index: int, t: int, height: int, width: int) -> np.ndarray:
    """One frame's nuclei, centred in every cell but the last."""
    return _discs(
        field_index, t, nucleus_count(field_index, t), NUCLEUS_RADIUS, (height, width)
    )


def field_labels(field_index: int, timepoints: int, height: int, width: int) -> np.ndarray:
    """Every frame of one field's cell masks, as (t, y, x)."""
    return np.stack(
        [frame_labels(field_index, t, height, width) for t in range(timepoints)]
    )


def field_nuclei(field_index: int, timepoints: int, height: int, width: int) -> np.ndarray:
    """Every frame of one field's nucleus masks, as (t, y, x)."""
    return np.stack(
        [frame_nuclei(field_index, t, height, width) for t in range(timepoints)]
    )


def production_frame_labels(field_index: int, t: int, height: int, width: int) -> np.ndarray:
    """One frame of the production run's cell discs, a little lower than the export's."""
    return _discs(
        field_index,
        t,
        production_cell_count(field_index, t),
        RADIUS,
        (height, width),
        _PRODUCTION_SHIFT,
    )


def production_frame_nuclei(field_index: int, t: int, height: int, width: int) -> np.ndarray:
    """One frame of the production nuclei, centred in every cell but the last."""
    return _discs(
        field_index,
        t,
        production_nucleus_count(field_index, t),
        NUCLEUS_RADIUS,
        (height, width),
        _PRODUCTION_SHIFT,
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
        group = root / f"{plate}.zarr" / f"{well}_s{site}.zarr"
        write_mask_array(group, field_labels(index, timepoints, height, width), range(timepoints))
        write_mask_array(
            group,
            field_nuclei(index, timepoints, height, width),
            range(timepoints),
            array_name=NUCLEUS_ARRAY,
        )
    return root


def run_array_dir(root: Path, plate: str, well: str, site: int, run: str) -> Path:
    """One run's array for one field, in the production segmentation's layout."""
    field = f"{well}_s{site}"
    return root / plate / "sites" / field / "stage1" / run / "labels" / plate / f"{field}.zarr"


def write_run_array(
    path: Path,
    labels: np.ndarray,
    t_indices: Sequence[int],
    *,
    plate: str,
    well: str,
    site: int,
    run_id: str,
    inner_chunk: tuple[int, int] = INNER_CHUNK,
) -> Path:
    """Write one run's array for one field: the export's storage, the run's attributes."""
    array = zarr.create_array(
        path,
        shape=labels.shape,
        dtype="uint16",
        chunks=(1, *inner_chunk),
        shards=labels.shape,
        compressors=BloscCodec(cname="zstd", clevel=5, shuffle="shuffle"),
        fill_value=0,
        chunk_key_encoding={"name": "v2", "separator": "."},
        attributes={
            "run_id": run_id,
            "plate": plate,
            "well": well,
            "site": site,
            "illumination": "synthetic",
            "shape": list(labels.shape),
            "t_indices": list(t_indices),
        },
        overwrite=True,
    )
    array[:] = labels
    return path


def write_fixture_timed_segmentation(
    output: Path,
    *,
    timepoints: int,
    height: int,
    width: int,
) -> Path:
    """Write ``timed-seg/`` beside the TIMED plates; the folder must be new."""
    root = output / TIMED_SEGMENTATION_DIRECTORY
    root.mkdir()
    for index, (plate, well, site) in enumerate(SEGMENTED_FIELDS):
        for run, frame in (
            (CELL_RUN, production_frame_labels),
            (NUCLEUS_RUN, production_frame_nuclei),
        ):
            write_run_array(
                run_array_dir(root, plate, well, site, run),
                np.stack([frame(index, t, height, width) for t in range(timepoints)]),
                range(timepoints),
                plate=plate,
                well=well,
                site=site,
                run_id=run,
            )
    return root
