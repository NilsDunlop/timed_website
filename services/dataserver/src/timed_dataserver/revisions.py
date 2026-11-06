"""File identities used as cache keys and validators, and stable loading.

A loader that reads a file while it may be replaced compares the file's
identity before and after the read, and retries when it changed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StatSignature:
    """A file's modification time and size."""

    mtime_ns: int
    size: int

    @classmethod
    def of(cls, path: Path) -> StatSignature:
        metadata = path.stat()
        return cls(mtime_ns=metadata.st_mtime_ns, size=metadata.st_size)


@dataclass(frozen=True, slots=True)
class FileRevision:
    """A file's identity on its device, plus its modification time and size."""

    path: Path
    device: int
    inode: int
    mtime_ns: int
    size: int

    @classmethod
    def from_stat(cls, path: Path, metadata: os.stat_result) -> FileRevision:
        return cls(
            path=path,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            mtime_ns=metadata.st_mtime_ns,
            size=metadata.st_size,
        )


class UnstableRevisionError(RuntimeError):
    """Raised when every load attempt saw its file change."""


def load_stable[R, T](
    read: Callable[[], R],
    load: Callable[[R], T],
    *,
    first: R | None = None,
    reuse: Callable[[R], T | None] | None = None,
    attempts: int = 3,
) -> tuple[T, R]:
    """Return a value loaded against a revision that still holds afterwards.

    Each attempt loads against ``before`` and accepts the value when ``read()``
    after loading returns the same revision. Without ``first``, every attempt
    reads ``before`` itself and may return ``reuse(before)`` instead of
    loading. With ``first``, the first attempt loads against it and each retry
    loads against the revision read after the previous attempt.

    Callers keep their own exceptions: errors from ``read`` and ``load``
    propagate unchanged, and UnstableRevisionError reports that every attempt
    saw a change.
    """
    known = first
    for _attempt in range(attempts):
        before = read() if known is None else known
        if reuse is not None:
            reused = reuse(before)
            if reused is not None:
                return reused, before
        value = load(before)
        after = read()
        if after == before:
            return value, after
        known = None if first is None else after
    raise UnstableRevisionError(f"Revision changed during each of {attempts} attempts")
