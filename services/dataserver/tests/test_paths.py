"""Direct table over RootGuard: the one path check behind every data-server route."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from timed_dataserver.paths import (
    PathForbiddenError,
    PathNotFoundError,
    RootGuard,
    error_reason,
    os_error_reason,
)

WINDOWS = os.name == "nt"
PARENT = "Parent path segments are not allowed"
OUTSIDE = "Path is outside the configured data roots"
POSIX_STORE_PATH = "Store paths must be relative POSIX paths"
ESCAPE = "Path escapes the linked dataset"


@dataclass(frozen=True)
class Layout:
    root: Path
    store: Path
    outside: Path
    guard: RootGuard


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    root = tmp_path / "root"
    store = root / "store"
    (store / "s0").mkdir(parents=True)
    (store / "s0" / "zarr.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    return Layout(
        root=root.resolve(),
        store=store.resolve(),
        outside=outside.resolve(),
        guard=RootGuard((root,)),
    )


def _other_drive(path: Path) -> str:
    return "Z:" if path.drive.upper() != "Z:" else "Y:"


def test_resolve_accepts_paths_inside_a_root_with_either_separator(layout: Layout) -> None:
    store = layout.store
    assert layout.guard.resolve(str(store)) == store
    assert layout.guard.resolve(store.as_posix()) == store
    assert layout.guard.resolve("store") == store
    assert layout.guard.resolve("store/s0") == store / "s0"
    if WINDOWS:
        assert layout.guard.resolve("store\\s0") == store / "s0"
    else:
        with pytest.raises(PathNotFoundError, match="Path does not exist: store"):
            layout.guard.resolve("store\\s0")


@pytest.mark.parametrize(
    "raw",
    (
        "../outside",
        "store/../store",
        "store/..",
        "..\\outside",
        "store\\..\\store",
        "store\x00",
    ),
)
def test_resolve_rejects_parent_segments_and_nul_before_any_lookup(
    layout: Layout, raw: str
) -> None:
    for must_exist in (True, False):
        with pytest.raises(PathForbiddenError, match=f"^{PARENT}$"):
            layout.guard.resolve(raw, must_exist=must_exist)
    with pytest.raises(PathForbiddenError, match=f"^{PARENT}$"):
        layout.guard.resolve(str(layout.root) + os.sep + raw)


def test_resolve_distinguishes_outside_from_missing(layout: Layout) -> None:
    for raw in (str(layout.outside), str(layout.outside / "missing")):
        for must_exist in (True, False):
            with pytest.raises(PathForbiddenError, match=f"^{OUTSIDE}$"):
                layout.guard.resolve(raw, must_exist=must_exist)
    missing = layout.root / "missing.zarr"
    with pytest.raises(PathNotFoundError, match=f"^Path does not exist: {missing.name}$"):
        layout.guard.resolve("missing.zarr")
    assert layout.guard.resolve("missing.zarr", must_exist=False) == missing
    assert layout.guard.resolve(str(missing), must_exist=False) == missing


def test_missing_absolute_paths_are_named_relative_to_their_root(layout: Layout) -> None:
    missing = layout.root / "store" / "missing.zarr"
    for raw in (str(missing), missing.as_posix()):
        with pytest.raises(
            PathNotFoundError, match="^Path does not exist: store/missing.zarr$"
        ):
            layout.guard.resolve(raw)
    with pytest.raises(PathNotFoundError, match="^Path does not exist: store/missing.zarr$"):
        layout.guard.resolve("store/missing.zarr")


def test_os_error_reasons_never_carry_the_file_name(tmp_path: Path) -> None:
    named = FileNotFoundError(2, "No such file or directory", str(tmp_path / "x.csv"))
    assert os_error_reason(named) == "No such file or directory"
    assert os_error_reason(PermissionError(13, "Permission denied", "private.csv")) == (
        "Permission denied"
    )
    assert os_error_reason(OSError("connection reset by peer")) == "connection reset by peer"
    assert os_error_reason(OSError()) == "OSError"
    try:
        (tmp_path / "missing.txt").read_text(encoding="utf-8")
    except OSError as exc:
        assert tmp_path.name in str(exc)
        assert tmp_path.name not in os_error_reason(exc)


def test_error_reasons_replace_every_spelling_of_a_named_path(tmp_path: Path) -> None:
    index = tmp_path / "idr0080-raw.index.parquet"
    arrow_style = ValueError(f"Could not open Parquet input source '{index.as_posix()}': bad")
    native_style = ValueError(f"Could not open {index}: bad")

    assert error_reason(arrow_style, index) == (
        "Could not open Parquet input source 'idr0080-raw.index.parquet': bad"
    )
    assert error_reason(native_style, index) == "Could not open idr0080-raw.index.parquet: bad"
    assert error_reason(OSError(f"Failed to open local file '{index.as_posix()}'"), index) == (
        "Failed to open local file 'idr0080-raw.index.parquet'"
    )


def test_resolve_treats_drive_letters_by_platform(layout: Layout) -> None:
    if WINDOWS:
        drive_absolute = f"{layout.root.drive}/"
        with pytest.raises(PathForbiddenError, match=f"^{OUTSIDE}$"):
            layout.guard.resolve(drive_absolute)
        # A drive-relative path on the root's own drive stays beneath the root.
        relative = f"{layout.root.drive}store"
        assert layout.guard.resolve(relative) == layout.store
    else:
        with pytest.raises(PathNotFoundError, match="^Path does not exist: C:/store$"):
            layout.guard.resolve("C:/store")
        assert layout.guard.resolve("C:store", must_exist=False) == layout.root / "C:store"


@pytest.mark.needs_symlinks
def test_resolve_rejects_symlinks_that_leave_the_roots(layout: Layout) -> None:
    link = layout.root / "escape"
    try:
        link.symlink_to(layout.outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Creating a directory symlink is unavailable: {exc}")
    for raw in (str(link), str(link / "secret.txt"), "escape/secret.txt"):
        with pytest.raises(PathForbiddenError, match=f"^{OUTSIDE}$"):
            layout.guard.resolve(raw)
    with pytest.raises(PathForbiddenError, match=f"^{OUTSIDE}$"):
        layout.guard.root_for(link)
    with pytest.raises(PathForbiddenError, match=f"^{ESCAPE}$"):
        layout.guard.child(layout.root, "escape/secret.txt")


@pytest.mark.parametrize(
    ("relative", "expected"),
    (
        ("s0/zarr.json", "s0/zarr.json"),
        ("s0//zarr.json", "s0/zarr.json"),
        ("./s0", "s0"),
        ("s0/", "s0"),
        ("", "."),
        (".", "."),
    ),
)
def test_child_resolves_url_style_paths_beneath_the_store(
    layout: Layout, relative: str, expected: str
) -> None:
    assert layout.guard.child(layout.store, relative) == layout.store / expected


@pytest.mark.parametrize(
    ("relative", "message"),
    (
        ("../outside", PARENT),
        ("s0/../s0", PARENT),
        ("..\\outside", PARENT),
        ("s0\x00", PARENT),
        ("s0\\zarr.json", POSIX_STORE_PATH),
        ("/s0/zarr.json", POSIX_STORE_PATH),
        ("C:\\s0\\zarr.json", POSIX_STORE_PATH),
    ),
)
def test_child_rejects_parent_segments_backslashes_and_absolute_paths(
    layout: Layout, relative: str, message: str
) -> None:
    for must_exist in (True, False):
        with pytest.raises(PathForbiddenError, match=f"^{message}$"):
            layout.guard.child(layout.store, relative, must_exist=must_exist)


def test_child_reports_missing_paths_unless_absence_is_allowed(layout: Layout) -> None:
    with pytest.raises(PathNotFoundError, match="^Path does not exist: s0/missing$"):
        layout.guard.child(layout.store, "s0/missing")
    assert layout.guard.child(layout.store, "s0/missing", must_exist=False) == (
        layout.store / "s0" / "missing"
    )
    # Percent escapes are literal names here; the HTTP layer decodes them first.
    assert layout.guard.child(layout.store, "%2e%2e/x", must_exist=False) == (
        layout.store / "%2e%2e" / "x"
    )


def test_child_treats_drive_letters_by_platform(layout: Layout) -> None:
    if WINDOWS:
        # The store's own drive letter is dropped, so the path stays in the store;
        # another drive escapes it.
        same = f"{layout.store.drive}/s0/zarr.json"
        for must_exist in (True, False):
            assert layout.guard.child(layout.store, same, must_exist=must_exist) == (
                layout.store / "s0" / "zarr.json"
            )
        other = f"{_other_drive(layout.store)}/s0/zarr.json"
        with pytest.raises(PathNotFoundError, match=f"^Path does not exist: {other}$"):
            layout.guard.child(layout.store, other)
        with pytest.raises(PathForbiddenError, match=f"^{ESCAPE}$"):
            layout.guard.child(layout.store, other, must_exist=False)
    else:
        with pytest.raises(PathNotFoundError, match="^Path does not exist: C:/s0/zarr.json$"):
            layout.guard.child(layout.store, "C:/s0/zarr.json")
        assert layout.guard.child(layout.store, "C:/s0/zarr.json", must_exist=False) == (
            layout.store / "C:" / "s0" / "zarr.json"
        )


def test_root_for_returns_the_containing_root(layout: Layout) -> None:
    assert layout.guard.root_for(layout.store) == layout.root
    assert layout.guard.root_for(layout.root) == layout.root
    with pytest.raises(PathForbiddenError, match=f"^{OUTSIDE}$"):
        layout.guard.root_for(layout.outside)
    # A missing path is not translated into PathNotFoundError.
    with pytest.raises(FileNotFoundError):
        layout.guard.root_for(layout.root / "missing")


def test_roots_must_exist_and_are_deduplicated(layout: Layout, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="At least one data root is required"):
        RootGuard(())
    with pytest.raises(ValueError, match="Data root does not exist"):
        RootGuard((tmp_path / "absent",))
    with pytest.raises(ValueError, match="Data root is not a directory"):
        RootGuard((layout.outside / "secret.txt",))
    assert RootGuard((layout.root, layout.root / "store" / "..")).roots == (layout.root,)
