"""Centralized path containment checks for all filesystem access.

User-facing errors never show an absolute path: they name a file by its name or
by its path inside its data root, dataset or folder, and give an OS error's
reason without the file name that ``str(exc)`` appends.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def os_error_reason(exc: OSError) -> str:
    """The reason of an OS error, without the file names ``str(exc)`` includes."""
    if exc.strerror:
        return exc.strerror
    if exc.filename is None and exc.filename2 is None:
        return str(exc) or type(exc).__name__
    return type(exc).__name__


def error_reason(exc: Exception, path: Path | None = None) -> str:
    """An exception's reason for a user-facing message.

    An OS error gives its reason without file names. Libraries such as pyarrow
    write the file into the message itself, so any spelling of ``path`` is
    replaced by its name.
    """
    reason = os_error_reason(exc) if isinstance(exc, OSError) else str(exc)
    if path is not None:
        for spelling in {str(path), path.as_posix()}:
            reason = reason.replace(spelling, path.name)
    return reason


class PathAccessError(ValueError):
    """Base class for safe-path failures."""


class PathForbiddenError(PathAccessError):
    """Raised when a path is outside an allowed root or is lexically unsafe."""


class PathNotFoundError(PathAccessError):
    """Raised when a safe path does not exist."""


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(candidate), str(parent))) == str(parent)
    except ValueError:
        return False


def _reject_parent_segments(raw: str) -> None:
    if "\x00" in raw or ".." in raw.replace("\\", "/").split("/"):
        raise PathForbiddenError("Parent path segments are not allowed")


class RootGuard:
    """Resolve paths while following symlinks and enforcing configured roots."""

    def __init__(
        self,
        roots: tuple[Path, ...] | list[Path],
        *,
        allow_empty: bool = False,
    ) -> None:
        """Resolve the roots; ``allow_empty`` admits none, when the account can read none."""
        if not roots and not allow_empty:
            raise ValueError("At least one data root is required")
        resolved: list[Path] = []
        for root in roots:
            try:
                value = root.expanduser().resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"Data root does not exist: {root}") from exc
            if not value.is_dir():
                raise ValueError(f"Data root is not a directory: {value}")
            if value not in resolved:
                resolved.append(value)
        self.roots = tuple(resolved)

    def resolve(self, raw: str, *, must_exist: bool = True) -> Path:
        """Resolve an absolute path, or a relative path beneath one data root."""
        _reject_parent_segments(raw)
        supplied = Path(raw).expanduser()
        candidates = (
            [supplied] if supplied.is_absolute() else [root / supplied for root in self.roots]
        )
        if not candidates:
            raise PathForbiddenError("Path is outside the configured data roots")
        safe_missing: Path | None = None
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=must_exist)
            except OSError:
                continue
            if not any(_is_within(resolved, root) for root in self.roots):
                continue
            if must_exist and not resolved.exists():
                continue
            if resolved.exists():
                return resolved
            safe_missing = resolved
        if safe_missing is not None:
            return safe_missing

        # Distinguish an existing escape (including a symlink escape) from a
        # missing path so callers can return the required 403 or 404.
        try:
            resolved_supplied = candidates[0].resolve(strict=False)
        except OSError as exc:
            raise PathNotFoundError(self._missing(raw, supplied, None)) from exc
        if not any(_is_within(resolved_supplied, root) for root in self.roots):
            raise PathForbiddenError("Path is outside the configured data roots")
        raise PathNotFoundError(self._missing(raw, supplied, resolved_supplied))

    def _missing(self, raw: str, supplied: Path, resolved: Path | None) -> str:
        """Name a missing path relative to its data root, never absolutely."""
        if not supplied.is_absolute():
            return f"Path does not exist: {raw}"
        for root in self.roots:
            if resolved is not None and _is_within(resolved, root):
                relative = Path(os.path.relpath(resolved, root)).as_posix()
                return f"Path does not exist: {relative}"
        return "Path does not exist"

    def child(self, base: Path, relative: str, *, must_exist: bool = True) -> Path:
        """Resolve a URL-style child path without allowing escape from base."""
        _reject_parent_segments(relative)
        logical = PurePosixPath(relative)
        if logical.is_absolute() or "\\" in relative:
            raise PathForbiddenError("Store paths must be relative POSIX paths")
        resolved_base = base.resolve(strict=True)
        child_parts = [part for part in logical.parts if part not in ("", ".")]
        candidate = resolved_base.joinpath(*child_parts)
        try:
            resolved = candidate.resolve(strict=must_exist)
        except OSError as exc:
            raise PathNotFoundError(f"Path does not exist: {relative}") from exc
        if not _is_within(resolved, resolved_base):
            raise PathForbiddenError("Path escapes the linked dataset")
        if must_exist and not resolved.exists():
            raise PathNotFoundError(f"Path does not exist: {relative}")
        return resolved

    def root_for(self, path: Path) -> Path:
        resolved = path.resolve(strict=True)
        for root in self.roots:
            if _is_within(resolved, root):
                return root
        raise PathForbiddenError("Path is outside the configured data roots")


@dataclass(frozen=True, slots=True)
class PrivateFolders:
    """Configured folders that are never browsable, linkable or served."""

    roots: frozenset[Path]
    data_roots: tuple[Path, ...]

    def contains(self, path: Path) -> bool:
        """Whether a resolved path is one of the folders or lies inside one."""
        return any(path == root or path.is_relative_to(root) for root in self.roots)

    def contains_raw(self, raw: str) -> bool:
        """Whether a requested path, before guard resolution, names one of the folders."""
        try:
            supplied = Path(raw).expanduser()
            candidates = (
                (supplied,)
                if supplied.is_absolute()
                else tuple(root / supplied for root in self.data_roots)
            )
            return any(
                self.contains(candidate.resolve(strict=False))
                for candidate in candidates
            )
        except (OSError, ValueError):
            return False
