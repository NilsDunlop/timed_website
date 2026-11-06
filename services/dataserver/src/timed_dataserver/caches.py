"""Thread-safe caches shared by the data server's readers."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from typing import Literal


class ByteBoundedSingleFlightLru[K: Hashable, V]:
    """A least-recently-used cache bounded by value size, with one loader per key.

    Concurrent requests for a missing key wait for the first caller's load
    and receive its value or its exception. A value larger than the budget is
    returned but not retained.
    """

    def __init__(self, max_bytes: int, size_of: Callable[[V], int]) -> None:
        if max_bytes < 0:
            raise ValueError("Cache size cannot be negative")
        self.max_bytes = max_bytes
        self._size_of = size_of
        self._lock = threading.Lock()
        self._entries: OrderedDict[K, V] = OrderedDict()
        self._inflight: dict[K, Future[V]] = {}
        self._current_bytes = 0

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return self._current_bytes

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._current_bytes = 0

    def get_or_load(
        self,
        key: K,
        loader: Callable[[], V],
    ) -> tuple[V, Literal["hit", "miss"]]:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached, "hit"
            future = self._inflight.get(key)
            leader = future is None
            if future is None:
                future = Future()
                self._inflight[key] = future

        if not leader:
            return future.result(), "hit"

        try:
            loaded = loader()
        except BaseException as exc:
            with self._lock:
                self._inflight.pop(key, None)
                future.set_exception(exc)
            raise

        # Insert, evict, release the key, then wake followers: all under the lock.
        with self._lock:
            self._entries[key] = loaded
            self._entries.move_to_end(key)
            self._current_bytes += self._size_of(loaded)
            while self._entries and self._current_bytes > self.max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._current_bytes -= self._size_of(evicted)
            self._inflight.pop(key, None)
            future.set_result(loaded)
        return loaded, "miss"
