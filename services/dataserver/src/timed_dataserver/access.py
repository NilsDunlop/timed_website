"""Startup access checks: a path this account can't read never stops the server.

The server checks each configured path once, as the account it runs under. A
data root, metadata folder, collection, mask folder, segmentation folder or QC
export that the account can't read is left out of service and reported in the
handshake, so that the web app can say what this account can't read. An
existing annotations folder it can't write is reported too and keeps QC
read-only, as before. Only a permission problem is reported: a path that
doesn't exist keeps its existing error or behaviour.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .models import AccessIssue
from .paths import os_error_reason
from .qc_config import QcPathConfig
from .settings import ServerSettings
from .timed_mask_config import TimedMaskMapping

NOT_WRITABLE = "Not writable"


def denied(path: Path) -> bool:
    """Whether this account can't even look the path up."""
    try:
        os.stat(path)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return False


def unreadable(path: Path) -> str | None:
    """Why this account can't read an existing file or folder, or None when it can.

    A folder must be listable and searchable; a file must open for reading,
    which opens it read-only. A missing path is not an access problem.
    """
    try:
        if not stat.S_ISDIR(os.stat(path).st_mode):
            with open(path, "rb"):
                return None
        with os.scandir(path) as entries:
            next(entries, None)
    except PermissionError as exc:
        return os_error_reason(exc)
    except (OSError, ValueError):
        return None
    if not os.access(path, os.R_OK | os.X_OK):
        return "Permission denied"
    return None


def unwritable(path: Path) -> str | None:
    """Why this account can't write an existing folder, or None.

    A missing folder is not an access problem: QC stays read-only, as before.
    """
    try:
        status = os.stat(path)
    except PermissionError as exc:
        return os_error_reason(exc)
    except (OSError, ValueError):
        return None
    if stat.S_ISDIR(status.st_mode) and not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        return NOT_WRITABLE
    return None


@dataclass(frozen=True, slots=True)
class StartupAccess:
    """The configuration this account can serve, and what it can't read."""

    data_roots: tuple[Path, ...]
    metadata_root: Path | None
    qc_paths: tuple[QcPathConfig, ...]
    timed_masks: tuple[TimedMaskMapping, ...]
    issues: tuple[AccessIssue, ...]


def _normal(path: Path) -> Path:
    return Path(os.path.normpath(path.expanduser()))


def _collections(settings: ServerSettings) -> dict[Path, str]:
    """Every configured collection inside a data root, keyed by path, with its spelling.

    A collection outside every data root is never opened here: the guard
    refuses it later, as before.
    """
    roots = [_normal(root) for root in settings.data_roots]
    candidates = [
        *((Path(raw).expanduser(), raw) for raw in settings.collections),
        *((config.index_path, str(config.index_path)) for config in settings.qc_paths),
        *((mapping.collection, str(mapping.collection)) for mapping in settings.timed_masks),
    ]
    spellings: dict[Path, str] = {}
    for path, spelling in candidates:
        if any(_normal(path).is_relative_to(root) for root in roots):
            spellings.setdefault(path, spelling)
    return spellings


def check_startup_access(settings: ServerSettings) -> StartupAccess:
    """Check every configured path and keep in service only what this account can read.

    A data root the account can't look up is left out of the guard; one it can
    look up but not list stays in. A root is reported only when no collection
    lies in it, since each such collection is reported instead. QC and mask
    folders are reported without their paths, which profiles never show.
    """
    collections = _collections(settings)
    unreadable_collections: set[Path] = set()
    collection_issues: list[AccessIssue] = []
    for path, spelling in collections.items():
        reason = unreadable(path)
        if reason is not None:
            unreadable_collections.add(path)
            collection_issues.append(
                AccessIssue(kind="collection", reason=reason, path=spelling, collection=spelling)
            )

    data_roots: list[Path] = []
    root_issues: list[AccessIssue] = []
    for root in settings.data_roots:
        expanded = root.expanduser()
        reason = unreadable(expanded)
        if reason is not None and not any(path.is_relative_to(expanded) for path in collections):
            root_issues.append(AccessIssue(kind="data_root", reason=reason, path=str(root)))
        if not denied(expanded):
            data_roots.append(root)

    metadata_root = settings.metadata_root
    metadata_issues: list[AccessIssue] = []
    if metadata_root is not None:
        reason = unreadable(metadata_root.expanduser())
        if reason is not None:
            metadata_issues.append(
                AccessIssue(kind="metadata", reason=reason, path=str(metadata_root))
            )
            metadata_root = None

    feature_issues: list[AccessIssue] = []
    timed_masks: list[TimedMaskMapping] = []
    for mapping in settings.timed_masks:
        if mapping.collection in unreadable_collections:
            continue
        reason = unreadable(mapping.masks_root)
        if reason is not None:
            feature_issues.append(
                AccessIssue(
                    kind="masks" if mapping.layout == "export" else "segmentation",
                    reason=reason,
                    collection=collections.get(mapping.collection, str(mapping.collection)),
                )
            )
            continue
        timed_masks.append(mapping)

    qc_paths: list[QcPathConfig] = []
    for config in settings.qc_paths:
        if config.index_path in unreadable_collections:
            continue
        collection = collections.get(config.index_path, str(config.index_path))
        reason = unreadable(config.export_path)
        if reason is not None:
            feature_issues.append(
                AccessIssue(kind="qc_export", reason=reason, collection=collection)
            )
        elif (reason := unwritable(config.annotations_path)) is not None:
            feature_issues.append(
                AccessIssue(kind="qc_annotations", reason=reason, collection=collection)
            )
        qc_paths.append(config)

    return StartupAccess(
        data_roots=tuple(data_roots),
        metadata_root=metadata_root,
        qc_paths=tuple(qc_paths),
        timed_masks=tuple(timed_masks),
        issues=(*root_issues, *metadata_issues, *collection_issues, *feature_issues),
    )
