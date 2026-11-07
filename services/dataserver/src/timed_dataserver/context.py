"""State shared by one data-server application's routes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import anyio
from fastapi import Request

from .mask_reader import MaskReader
from .packed import PackedCatalogueCache
from .packed_io import PackedReader
from .packed_metadata import PackedMetadataCache
from .packed_profile_cache import PackedProfileResolver
from .paths import RootGuard
from .plate_metadata import PlateMetadataCatalog
from .qc_annotations import QcAnnotationStore
from .qc_config import QcPathConfig, QcPrivateRoots, resolve_qc_path_configs
from .qc_export import QcExportStore
from .registry import DatasetRegistry
from .settings import ServerSettings


@dataclass(frozen=True, slots=True)
class ServerContext:
    """The guard, registry, caches, readers and QC stores of one application."""

    settings: ServerSettings
    guard: RootGuard
    qc_paths: Mapping[Path, QcPathConfig]
    qc_private_roots: QcPrivateRoots
    qc_stores: dict[Path, tuple[object, QcExportStore]]
    qc_annotation_stores: dict[Path, QcAnnotationStore]
    registry: DatasetRegistry
    packed_catalogues: PackedCatalogueCache
    packed_reader: PackedReader
    packed_metadata: PackedMetadataCache
    packed_profiles: PackedProfileResolver
    metadata_catalog: PlateMetadataCatalog | None
    masks: MaskReader

    @classmethod
    def build(cls, settings: ServerSettings) -> ServerContext:
        """Validate the settings and construct the shared state in a fixed order."""
        if settings.packed_cache_mb <= 0:
            raise ValueError("Packed cache size must be greater than zero")
        if settings.mask_cache_mb <= 0:
            raise ValueError("Mask cache size must be greater than zero")
        guard = RootGuard(settings.data_roots)
        qc_paths = resolve_qc_path_configs(settings.qc_paths, guard)
        return cls(
            settings=settings,
            guard=guard,
            qc_paths=qc_paths,
            qc_private_roots=QcPrivateRoots.from_configs(qc_paths, guard),
            qc_stores={},
            qc_annotation_stores={},
            registry=DatasetRegistry(settings.state_dir),
            packed_catalogues=PackedCatalogueCache(),
            packed_reader=PackedReader(cache_bytes=settings.packed_cache_mb * 1024 * 1024),
            packed_metadata=PackedMetadataCache(),
            packed_profiles=PackedProfileResolver(),
            metadata_catalog=(
                PlateMetadataCatalog(settings.metadata_root)
                if settings.metadata_root is not None
                else None
            ),
            masks=MaskReader(cache_bytes=settings.mask_cache_mb * 1024 * 1024),
        )

    async def close(self) -> None:
        """Release what the application holds open; called when its lifespan ends."""
        await anyio.to_thread.run_sync(self.packed_reader.close)
        self.masks.close()


async def get_context(request: Request) -> ServerContext:
    """Route dependency: the context of the application serving the request."""
    return request.app.state.context
