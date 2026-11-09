"""Values derived from files, reused while those files keep their identity.

A file's identity is its device, inode, size and modification time. Any change
to the file, including its replacement, changes the identity, so the next
lookup recomputes the value instead of serving a stale one. Lookups are
thread-safe; a value is computed outside the lock, so two threads may compute
the same value once each.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from pathlib import Path

type Identity = tuple[int, int, int, int]


def identity(path: Path) -> Identity | None:
    """The identity of the file or directory at *path* (following symlinks), or None."""
    try:
        result = os.stat(path)
    except (OSError, ValueError):
        return None
    return result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns


class FileMemo[V]:
    """A bounded least-recently-used memo keyed on a path and that path's identity."""

    def __init__(self, max_entries: int) -> None:
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, tuple[Identity, V]] = OrderedDict()

    def get(self, key: Hashable, path: Path, compute: Callable[[], V]) -> V:
        """The memoised value for *key*, recomputed when *path*'s identity changes.

        The identity is read before *compute* runs, so a change during the
        computation is noticed on the next lookup. Exceptions are not memoised.
        """
        current = identity(path)
        if current is None:
            return compute()
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and cached[0] == current:
                self._entries.move_to_end(key)
                return cached[1]
        value = compute()
        with self._lock:
            self._entries[key] = (current, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
