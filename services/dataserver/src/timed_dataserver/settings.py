"""Runtime settings for one data-server process."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .qc_config import QcPathConfig
from .timed_mask_config import TimedMaskMapping


@dataclass(frozen=True)
class ServerSettings:
    """Runtime paths and environment advertised by one server process."""

    data_roots: tuple[Path, ...]
    state_dir: Path
    environment: Literal["local", "hpc"]
    metadata_root: Path | None = None
    packed_cache_mb: int = 1024
    qc_paths: tuple[QcPathConfig, ...] = ()
    mask_cache_mb: int = 512
    timed_masks: tuple[TimedMaskMapping, ...] = ()
    collections: tuple[str, ...] = ()
    """Configured collection paths, spelled as the profile gives them."""
