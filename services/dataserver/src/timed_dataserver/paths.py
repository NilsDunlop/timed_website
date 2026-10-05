"""Centralized path containment checks for all filesystem access."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath


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

    def __init__(self, roots: tuple[Path, ...] | list[Path]) -> None:
        if not roots:
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
            raise PathNotFoundError(f"Path does not exist: {raw}") from exc
        if not any(_is_within(resolved_supplied, root) for root in self.roots):
            raise PathForbiddenError("Path is outside the configured data roots")
        raise PathNotFoundError(f"Path does not exist: {raw}")

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
