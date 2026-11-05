from __future__ import annotations

import os
from pathlib import Path

import pytest
from timed_dataserver.packed import IndexSignature
from timed_dataserver.qc_export import QcExportSignature
from timed_dataserver.revisions import (
    FileRevision,
    StatSignature,
    UnstableRevisionError,
    load_stable,
)


def test_signature_types_are_frozen_comparable_values(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"12345")
    metadata = path.stat()

    signature = StatSignature.of(path)
    assert signature == StatSignature(metadata.st_mtime_ns, 5)
    assert IndexSignature is StatSignature
    assert QcExportSignature is StatSignature
    assert len({signature, StatSignature(metadata.st_mtime_ns, 5)}) == 1
    with pytest.raises(AttributeError):
        signature.size = 6  # type: ignore[misc]

    revision = FileRevision.from_stat(path, os.stat(path))
    assert (revision.path, revision.device, revision.inode, revision.size) == (
        path,
        metadata.st_dev,
        metadata.st_ino,
        5,
    )


def test_load_stable_reads_before_each_attempt_and_can_reuse() -> None:
    revisions = iter((1, 2, 3, 3))
    loads: list[int] = []

    def load(revision: int) -> str:
        loads.append(revision)
        return f"value {revision}"

    assert load_stable(lambda: next(revisions), load) == ("value 3", 3)
    assert loads == [1, 3]

    reads: list[int] = []

    def read() -> int:
        reads.append(7)
        return 7

    assert load_stable(read, load, reuse=lambda revision: f"cached {revision}") == (
        "cached 7",
        7,
    )
    assert reads == [7]
    assert loads == [1, 3]


def test_load_stable_continues_from_the_revision_read_after_loading() -> None:
    revisions = iter((5, 6, 6))
    loads: list[int] = []

    def load(revision: int) -> int:
        loads.append(revision)
        return revision * 10

    assert load_stable(lambda: next(revisions), load, first=4) == (60, 6)
    assert loads == [4, 5, 6]


@pytest.mark.parametrize("first", [None, 0])
def test_load_stable_gives_up_after_the_attempt_count(first: int | None) -> None:
    counter = iter(range(1, 100))
    loads: list[int] = []

    def load(revision: int) -> int:
        loads.append(revision)
        return revision

    with pytest.raises(UnstableRevisionError):
        load_stable(lambda: next(counter), load, first=first, attempts=3)
    assert len(loads) == 3


def test_load_stable_passes_read_and_load_errors_through() -> None:
    def unreadable() -> int:
        raise FileNotFoundError("gone")

    with pytest.raises(FileNotFoundError, match="gone"):
        load_stable(unreadable, lambda revision: revision)

    def failing_load(_revision: int) -> int:
        raise ValueError("bad content")

    with pytest.raises(ValueError, match="bad content"):
        load_stable(lambda: 1, failing_load)
