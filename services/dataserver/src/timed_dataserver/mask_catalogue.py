"""Segmentation mask models declared beside a packed index, and their plate files.

A packed profile declares a masks folder relative to its index. Each model is a
subfolder whose ``manifest.json`` names the ``tar+wal/v1`` format; folders with
any other format, or without a readable manifest, are skipped. A model covers a
plate when its tar and log exist. Nothing here opens a file other than a
manifest, and no symlink leads out of the masks folder.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .packed_profiles import PackedMasks
from .tar_io import (
    NotRegularFileError,
    ShortReadError,
    open_flags,
    read_exact_at,
    regular_stats,
    same_file_revision,
)

MANIFEST_NAME = "manifest.json"
STORE_FORMAT = "tar+wal/v1"
MASK_DTYPE = "uint16"
MAX_MANIFEST_BYTES = 1024 * 1024
MODEL_TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
KIND_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,31}")
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaskModel:
    """One model folder's manifest facts."""

    tag: str
    recipe: str | None
    kinds: tuple[str, ...]
    frozen: bool
    created_at: str | None
    directory: Path


@dataclass(frozen=True, slots=True)
class MaskPlateFiles:
    """The tar and log of one model's plate store, and its index snapshot."""

    model: MaskModel
    experiment: str
    plate: int
    tar: Path
    log: Path
    snapshot: Path

    @property
    def label(self) -> str:
        """The store as errors name it: inside its masks folder, never absolutely."""
        return f"{self.model.tag}/{self.experiment}/Plate{self.plate}"


def _within(candidate: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(candidate), str(parent))) == str(parent)
    except ValueError:
        return False


def masks_root(index_path: Path, masks: PackedMasks) -> Path | None:
    """The declared masks folder, resolved, or None when it is absent or escapes."""
    relative = masks.path
    logical = PurePosixPath(relative)
    if (
        not relative
        or "\x00" in relative
        or "\\" in relative
        or ":" in relative
        or logical.is_absolute()
        or ".." in logical.parts
    ):
        _LOG.warning("Ignoring a masks folder outside its index directory: %s", relative)
        return None
    index_directory = index_path.parent.resolve()
    candidate = index_directory.joinpath(*(part for part in logical.parts if part != "."))
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not _within(resolved, index_directory):
        _LOG.warning("Ignoring a masks folder that escapes its index directory: %s", relative)
        return None
    return resolved if resolved.is_dir() else None


def _read_manifest(path: Path) -> tuple[os.stat_result, bytes]:
    fd = os.open(path, open_flags())
    try:
        descriptor_stat, path_stat = regular_stats(fd, path)
        if not same_file_revision(descriptor_stat, path_stat):
            raise OSError("manifest changed while opening")
        if descriptor_stat.st_size > MAX_MANIFEST_BYTES:
            raise ValueError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
        return descriptor_stat, read_exact_at(fd, 0, descriptor_stat.st_size)
    finally:
        with suppress(OSError):
            os.close(fd)


def _recipe_name(value: object) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        return value["name"] or None
    return None


def parse_manifest(tag: str, directory: Path, content: bytes) -> MaskModel:
    """Validate one manifest; raise ValueError when its model must be skipped."""
    try:
        manifest = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not a JSON object")
    declared_tag = manifest.get("model_tag", tag)
    if declared_tag != tag:
        raise ValueError(f"manifest names model {declared_tag!r}, not its folder")
    schema = manifest.get("schema")
    if not isinstance(schema, dict):
        raise ValueError("manifest has no schema")
    if schema.get("store_format") != STORE_FORMAT:
        raise ValueError(f"store format is {schema.get('store_format')!r}, not {STORE_FORMAT}")
    if schema.get("mask_dtype") != MASK_DTYPE:
        raise ValueError(f"mask dtype is {schema.get('mask_dtype')!r}, not {MASK_DTYPE}")
    kinds = schema.get("kinds")
    if (
        not isinstance(kinds, list)
        or not kinds
        or not all(isinstance(kind, str) and KIND_PATTERN.fullmatch(kind) for kind in kinds)
        or len(set(kinds)) != len(kinds)
    ):
        raise ValueError("manifest kinds are not a list of distinct mask kinds")
    frozen = manifest.get("frozen", False)
    if not isinstance(frozen, bool):
        raise ValueError("manifest frozen flag is not a boolean")
    created_at = manifest.get("created_at")
    return MaskModel(
        tag=tag,
        recipe=_recipe_name(manifest.get("recipe")),
        kinds=tuple(kinds),
        frozen=frozen,
        created_at=created_at if isinstance(created_at, str) else None,
        directory=directory,
    )


class MaskCatalogue:
    """Read model manifests once per file revision; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._manifests: dict[Path, tuple[tuple[int, int, int, int], MaskModel | None]] = {}

    def models(self, root: Path) -> tuple[MaskModel, ...]:
        """Every supported model under a resolved masks folder, sorted by tag."""
        try:
            entries = sorted(os.scandir(root), key=lambda entry: entry.name)
        except OSError as exc:
            _LOG.warning("Could not list masks folder: %s", exc.strerror or type(exc).__name__)
            return ()
        models = []
        for entry in entries:
            if not MODEL_TAG_PATTERN.fullmatch(entry.name):
                continue
            with suppress(OSError):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                model = self.model(root, entry.name)
                if model is not None:
                    models.append(model)
        return tuple(models)

    def model(self, root: Path, tag: str) -> MaskModel | None:
        """One model by tag, or None when it is unknown, unsupported or unreadable."""
        if not MODEL_TAG_PATTERN.fullmatch(tag):
            return None
        directory = root / tag
        try:
            directory_stat = os.stat(directory, follow_symlinks=False)
        except OSError:
            return None
        if not stat.S_ISDIR(directory_stat.st_mode):
            return None
        path = directory / MANIFEST_NAME
        try:
            path_stat = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        identity = (path_stat.st_dev, path_stat.st_ino, path_stat.st_mtime_ns, path_stat.st_size)
        with self._lock:
            cached = self._manifests.get(path)
        if cached is not None and cached[0] == identity:
            return cached[1]
        model: MaskModel | None
        try:
            manifest_stat, content = _read_manifest(path)
            identity = (
                manifest_stat.st_dev,
                manifest_stat.st_ino,
                manifest_stat.st_mtime_ns,
                manifest_stat.st_size,
            )
            model = parse_manifest(tag, directory, content)
        except (OSError, NotRegularFileError, ShortReadError, ValueError) as exc:
            _LOG.warning("Skipping mask model %s: %s", tag, exc)
            model = None
        with self._lock:
            if len(self._manifests) >= 256 and path not in self._manifests:
                self._manifests.pop(next(iter(self._manifests)))
            self._manifests[path] = (identity, model)
        return model


def plate_files(model: MaskModel, experiment: str, plate: int) -> MaskPlateFiles | None:
    """The store paths for one plate, or None when its folder leaves the model."""
    experiment_directory = model.directory / experiment
    with suppress(OSError):
        resolved = experiment_directory.resolve(strict=True)
        if _within(resolved, model.directory.resolve()) and resolved.is_dir():
            stem = f"Plate{plate}"
            return MaskPlateFiles(
                model=model,
                experiment=experiment,
                plate=plate,
                tar=resolved / f"{stem}.masks.tar",
                log=resolved / f"{stem}.wal.jsonl",
                snapshot=resolved / f"{stem}.index.parquet",
            )
    return None


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.stat(path, follow_symlinks=False).st_mode)
    except OSError:
        return False


def plate_available(files: MaskPlateFiles | None) -> bool:
    """A model covers a plate when its tar and log exist as regular files."""
    return files is not None and _is_regular(files.tar) and _is_regular(files.log)
