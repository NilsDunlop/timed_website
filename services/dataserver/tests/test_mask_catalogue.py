"""Mask models come from the code profile's masks folder beside the packed index."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from timed_dataserver.mask_catalogue import MaskCatalogue, masks_root, plate_files
from timed_dataserver.mask_reader import MaskReader
from timed_dataserver.packed_profiles import (
    IDR0080_FIXTURE_PROFILE,
    IDR0080_PROFILE,
    PackedMasks,
)

from scripts import make_test_data, mask_stores

MASKS = PackedMasks(path="masks", default_model=mask_stores.DEFAULT_MODEL)


@pytest.fixture(scope="module")
def packed_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_test_data._write_packed_fixture(tmp_path_factory.mktemp("mask-catalogue"))


def _index(root: Path) -> Path:
    return root / make_test_data.PACKED_INDEX_NAME


def _listing(reader: MaskReader, root: Path, experiment: str, plate: int) -> list[tuple]:
    return [
        (entry.model.tag, entry.available, entry.default)
        for entry in reader.models(_index(root), MASKS, experiment, plate)
    ]


def test_the_idr0080_profiles_declare_their_masks_beside_the_index() -> None:
    assert IDR0080_PROFILE.masks == MASKS
    assert IDR0080_FIXTURE_PROFILE.masks == MASKS


def test_listing_puts_the_default_first_and_offers_the_stock_model_only_on_14610(
    packed_root: Path,
) -> None:
    reader = MaskReader()
    assert _listing(reader, packed_root, "idr0080-A549", 14610) == [
        (mask_stores.DEFAULT_MODEL, True, True),
        (mask_stores.STOCK_MODEL, True, False),
    ]
    for experiment, plate in (("idr0080-A549", 14611), ("idr0080-ES2", 14613)):
        assert _listing(reader, packed_root, experiment, plate) == [
            (mask_stores.DEFAULT_MODEL, True, True),
            (mask_stores.STOCK_MODEL, False, False),
        ]
    default, stock = (entry.model for entry in reader.models(_index(packed_root), MASKS, "x", 1))
    assert (default.recipe, default.kinds, default.frozen, default.created_at) == (
        "IDR0080_B90",
        ("nuclei", "cells", "cytoplasm"),
        False,
        "2026-10-15T15:54:43+0200",
    )
    assert (stock.recipe, stock.created_at) == ("R2", "2026-10-14T03:08:39+0200")


def test_a_missing_default_model_leaves_no_default(packed_root: Path) -> None:
    other = PackedMasks(path="masks", default_model="not_a_model")
    entries = MaskReader().models(_index(packed_root), other, "idr0080-A549", 14610)
    assert [(entry.model.tag, entry.default) for entry in entries] == [
        (mask_stores.STOCK_MODEL, False),
        (mask_stores.DEFAULT_MODEL, False),
    ]


def _manifest(directory: Path, value: object, *, raw: bytes | None = None) -> None:
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(
        raw if raw is not None else json.dumps(value).encode("utf-8")
    )


def _schema(**changes: object) -> dict[str, object]:
    schema: dict[str, object] = {
        "store_format": "tar+wal/v1",
        "mask_dtype": "uint16",
        "kinds": ["nuclei"],
    }
    schema.update(changes)
    return schema


def _valid(tag: str, **changes: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "model_tag": tag,
        "frozen": True,
        "recipe": {"name": "R9"},
        "schema": _schema(),
    }
    manifest.update(changes)
    return manifest


def test_unsupported_or_unreadable_manifests_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "masks"
    untagged = _valid("x")
    del untagged["model_tag"]
    _manifest(root / "good", _valid("good"))
    _manifest(root / "untagged", untagged)
    _manifest(root / "not_json", None, raw=b"{")
    _manifest(root / "not_object", [1])
    _manifest(root / "mismatched", _valid("someone_else"))
    _manifest(root / "zarr", _valid("zarr", schema=_schema(store_format="zarr")))
    _manifest(root / "uint32", _valid("uint32", schema=_schema(mask_dtype="uint32")))
    _manifest(root / "no_kinds", _valid("no_kinds", schema=_schema(kinds=[])))
    _manifest(root / "bad_kind", _valid("bad_kind", schema=_schema(kinds=["Nuclei!"])))
    _manifest(root / "twice", _valid("twice", schema=_schema(kinds=["nuclei", "nuclei"])))
    _manifest(root / "frozen_text", _valid("frozen_text", frozen="yes"))
    _manifest(root / "huge", None, raw=b" " * (1024 * 1024 + 1))
    _manifest(root / ".hidden", _valid(".hidden"))
    (root / "no_manifest").mkdir()
    (root / "a_file").write_text("{}", encoding="utf-8")

    models = MaskCatalogue().models(root)
    assert [(model.tag, model.recipe, model.frozen) for model in models] == [
        ("good", "R9", True),
        ("untagged", "R9", True),
    ]


def test_unknown_or_unsafe_model_tags_resolve_to_nothing(packed_root: Path) -> None:
    catalogue = MaskCatalogue()
    root = packed_root / "masks"
    assert catalogue.model(root, mask_stores.DEFAULT_MODEL) is not None
    for tag in ("missing", "fixture", mask_stores.OTHER_FORMAT_MODEL, "..", "a/b", "", ".x"):
        assert catalogue.model(root, tag) is None


def test_the_masks_folder_must_stay_inside_the_index_directory(packed_root: Path) -> None:
    index = _index(packed_root)
    assert masks_root(index, MASKS) == (packed_root / "masks").resolve()
    for path in ("../masks", "/masks", "C:/masks", "masks\\x", "", "missing"):
        assert masks_root(index, PackedMasks(path=path, default_model="x")) is None
        assert MaskReader().models(index, PackedMasks(path=path, default_model="x"), "e", 1) == []


def test_a_plate_is_covered_when_its_tar_and_log_exist(tmp_path: Path, packed_root: Path) -> None:
    root = tmp_path / "copy"
    shutil.copytree(packed_root / "masks" / mask_stores.STOCK_MODEL, root / "masks" / "stock")
    manifest = root / "masks" / "stock" / "manifest.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(mask_stores.STOCK_MODEL, "stock"),
        encoding="utf-8",
    )
    model = MaskCatalogue().model(root / "masks", "stock")
    assert model is not None
    files = plate_files(model, "idr0080-A549", 14610)
    assert files is not None
    reader = MaskReader()
    index = root / "idr0080-raw.index.parquet"
    stock = PackedMasks(path="masks", default_model="stock")
    assert _availability(reader, index, stock) is True
    files.log.unlink()
    assert _availability(reader, index, stock) is False
    files.log.mkdir()
    assert _availability(reader, index, stock) is False


def _availability(reader: MaskReader, index: Path, masks: PackedMasks) -> bool:
    (entry,) = reader.models(index, masks, "idr0080-A549", 14610)
    return entry.available


@pytest.mark.needs_symlinks
def test_symlinks_never_lead_out_of_the_masks_folder(tmp_path: Path, packed_root: Path) -> None:
    outside = tmp_path / "outside"
    shutil.copytree(packed_root / "masks", outside)
    index_directory = tmp_path / "index"
    index_directory.mkdir()
    index = index_directory / make_test_data.PACKED_INDEX_NAME
    try:
        (index_directory / "masks").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Creating a directory symlink is unavailable: {exc}")
    assert masks_root(index, MASKS) is None

    inside = tmp_path / "inside"
    root = inside / "masks"
    root.mkdir(parents=True)
    (root / mask_stores.DEFAULT_MODEL).symlink_to(
        outside / mask_stores.DEFAULT_MODEL, target_is_directory=True
    )
    shutil.copytree(outside / mask_stores.STOCK_MODEL, root / mask_stores.STOCK_MODEL)
    catalogue = MaskCatalogue()
    assert [model.tag for model in catalogue.models(root)] == [mask_stores.STOCK_MODEL]

    stock = root / mask_stores.STOCK_MODEL
    os.replace(stock / "manifest.json", tmp_path / "manifest.json")
    (stock / "manifest.json").symlink_to(tmp_path / "manifest.json")
    assert catalogue.models(root) == ()
    os.replace(tmp_path / "manifest.json", tmp_path / "kept.json")
    (stock / "manifest.json").unlink()
    shutil.copy(tmp_path / "kept.json", stock / "manifest.json")
    model = catalogue.model(root, mask_stores.STOCK_MODEL)
    assert model is not None

    experiment = stock / "idr0080-A549"
    shutil.move(experiment, tmp_path / "experiment")
    experiment.symlink_to(tmp_path / "experiment", target_is_directory=True)
    assert plate_files(model, "idr0080-A549", 14610) is None
    experiment.unlink()
    shutil.move(tmp_path / "experiment", experiment)
    files = plate_files(model, "idr0080-A549", 14610)
    assert files is not None
    os.replace(files.tar, tmp_path / "plate.tar")
    files.tar.symlink_to(tmp_path / "plate.tar")
    reader = MaskReader()
    entries = reader.models(inside / make_test_data.PACKED_INDEX_NAME, MASKS, "idr0080-A549", 14610)
    assert [(entry.model.tag, entry.available) for entry in entries] == [
        (mask_stores.STOCK_MODEL, False)
    ]
