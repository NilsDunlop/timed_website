"""TIMED mask arrays: strict validation against their field, plane reads and revisions."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from timed_dataserver.mask_store import MaskStoreError
from timed_dataserver.timed_masks import (
    METADATA_NAME,
    RunIdentity,
    TimedMaskArrays,
    confined,
    has_array,
    load_array,
    read_plane,
)

from scripts import timed_mask_stores

TIMEPOINTS, HEIGHT, WIDTH = 4, 96, 192
CHUNK = (32, 64)
IMAGE_SHAPE = (TIMEPOINTS, 3, HEIGHT, WIDTH)
T_INDICES = tuple(range(TIMEPOINTS))
LABEL = "masks_raw/plate.zarr/A01_s1.zarr"


def _labels(offset: int = 0) -> np.ndarray:
    labels = np.zeros((TIMEPOINTS, HEIGHT, WIDTH), dtype=np.uint16)
    for t in range(TIMEPOINTS):
        for cell in range(t + 1 + offset):
            top = 4 + 22 * cell
            labels[t, top : top + 16, 70 + 10 * t : 90 + 10 * t] = cell + 1
    return labels


def _write(root: Path, labels: np.ndarray | None = None) -> Path:
    masks_root = root / "masks"
    timed_mask_stores.write_mask_array(
        masks_root / "plate.zarr" / "A01_s1.zarr",
        _labels() if labels is None else labels,
        T_INDICES,
        inner_chunk=CHUNK,
    )
    return masks_root


def _array_dir(masks_root: Path) -> Path:
    return masks_root / "plate.zarr" / "A01_s1.zarr" / "masks_raw"


def _load(masks_root: Path, image_shape: tuple[int, ...] = IMAGE_SHAPE, t_indices=T_INDICES):
    metadata = confined(_array_dir(masks_root) / METADATA_NAME, masks_root.resolve())
    assert metadata is not None
    return load_array(LABEL, metadata, masks_root.resolve(), image_shape, t_indices)


def test_planes_read_back_exactly_with_omitted_chunks(tmp_path: Path) -> None:
    masks_root = _write(tmp_path)
    array = _load(masks_root)
    assert array.shape == (TIMEPOINTS, HEIGHT, WIDTH)
    assert array.chunk == CHUNK
    assert array.index.shape == (TIMEPOINTS, 3, 3, 2)
    assert bool((array.index == (1 << 64) - 1).any()), "the fixture must omit empty chunks"
    labels = _labels()
    for t in range(TIMEPOINTS):
        plane = read_plane(array, t)
        assert plane.dtype == np.uint16
        assert not plane.flags.writeable
        assert np.array_equal(plane, labels[t])


def _edit(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    change(metadata)
    path.write_text(json.dumps(metadata), encoding="utf-8")


def _sharding(metadata: dict[str, Any]) -> dict[str, Any]:
    return metadata["codecs"][0]["configuration"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda m: m.update(zarr_format=2), "not a Zarr v3 array"),
        (lambda m: m.update(node_type="group"), "not a Zarr v3 array"),
        (lambda m: m.update(shape=[TIMEPOINTS, HEIGHT]), "not three positive integers"),
        (lambda m: m.update(data_type="uint8"), "not uint16"),
        (lambda m: m.update(fill_value=1), "fill value is not 0"),
        (lambda m: m["attributes"].update(axes=["t", "x", "y"]), "axes are not t, y, x"),
        (lambda m: m["attributes"].update(t_indices=[1, 2, 3, 4]), "time indices differ"),
        (lambda m: m.update(dimension_names=["t", "c", "y"]), "dimension names"),
        (lambda m: m.update(storage_transformers=[{"name": "x"}]), "storage transformers"),
        (
            lambda m: m["chunk_grid"]["configuration"].update(chunk_shape=[1, HEIGHT, WIDTH]),
            "not one whole-array shard",
        ),
        (lambda m: _sharding(m).update(chunk_shape=[2, *CHUNK]), "part of one plane"),
        (lambda m: _sharding(m)["codecs"][1]["configuration"].update(typesize=1), "uint16 Blosc"),
        (lambda m: _sharding(m)["codecs"][0]["configuration"].update(endian="big"), "little"),
        (lambda m: _sharding(m)["index_codecs"].pop(), "CRC32C"),
        (lambda m: m["chunk_key_encoding"].update(name="other"), "chunk-key encoding"),
    ],
)
def test_metadata_that_breaks_the_contract_is_invalid(
    tmp_path: Path,
    change: Callable[[dict[str, Any]], None],
    reason: str,
) -> None:
    masks_root = _write(tmp_path)
    _edit(_array_dir(masks_root) / METADATA_NAME, change)
    with pytest.raises(MaskStoreError) as invalid:
        _load(masks_root)
    assert str(invalid.value).startswith(f"TIMED masks {LABEL} are invalid: ")
    assert reason in str(invalid.value)
    assert str(tmp_path) not in str(invalid.value)


@pytest.mark.parametrize(
    ("image_shape", "t_indices", "reason"),
    [
        ((TIMEPOINTS + 1, 3, HEIGHT, WIDTH), T_INDICES, "the field needs (5, 96, 192)"),
        ((TIMEPOINTS, 3, HEIGHT, WIDTH + 1), T_INDICES, "the field needs (4, 96, 193)"),
        (IMAGE_SHAPE, (0, 1, 2, 4), "time indices differ"),
    ],
)
def test_the_masks_must_match_their_image_field(
    tmp_path: Path,
    image_shape: tuple[int, ...],
    t_indices: tuple[int, ...],
    reason: str,
) -> None:
    masks_root = _write(tmp_path)
    with pytest.raises(MaskStoreError, match="are invalid") as invalid:
        _load(masks_root, image_shape, t_indices)
    assert reason in str(invalid.value)


def test_a_damaged_index_shard_or_chunk_is_reported(tmp_path: Path) -> None:
    masks_root = _write(tmp_path)
    shard = _array_dir(masks_root) / "0.0.0"
    original = shard.read_bytes()

    damaged = bytearray(original)
    damaged[-10] ^= 0xFF
    shard.write_bytes(bytes(damaged))
    with pytest.raises(MaskStoreError, match="fails its CRC32C check"):
        _load(masks_root)

    shard.write_bytes(original[:100])
    with pytest.raises(MaskStoreError, match="shorter than its index"):
        _load(masks_root)

    shard.write_bytes(original)
    array = _load(masks_root)
    offset = next(int(entry[0]) for entry in array.index.reshape(-1, 2) if entry[0] < 1 << 63)
    damaged = bytearray(original)
    damaged[offset : offset + 16] = b"\0" * 16
    shard.write_bytes(bytes(damaged))
    array = _load(masks_root)
    planes_with_data = sorted({int(t) for t in np.argwhere(array.index[..., 0] == offset)[:, 0]})
    with pytest.raises(MaskStoreError, match="could not be decoded|bytes, expected"):
        read_plane(array, planes_with_data[0])

    (_array_dir(masks_root) / "0.0.0").unlink()
    with pytest.raises(MaskStoreError, match="the shard is missing"):
        _load(masks_root)


def test_the_array_cache_follows_each_files_revision(tmp_path: Path) -> None:
    masks_root = _write(tmp_path)
    arrays = TimedMaskArrays()
    array_dir = _array_dir(masks_root)
    resolved_root = masks_root.resolve()

    first = arrays.get(LABEL, array_dir, resolved_root, IMAGE_SHAPE, T_INDICES)
    assert first is not None
    assert arrays.get(LABEL, array_dir, resolved_root, IMAGE_SHAPE, T_INDICES) is first

    _write(tmp_path, _labels(offset=1))
    regenerated = arrays.get(LABEL, array_dir, resolved_root, IMAGE_SHAPE, T_INDICES)
    assert regenerated is not None and regenerated is not first
    assert regenerated.revision != first.revision
    assert np.array_equal(read_plane(regenerated, 0), _labels(offset=1)[0])

    shutil.rmtree(array_dir)
    assert arrays.get(LABEL, array_dir, resolved_root, IMAGE_SHAPE, T_INDICES) is None
    assert not has_array(array_dir, resolved_root)


@pytest.mark.needs_symlinks
def test_mask_paths_never_follow_a_symlink_out_of_the_mask_folder(tmp_path: Path) -> None:
    outside = _write(tmp_path / "outside")
    masks_root = tmp_path / "inside" / "masks"
    (masks_root / "plate.zarr").mkdir(parents=True)
    try:
        (masks_root / "plate.zarr" / "A01_s1.zarr").symlink_to(
            outside / "plate.zarr" / "A01_s1.zarr", target_is_directory=True
        )
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    resolved_root = masks_root.resolve()
    array_dir = _array_dir(masks_root)
    assert confined(array_dir / METADATA_NAME, resolved_root) is None
    assert not has_array(array_dir, resolved_root)
    assert TimedMaskArrays().get(LABEL, array_dir, resolved_root, IMAGE_SHAPE, T_INDICES) is None

    # A shard that leaves the folder is refused even when its metadata is inside.
    inside = _write(tmp_path / "second")
    shard = _array_dir(inside) / "0.0.0"
    shard.unlink()
    os.symlink(_array_dir(outside) / "0.0.0", shard)
    with pytest.raises(MaskStoreError, match="the shard is missing"):
        _load(inside)


def test_an_export_array_must_declare_its_axes(tmp_path: Path) -> None:
    masks_root = _write(tmp_path)
    _edit(_array_dir(masks_root) / METADATA_NAME, lambda m: m["attributes"].pop("axes"))
    with pytest.raises(MaskStoreError, match="the axes are not t, y, x"):
        _load(masks_root)


RUN = RunIdentity(plate="plate", well="A01", site=1, run_id="R4_cpsam_dS_corrected")
RUN_LABEL = "plate/sites/A01_s1/stage1/R4_cpsam_dS_corrected/labels/plate/A01_s1.zarr"


def _write_run(root: Path) -> tuple[Path, Path]:
    seg_root = root / "seg"
    array_dir = timed_mask_stores.write_run_array(
        timed_mask_stores.run_array_dir(seg_root, "plate", "A01", 1, RUN.run_id),
        _labels(),
        T_INDICES,
        plate=RUN.plate,
        well=RUN.well,
        site=RUN.site,
        run_id=RUN.run_id,
        inner_chunk=CHUNK,
    )
    return seg_root, array_dir


def _load_run(seg_root: Path, array_dir: Path, run: RunIdentity = RUN):
    metadata = confined(array_dir / METADATA_NAME, seg_root.resolve())
    assert metadata is not None
    return load_array(RUN_LABEL, metadata, seg_root.resolve(), IMAGE_SHAPE, T_INDICES, run)


def test_a_run_array_needs_no_axes_and_reads_back_exactly(tmp_path: Path) -> None:
    seg_root, array_dir = _write_run(tmp_path)
    metadata = json.loads((array_dir / METADATA_NAME).read_text(encoding="utf-8"))
    assert "axes" not in metadata["attributes"]
    array = _load_run(seg_root, array_dir)
    for t in range(TIMEPOINTS):
        assert np.array_equal(read_plane(array, t), _labels()[t])
    # Declared axes are accepted only when they are the pinned order.
    _edit(array_dir / METADATA_NAME, lambda m: m["attributes"].update(axes=["t", "y", "x"]))
    assert _load_run(seg_root, array_dir).shape == (TIMEPOINTS, HEIGHT, WIDTH)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda a: a.update(plate="P2"), "the stored plate 'P2' differs from its path's 'plate'"),
        (lambda a: a.update(well="A02"), "the stored well 'A02' differs from its path's 'A01'"),
        (lambda a: a.update(site=2), "the stored site 2 differs from its path's 1"),
        (lambda a: a.update(site="1"), "the stored site '1' differs from its path's 1"),
        (lambda a: a.update(site=True), "the stored site True differs from its path's 1"),
        (
            lambda a: a.update(run_id="R4_cpsam_dS_corrected_nuclei"),
            "the stored run_id 'R4_cpsam_dS_corrected_nuclei' differs from its path's",
        ),
        (lambda a: a.update(well=["A01"]), "the stored well list differs from its path's 'A01'"),
        (lambda a: a.pop("run_id"), "the run_id attribute is missing"),
        (lambda a: a.pop("plate"), "the plate attribute is missing"),
        (lambda a: a.update(axes=["t", "x", "y"]), "the axes are not t, y, x"),
        (lambda a: a.update(t_indices=[1, 2, 3, 4]), "time indices differ"),
    ],
)
def test_a_run_array_must_store_what_its_path_names(
    tmp_path: Path,
    change: Callable[[dict[str, Any]], None],
    reason: str,
) -> None:
    seg_root, array_dir = _write_run(tmp_path)
    _edit(array_dir / METADATA_NAME, lambda m: change(m["attributes"]))
    with pytest.raises(MaskStoreError) as invalid:
        _load_run(seg_root, array_dir)
    assert str(invalid.value).startswith(f"TIMED masks {RUN_LABEL} are invalid: ")
    assert reason in str(invalid.value)
    assert str(tmp_path) not in str(invalid.value)


def test_the_array_cache_keys_on_the_run_it_expects(tmp_path: Path) -> None:
    seg_root, array_dir = _write_run(tmp_path)
    arrays = TimedMaskArrays()
    resolved = seg_root.resolve()
    first = arrays.get(RUN_LABEL, array_dir, resolved, IMAGE_SHAPE, T_INDICES, RUN)
    assert first is not None
    assert arrays.get(RUN_LABEL, array_dir, resolved, IMAGE_SHAPE, T_INDICES, RUN) is first
    other = RunIdentity(plate="plate", well="A01", site=1, run_id="R5")
    with pytest.raises(MaskStoreError, match="the stored run_id"):
        arrays.get(RUN_LABEL, array_dir, resolved, IMAGE_SHAPE, T_INDICES, other)
    # The same files validated as an export array still need declared axes.
    with pytest.raises(MaskStoreError, match="the axes are not t, y, x"):
        arrays.get(RUN_LABEL, array_dir, resolved, IMAGE_SHAPE, T_INDICES)
