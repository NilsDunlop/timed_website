"""The file-identity memo: one computation per identity, never a stale value."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from timed_dataserver.file_memo import FileMemo, identity


def test_a_value_is_computed_once_per_identity(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    path.write_text("{}", encoding="utf-8")
    memo: FileMemo[int] = FileMemo(max_entries=4)
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return len(calls)

    assert memo.get("key", path, compute) == 1
    assert memo.get("key", path, compute) == 1
    path.write_text('{"changed": true}', encoding="utf-8")
    assert memo.get("key", path, compute) == 2
    assert memo.get("key", path, compute) == 2
    assert len(calls) == 2


def test_a_new_modification_time_alone_invalidates(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    path.write_text("{}", encoding="utf-8")
    memo: FileMemo[str] = FileMemo(max_entries=4)
    assert memo.get("key", path, lambda: "first") == "first"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert memo.get("key", path, lambda: "second") == "second"


def test_missing_paths_and_failures_are_never_memoised(tmp_path: Path) -> None:
    memo: FileMemo[str] = FileMemo(max_entries=4)
    missing = tmp_path / "missing.json"
    assert identity(missing) is None
    assert memo.get("missing", missing, lambda: "a") == "a"
    assert memo.get("missing", missing, lambda: "b") == "b"

    path = tmp_path / "metadata.json"
    path.write_text("{}", encoding="utf-8")

    def fail() -> str:
        raise ValueError("invalid metadata")

    with pytest.raises(ValueError, match="invalid metadata"):
        memo.get("key", path, fail)
    assert memo.get("key", path, lambda: "valid") == "valid"


def test_the_least_recently_used_entry_is_dropped_first(tmp_path: Path) -> None:
    memo: FileMemo[str] = FileMemo(max_entries=2)
    paths = []
    for name in ("a", "b", "c"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        paths.append(path)
    memo.get("a", paths[0], lambda: "a1")
    memo.get("b", paths[1], lambda: "b1")
    memo.get("a", paths[0], lambda: "a2")  # a is now the most recent
    memo.get("c", paths[2], lambda: "c1")  # evicts b
    assert memo.get("a", paths[0], lambda: "a3") == "a1"
    assert memo.get("b", paths[1], lambda: "b2") == "b2"
    memo.clear()
    assert memo.get("a", paths[0], lambda: "a4") == "a4"
