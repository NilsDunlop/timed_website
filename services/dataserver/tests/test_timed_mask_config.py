"""TIMED mask-folder mappings: CLI parsing, startup validation and plate mirroring."""

from __future__ import annotations

from pathlib import Path

import pytest
from timed_dataserver import cli
from timed_dataserver.paths import RootGuard
from timed_dataserver.timed_mask_config import (
    MaskConfigurationError,
    TimedMaskMapping,
    mask_private_folders,
    mirrored_plate,
    parse_timed_mask_mappings,
    production_plate,
    resolve_timed_mask_mappings,
)
from timed_dataserver.timed_mask_layout import TimedMaskFolder, plate_folders
from timed_dataserver.timed_masks import RunIdentity


def test_mappings_parse_one_absolute_collection_and_folder_each(tmp_path: Path) -> None:
    collection, folder = tmp_path / "zarr", tmp_path / "corrected"
    assert parse_timed_mask_mappings([f"{collection}={folder}"]) == (
        TimedMaskMapping(collection=collection, masks_root=folder),
    )
    assert parse_timed_mask_mappings([]) == ()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (["no-separator"], "exactly one collection=folder separator"),
        (["/a=/b=/c"], "exactly one collection=folder separator"),
        (["=/folder"], "non-empty collection and folder"),
        (["/collection="], "non-empty collection and folder"),
        (["relative=/folder"], "paths must be absolute"),
        (["/collection=relative"], "paths must be absolute"),
    ],
)
def test_malformed_mappings_are_refused(values: list[str], message: str) -> None:
    with pytest.raises(MaskConfigurationError, match=message):
        anchor = str(Path("/").resolve())
        parse_timed_mask_mappings([value.replace("/", anchor) for value in values])


def test_a_collection_may_be_mapped_only_once(tmp_path: Path) -> None:
    value = f"{tmp_path / 'zarr'}={tmp_path / 'corrected'}"
    with pytest.raises(MaskConfigurationError, match="repeats a collection path"):
        parse_timed_mask_mappings([value, value])


def _root(tmp_path: Path) -> tuple[Path, RootGuard]:
    data = tmp_path / "data"
    (data / "zarr").mkdir(parents=True)
    return data, RootGuard((data,))


def test_resolution_canonicalizes_and_allows_a_private_folder_inside_a_root(
    tmp_path: Path,
) -> None:
    data, guard = _root(tmp_path)
    outside = tmp_path / "corrected"
    inside = data / "masks"
    resolved = resolve_timed_mask_mappings(
        (
            TimedMaskMapping(data / "zarr", outside),
            TimedMaskMapping(data, inside),
        ),
        guard,
    )
    assert resolved == (
        TimedMaskMapping((data / "zarr").resolve(), outside.resolve()),
        TimedMaskMapping(data.resolve(), inside.resolve()),
    )
    private = mask_private_folders(resolved, guard)
    assert private.contains(inside.resolve() / "plate.zarr")
    assert private.contains_raw("masks/plate.zarr")
    assert not private.contains((data / "zarr").resolve())


@pytest.mark.parametrize(
    ("collection", "folder", "message"),
    [
        ("outside", "corrected", "Invalid TIMED mask collection"),
        ("data/missing", "corrected", "Invalid TIMED mask collection"),
        ("data/file.txt", "corrected", "is not a directory"),
        ("data/zarr", "data", "cannot contain a data root"),
        ("data/zarr", ".", "cannot contain a data root"),
        ("data/zarr", "data/zarr", "cannot contain their collection"),
    ],
)
def test_unsafe_mappings_stop_the_server_at_startup(
    tmp_path: Path,
    collection: str,
    folder: str,
    message: str,
) -> None:
    data, guard = _root(tmp_path)
    (data / "file.txt").write_text("not a collection", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    with pytest.raises(MaskConfigurationError, match=message):
        resolve_timed_mask_mappings(
            (TimedMaskMapping(tmp_path / collection, tmp_path / folder),),
            guard,
        )


def test_mask_folders_cannot_overlap_each_other(tmp_path: Path) -> None:
    data, guard = _root(tmp_path)
    (data / "other").mkdir()
    with pytest.raises(MaskConfigurationError, match="cannot overlap each other"):
        resolve_timed_mask_mappings(
            (
                TimedMaskMapping(data / "zarr", tmp_path / "corrected"),
                TimedMaskMapping(data / "other", tmp_path / "corrected" / "nested"),
            ),
            guard,
        )


def test_plates_mirror_into_the_innermost_collections_folder(tmp_path: Path) -> None:
    outer = TimedMaskMapping(tmp_path / "data", tmp_path / "outer-masks")
    inner = TimedMaskMapping(tmp_path / "data" / "zarr", tmp_path / "inner-masks")
    plate = tmp_path / "data" / "zarr" / "P1.zarr"
    assert mirrored_plate((outer, inner), plate) == (inner, tmp_path / "inner-masks" / "P1.zarr")
    assert mirrored_plate((outer,), plate) == (outer, tmp_path / "outer-masks" / "zarr" / "P1.zarr")
    assert mirrored_plate((inner,), tmp_path / "data" / "zarr") is None
    assert mirrored_plate((inner,), tmp_path / "elsewhere" / "P1.zarr") is None


@pytest.mark.parametrize("option", ["--timed-masks", "--timed-segmentation"])
def test_the_cli_reports_a_malformed_mapping(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(
            [
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--data-root",
                str(tmp_path),
                "--connection-file",
                str(tmp_path / "connection.json"),
                "--environment",
                "local",
                option,
                "relative=folder",
            ]
        )
    assert stopped.value.code == 2
    assert f"{option} paths must be absolute" in capsys.readouterr().err


def test_production_mappings_carry_their_layout_and_option(tmp_path: Path) -> None:
    collection, folder = tmp_path / "zarr", tmp_path / "seg"
    assert parse_timed_mask_mappings([f"{collection}={folder}"], layout="production") == (
        TimedMaskMapping(collection=collection, masks_root=folder, layout="production"),
    )
    value = f"{collection}={folder}"
    with pytest.raises(MaskConfigurationError, match="--timed-segmentation repeats a collection"):
        parse_timed_mask_mappings([value, value], layout="production")


def test_a_collection_has_at_most_one_folder_per_layout(tmp_path: Path) -> None:
    data, guard = _root(tmp_path)
    export = TimedMaskMapping(data / "zarr", tmp_path / "corrected")
    production = TimedMaskMapping(data / "zarr", tmp_path / "seg", layout="production")
    resolved = resolve_timed_mask_mappings((export, production), guard)
    assert [mapping.layout for mapping in resolved] == ["export", "production"]
    private = mask_private_folders(resolved, guard)
    assert private.contains((tmp_path / "seg").resolve() / "P1" / "sites")
    second = TimedMaskMapping(data / "zarr", tmp_path / "seg-2", layout="production")
    with pytest.raises(MaskConfigurationError, match="repeats a collection"):
        resolve_timed_mask_mappings((production, second), guard)


@pytest.mark.parametrize(
    ("export", "production"),
    [
        ("masks", "masks/seg"),
        ("masks/corrected", "masks"),
        ("masks", "masks"),
    ],
)
def test_production_and_export_folders_never_overlap(
    tmp_path: Path, export: str, production: str
) -> None:
    data, guard = _root(tmp_path)
    with pytest.raises(MaskConfigurationError, match="cannot overlap each other"):
        resolve_timed_mask_mappings(
            (
                TimedMaskMapping(data / "zarr", tmp_path / export),
                TimedMaskMapping(data / "zarr", tmp_path / production, layout="production"),
            ),
            guard,
        )


def test_production_folders_follow_the_same_startup_rules(tmp_path: Path) -> None:
    data, guard = _root(tmp_path)
    for folder, message in (
        (data, "cannot contain a data root"),
        (data / "zarr", "cannot contain their collection"),
    ):
        with pytest.raises(MaskConfigurationError, match=message):
            resolve_timed_mask_mappings(
                (TimedMaskMapping(data / "zarr", folder, layout="production"),),
                guard,
            )


def test_production_plates_drop_only_the_zarr_suffix(tmp_path: Path) -> None:
    export = TimedMaskMapping(tmp_path / "data", tmp_path / "corrected")
    outer = TimedMaskMapping(tmp_path / "data", tmp_path / "outer-seg", layout="production")
    inner = TimedMaskMapping(tmp_path / "data" / "zarr", tmp_path / "seg", layout="production")
    plate = tmp_path / "data" / "zarr" / "PB100140_P2.1.zarr"
    assert production_plate((export, outer, inner), plate) == (
        inner,
        tmp_path / "seg" / "PB100140_P2.1",
        "PB100140_P2.1",
    )
    assert production_plate((outer,), plate) == (
        outer,
        tmp_path / "outer-seg" / "zarr" / "PB100140_P2.1",
        "PB100140_P2.1",
    )
    # A plate folder without the suffix keeps its whole name, dots included.
    assert production_plate((inner,), tmp_path / "data" / "zarr" / "P2.1") == (
        inner,
        tmp_path / "seg" / "P2.1",
        "P2.1",
    )
    assert production_plate((export,), plate) is None
    assert mirrored_plate((outer, inner), plate) is None
    assert production_plate((inner,), tmp_path / "data" / "zarr" / ".zarr") is None
    assert production_plate((inner,), tmp_path / "data" / "zarr") is None


def test_plate_folders_place_each_layouts_arrays(tmp_path: Path) -> None:
    collection = tmp_path / "data" / "zarr"
    plate = collection / "PB100140_P2.1.zarr"
    folders = plate_folders(
        (
            TimedMaskMapping(collection, tmp_path / "corrected"),
            TimedMaskMapping(collection, tmp_path / "seg", layout="production"),
        ),
        plate,
    )
    export, production = folders["export"], folders["production"]
    assert export == TimedMaskFolder(
        "export", tmp_path / "corrected", tmp_path / "corrected" / plate.name, plate.name
    )
    assert export.array_dir("C05", 1, "masks_raw") == (
        tmp_path / "corrected" / "PB100140_P2.1.zarr" / "C05_s1.zarr" / "masks_raw"
    )
    assert export.label("C05", 1, "nuclei") == "PB100140_P2.1.zarr/C05_s1.zarr/nuclei"
    assert export.run("C05", 1, "nuclei") is None
    run = "R4_cpsam_dS_corrected"
    assert production.label("C05", 1, run) == (
        f"PB100140_P2.1/sites/C05_s1/stage1/{run}/labels/PB100140_P2.1/C05_s1.zarr"
    )
    assert production.array_dir("C05", 1, run).is_relative_to(tmp_path / "seg")
    assert production.run("C05", 1, run) == RunIdentity("PB100140_P2.1", "C05", 1, run)
    assert plate_folders((), plate) == {}
