"""Validated runtime paths for configured QC exports and annotations."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from .paths import RootGuard


class QcConfigurationError(ValueError):
    """Raised when QC path mappings are malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class QcPathConfig:
    """One index-keyed QC export and its sole writable annotations folder."""

    index_path: Path
    export_path: Path
    annotations_path: Path

    @property
    def export_readable(self) -> bool:
        return self.export_path.is_dir() and os.access(
            self.export_path,
            os.R_OK | os.X_OK,
        )

    @property
    def annotations_writable(self) -> bool:
        return self.annotations_path.is_dir() and os.access(
            self.annotations_path,
            os.R_OK | os.W_OK | os.X_OK,
        )


def _parse_mapping(values: Sequence[str], option: str) -> dict[Path, Path]:
    mappings: dict[Path, Path] = {}
    for value in values:
        if value.count("=") != 1:
            raise QcConfigurationError(
                f"{option} must use exactly one index=folder separator"
            )
        raw_index, raw_folder = value.split("=", 1)
        if not raw_index or not raw_folder:
            raise QcConfigurationError(f"{option} requires a non-empty index and folder")
        index_path = Path(raw_index).expanduser()
        folder_path = Path(raw_folder).expanduser()
        if not index_path.is_absolute() or not folder_path.is_absolute():
            raise QcConfigurationError(f"{option} paths must be absolute")
        if index_path in mappings:
            raise QcConfigurationError(f"{option} repeats an index path")
        mappings[index_path] = folder_path
    return mappings


def parse_qc_path_configs(
    export_values: Sequence[str],
    annotation_values: Sequence[str],
) -> tuple[QcPathConfig, ...]:
    """Pair repeated CLI mappings without resolving trusted folders."""
    exports = _parse_mapping(export_values, "--qc-export")
    annotations = _parse_mapping(annotation_values, "--qc-annotations")
    if exports.keys() != annotations.keys():
        raise QcConfigurationError(
            "--qc-export and --qc-annotations must configure the same index paths"
        )
    return tuple(
        QcPathConfig(
            index_path=index_path,
            export_path=export_path,
            annotations_path=annotations[index_path],
        )
        for index_path, export_path in exports.items()
    )


def resolve_qc_path_configs(
    configs: Sequence[QcPathConfig],
    guard: RootGuard,
) -> Mapping[Path, QcPathConfig]:
    """Canonicalize configured indexes through the data-root guard.

    Export and annotation folders are explicit trusted configuration. They are
    normalized for stable status reporting but never added to ``RootGuard``.
    """
    resolved: dict[Path, QcPathConfig] = {}
    export_paths = tuple(
        config.export_path.resolve(strict=False) for config in configs
    )
    for config in configs:
        try:
            index_path = guard.resolve(str(config.index_path))
        except ValueError as exc:
            raise QcConfigurationError(f"Invalid configured QC index: {exc}") from exc
        if not index_path.is_file():
            raise QcConfigurationError("Configured QC index is not a file")
        if index_path in resolved:
            raise QcConfigurationError("QC configuration repeats a canonical index path")
        export_path = config.export_path.resolve(strict=False)
        annotations_path = config.annotations_path.resolve(strict=False)
        if any(
            annotations_path == root
            or annotations_path.is_relative_to(root)
            or root.is_relative_to(annotations_path)
            for root in guard.roots
        ):
            raise QcConfigurationError(
                "Configured QC annotations cannot overlap a data root"
            )
        if any(
            annotations_path == candidate
            or annotations_path.is_relative_to(candidate)
            or candidate.is_relative_to(annotations_path)
            for candidate in export_paths
        ):
            raise QcConfigurationError(
                "Configured QC annotations cannot overlap a QC export"
            )
        for private_path in (export_path, annotations_path):
            if any(
                private_path == root or root.is_relative_to(private_path)
                for root in guard.roots
            ):
                raise QcConfigurationError(
                    "Configured QC folders cannot contain a data root"
                )
        resolved[index_path] = replace(
            config,
            index_path=index_path,
            export_path=export_path,
            annotations_path=annotations_path,
        )
    return MappingProxyType(resolved)
