"""The synthetic TIMED masks: both layouts, known labels, and time-varying counts."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import zarr

from scripts import make_test_data, timed_mask_stores

SHAPE = (make_test_data.TIMEPOINTS, make_test_data.HEIGHT, make_test_data.WIDTH)


def _write(tmp_path: Path) -> Path:
    return timed_mask_stores.write_fixture_timed_masks(
        tmp_path,
        timepoints=SHAPE[0],
        height=SHAPE[1],
        width=SHAPE[2],
    )


def test_fixture_writes_cells_and_nuclei_for_each_masked_field(tmp_path: Path) -> None:
    root = _write(tmp_path)
    assert root == tmp_path / timed_mask_stores.TIMED_MASKS_DIRECTORY
    written = sorted(path.relative_to(root).as_posix() for path in root.glob("*.zarr/*.zarr/*"))
    assert written == sorted(
        f"{plate}.zarr/{well}_s{site}.zarr/{name}"
        for plate, well, site in timed_mask_stores.MASKED_FIELDS
        for name in ("masks_raw", "nuclei", "zarr.json")
    )
    group = json.loads((root / "test_plate.zarr" / "A01_s1.zarr" / "zarr.json").read_text("utf-8"))
    assert group["node_type"] == "group"


def test_arrays_use_the_presentation_exports_layout(tmp_path: Path) -> None:
    array_dir = _write(tmp_path) / "test_plate.zarr" / "A01_s1.zarr" / "masks_raw"
    assert sorted(path.name for path in array_dir.iterdir()) == ["0.0.0", "zarr.json"]
    metadata = json.loads((array_dir / "zarr.json").read_text("utf-8"))
    assert metadata["shape"] == list(SHAPE)
    assert metadata["data_type"] == "uint16"
    assert metadata["fill_value"] == 0
    assert metadata["chunk_grid"]["configuration"]["chunk_shape"] == list(SHAPE)
    assert metadata["chunk_key_encoding"] == {"name": "v2", "configuration": {"separator": "."}}
    sharding = metadata["codecs"][0]
    assert sharding["name"] == "sharding_indexed"
    assert sharding["configuration"]["chunk_shape"] == [1, *timed_mask_stores.INNER_CHUNK]
    assert [codec["name"] for codec in sharding["configuration"]["codecs"]] == ["bytes", "blosc"]
    assert [codec["name"] for codec in sharding["configuration"]["index_codecs"]] == [
        "bytes",
        "crc32c",
    ]
    assert metadata["attributes"] == {"axes": ["t", "y", "x"], "t_indices": [0, 1, 2, 3, 4, 5]}


def test_labels_run_from_one_without_gaps_and_change_with_time(tmp_path: Path) -> None:
    root = _write(tmp_path)
    for index, (plate, well, site) in enumerate(timed_mask_stores.MASKED_FIELDS):
        stored = zarr.open_array(root / f"{plate}.zarr" / f"{well}_s{site}.zarr" / "masks_raw")
        labels = np.asarray(stored[:])
        assert np.array_equal(labels, timed_mask_stores.field_labels(index, *SHAPE))
        counts = [timed_mask_stores.cell_count(index, t) for t in range(SHAPE[0])]
        for t, count in enumerate(counts):
            assert np.unique(labels[t]).tolist() == list(range(count + 1))
        assert len(set(counts)) > 1
        assert not np.array_equal(labels[0] > 0, labels[1] > 0)

        nuclei_store = zarr.open_array(root / f"{plate}.zarr" / f"{well}_s{site}.zarr" / "nuclei")
        nuclei = np.asarray(nuclei_store[:])
        assert np.array_equal(nuclei, timed_mask_stores.field_nuclei(index, *SHAPE))
        for t in range(SHAPE[0]):
            count = timed_mask_stores.nucleus_count(index, t)
            assert np.unique(nuclei[t]).tolist() == list(range(count + 1))
            # Each nucleus lies inside the cell with the same number.
            assert np.array_equal(labels[t][nuclei[t] > 0], nuclei[t][nuclei[t] > 0])


def test_regenerating_replaces_only_the_mask_folder(tmp_path: Path) -> None:
    sibling = tmp_path / "test_plate.zarr"
    sibling.mkdir()
    for _run in range(2):
        make_test_data._replace_timed_masks_directory(tmp_path)
        root = _write(tmp_path)
    assert sorted(path.name for path in root.iterdir()) == ["test_plate.zarr", "test_plate_2.zarr"]
    assert sibling.is_dir()


def _write_segmentation(tmp_path: Path) -> Path:
    return timed_mask_stores.write_fixture_timed_segmentation(
        tmp_path,
        timepoints=SHAPE[0],
        height=SHAPE[1],
        width=SHAPE[2],
    )


def test_production_runs_follow_the_pipelines_layout(tmp_path: Path) -> None:
    root = _write_segmentation(tmp_path)
    assert root == tmp_path / timed_mask_stores.TIMED_SEGMENTATION_DIRECTORY
    written = sorted(path.relative_to(root).as_posix() for path in root.rglob("zarr.json"))
    assert written == sorted(
        f"{plate}/sites/{well}_s{site}/stage1/{run}/labels/{plate}/{well}_s{site}.zarr/zarr.json"
        for plate, well, site in timed_mask_stores.SEGMENTED_FIELDS
        for run in (timed_mask_stores.CELL_RUN, timed_mask_stores.NUCLEUS_RUN)
    )
    array_dir = timed_mask_stores.run_array_dir(
        root, "test_plate", "A02", 2, timed_mask_stores.CELL_RUN
    )
    assert sorted(path.name for path in array_dir.iterdir()) == ["0.0.0", "zarr.json"]
    metadata = json.loads((array_dir / "zarr.json").read_text("utf-8"))
    assert metadata["node_type"] == "array"
    assert metadata["shape"] == list(SHAPE)
    assert metadata["chunk_key_encoding"] == {"name": "v2", "configuration": {"separator": "."}}
    assert metadata["codecs"][0]["configuration"]["chunk_shape"] == [
        1,
        *timed_mask_stores.INNER_CHUNK,
    ]
    # Like the real runs: the run's identity, no axes and no dimension names.
    assert metadata["attributes"] == {
        "run_id": timed_mask_stores.CELL_RUN,
        "plate": "test_plate",
        "well": "A02",
        "site": 2,
        "illumination": "synthetic",
        "shape": list(SHAPE),
        "t_indices": [0, 1, 2, 3, 4, 5],
    }
    assert "dimension_names" not in metadata


def test_production_labels_differ_from_the_exports_on_every_frame(tmp_path: Path) -> None:
    root = _write_segmentation(tmp_path)
    for index, (plate, well, site) in enumerate(timed_mask_stores.SEGMENTED_FIELDS):
        cells = np.asarray(
            zarr.open_array(
                timed_mask_stores.run_array_dir(
                    root, plate, well, site, timed_mask_stores.CELL_RUN
                ),
                mode="r",
            )[:]
        )
        nuclei = np.asarray(
            zarr.open_array(
                timed_mask_stores.run_array_dir(
                    root, plate, well, site, timed_mask_stores.NUCLEUS_RUN
                ),
                mode="r",
            )[:]
        )
        for t in range(SHAPE[0]):
            count = timed_mask_stores.production_cell_count(index, t)
            assert count != timed_mask_stores.cell_count(index, t)
            assert np.unique(cells[t]).tolist() == list(range(count + 1))
            assert np.array_equal(
                cells[t], timed_mask_stores.production_frame_labels(index, t, *SHAPE[1:])
            )
            nucleus_total = timed_mask_stores.production_nucleus_count(index, t)
            assert np.unique(nuclei[t]).tolist() == list(range(nucleus_total + 1))
            assert np.array_equal(cells[t][nuclei[t] > 0], nuclei[t][nuclei[t] > 0])
            assert not np.array_equal(
                cells[t] > 0, timed_mask_stores.frame_labels(index, t, *SHAPE[1:]) > 0
            )


def test_regenerating_replaces_only_the_segmentation_folder(tmp_path: Path) -> None:
    masks = _write(tmp_path)
    for _run in range(2):
        make_test_data._replace_timed_masks_directory(
            tmp_path, timed_mask_stores.TIMED_SEGMENTATION_DIRECTORY
        )
        root = _write_segmentation(tmp_path)
    assert sorted(path.name for path in root.iterdir()) == ["test_plate", "test_plate_2"]
    assert masks.is_dir()
    with pytest.raises(ValueError, match="unexpected mask folder"):
        make_test_data._replace_timed_masks_directory(tmp_path, "test_plate.zarr")
