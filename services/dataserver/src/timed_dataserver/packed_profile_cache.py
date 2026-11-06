"""Pinned or generic display profiles for packed indexes."""

from __future__ import annotations

import threading
from concurrent.futures import Future

import anyio

from .packed import PackedVirtualPath
from .packed_profiles import (
    PackedDatasetProfile,
    PackedProbeSignature,
    PackedProfileError,
    packed_probe_signature,
    profile_for_catalogue,
    profile_for_index,
)

GENERIC_PROFILE_ENTRIES = 256

_GenericKey = tuple[str, int, int, PackedProbeSignature]


class PackedProfileResolver:
    """Resolve a packed index's profile once per index revision and shard probe.

    A pinned profile from code wins. Any other index gets a generic profile probed
    from its shards: concurrent requests share one probe, a probe that sees its
    shard change is retried up to three times, and results are kept in a FIFO of
    ``GENERIC_PROFILE_ENTRIES`` that a hit does not reorder.
    """

    def __init__(self) -> None:
        self._profiles: dict[_GenericKey, PackedDatasetProfile] = {}
        self._loads: dict[_GenericKey, Future[PackedDatasetProfile | None]] = {}
        self._lock = threading.Lock()

    def pinned(self, packed: PackedVirtualPath) -> PackedDatasetProfile | None:
        """Return the code-pinned profile for this index, if one matches."""
        return profile_for_index(packed.index_path.name, packed.catalogue.row_count)

    async def resolve(self, packed: PackedVirtualPath) -> PackedDatasetProfile:
        """Return the pinned profile, or probe a generic one in a worker thread."""
        known = self.pinned(packed)
        if known is not None:
            return known
        return await anyio.to_thread.run_sync(self._generic, packed)

    def _generic(self, packed: PackedVirtualPath) -> PackedDatasetProfile:
        index_signature = packed.catalogue.signature
        for _attempt in range(3):
            probe_signature = packed_probe_signature(packed.catalogue)
            key = (
                str(packed.index_path),
                index_signature.mtime_ns,
                index_signature.size,
                probe_signature,
            )
            with self._lock:
                cached = self._profiles.get(key)
                if cached is not None:
                    return cached
                load = self._loads.get(key)
                is_loader = load is None
                if load is None:
                    load = Future()
                    self._loads[key] = load
            if not is_loader:
                profile = load.result()
                if profile is None:
                    continue
                return profile

            try:
                profile = profile_for_catalogue(packed.catalogue)
                if packed_probe_signature(packed.catalogue) != probe_signature:
                    load.set_result(None)
                    continue
                with self._lock:
                    if len(self._profiles) >= GENERIC_PROFILE_ENTRIES:
                        self._profiles.pop(next(iter(self._profiles)))
                    self._profiles[key] = profile
                load.set_result(profile)
                return profile
            except BaseException as exc:
                load.set_exception(exc)
                raise
            finally:
                with self._lock:
                    if self._loads.get(key) is load:
                        del self._loads[key]
        raise PackedProfileError("Generic packed shard changed repeatedly while probing")
