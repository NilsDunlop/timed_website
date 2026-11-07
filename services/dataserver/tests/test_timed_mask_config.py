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
    resolve_timed_mask_mappings,
)


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


def test_the_cli_reports_a_malformed_mapping(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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
                "--timed-masks",
                "relative=folder",
            ]
        )
    assert stopped.value.code == 2
    assert "--timed-masks paths must be absolute" in capsys.readouterr().err
