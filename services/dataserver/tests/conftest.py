"""Shared data-server test fixtures."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest

type TreeEntry = tuple[str, int, str]
type TreeManifest = dict[str, TreeEntry]


def tree_manifest(root: Path, *, exclude: Iterable[Path] = ()) -> TreeManifest:
    """Describe every entry below ``root`` by POSIX path, kind, size and SHA-256.

    Symlinks are recorded by target and never followed. Excluded subtrees are
    skipped entirely.
    """
    excluded = tuple(path.resolve() for path in exclude)
    manifest: TreeManifest = {}
    for directory, subdirectories, files in os.walk(root):
        current = Path(directory)
        kept: list[str] = []
        for name in sorted(subdirectories):
            child = current / name
            if any(child.resolve() == path for path in excluded):
                continue
            relative = child.relative_to(root).as_posix()
            if child.is_symlink():
                manifest[relative] = ("link", 0, os.readlink(child))
                continue
            manifest[relative] = ("dir", 0, "")
            kept.append(name)
        subdirectories[:] = kept
        for name in sorted(files):
            child = current / name
            if any(child.resolve() == path for path in excluded):
                continue
            relative = child.relative_to(root).as_posix()
            if child.is_symlink():
                manifest[relative] = ("link", 0, os.readlink(child))
                continue
            digest = hashlib.sha256(child.read_bytes()).hexdigest()
            manifest[relative] = ("file", child.stat().st_size, digest)
    return manifest


class WriteAudit:
    """Prove that served data roots are unchanged after a suite.

    Only the locations passed as ``allow`` may change: the state directory, the
    connection-file directory, the annotations folder, or a store that a test
    itself adds to a shared root.
    """

    def __init__(self) -> None:
        self._watched: list[tuple[Path, tuple[Path, ...], TreeManifest]] = []

    def watch(self, root: Path, *, allow: Iterable[Path] = ()) -> None:
        resolved = root.resolve()
        allowed = tuple(path.resolve() for path in allow)
        self._watched.append((resolved, allowed, tree_manifest(resolved, exclude=allowed)))

    def verify(self) -> None:
        for root, allowed, before in self._watched:
            after = tree_manifest(root, exclude=allowed)
            changed = sorted(
                path
                for path in before.keys() | after.keys()
                if before.get(path) != after.get(path)
            )
            assert not changed, f"Data root {root} changed outside allowed paths: {changed}"


@pytest.fixture(scope="module")
def write_audit() -> Iterator[WriteAudit]:
    """Audit the data roots a module's route suite serves."""
    audit = WriteAudit()
    yield audit
    audit.verify()


@pytest.fixture(scope="session")
def session_write_audit() -> Iterator[WriteAudit]:
    """Audit data roots shared by several modules for the whole run."""
    audit = WriteAudit()
    yield audit
    audit.verify()
