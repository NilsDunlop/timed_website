"""YAML profile loading and validation."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import Profile, ProfilesDocument


class ProfileError(ValueError):
    """Raised when a profiles file is unavailable or invalid."""


class ProfileStore:
    """An immutable, ordered collection of validated connection profiles."""

    def __init__(self, profiles: list[Profile], source: Path) -> None:
        self.profiles = tuple(profiles)
        self.source = source
        self._by_id = {profile.id: profile for profile in profiles}

    def get(self, profile_id: str) -> Profile:
        try:
            return self._by_id[profile_id]
        except KeyError as exc:
            raise ProfileError(f"Unknown profile: {profile_id}") from exc

    def public(self) -> list[Profile]:
        return list(self.profiles)


def load_profiles(path: Path) -> ProfileStore:
    """Load profiles with strict Pydantic validation and no interpolation."""
    source = path.expanduser().resolve()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        document = ProfilesDocument.model_validate(raw)
    except OSError as exc:
        raise ProfileError(f"Could not read profiles file {source}: {exc}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ProfileError(f"Invalid profiles file {source}: {exc}") from exc
    return ProfileStore(document.profiles, source)


def validate_profile(store: ProfileStore, profile_id: str) -> Profile:
    """Return one profile after checks which do not contact the remote host."""
    profile = store.get(profile_id)
    if profile.kind == "local":
        resolved_roots: list[Path] = []
        for root in profile.data_roots:
            path = _local_path(root)
            resolved = path.resolve()
            if not resolved.is_dir():
                raise ProfileError(f"Local profile path does not exist: {path}")
            resolved_roots.append(resolved)

        if profile.metadata_root is not None:
            metadata_root = _local_path(profile.metadata_root)
            if not metadata_root.resolve().is_dir():
                raise ProfileError(f"Local profile path does not exist: {metadata_root}")

        for collection in profile.collections:
            path = _local_path(collection.path)
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ProfileError(
                    f"Local collection {collection.id!r} path does not exist: {path}"
                ) from exc
            if not any(resolved.is_relative_to(root) for root in resolved_roots):
                raise ProfileError(
                    f"Local collection {collection.id!r} path must be inside a data root: {path}"
                )
    if profile.known_hosts is not None:
        known_hosts = Path(profile.known_hosts).expanduser()
        if not known_hosts.is_absolute():
            known_hosts = Path.cwd() / known_hosts
        if known_hosts.exists() and not known_hosts.is_file():
            raise ProfileError(f"known_hosts is not a file: {known_hosts}")
    return profile


def _local_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path
