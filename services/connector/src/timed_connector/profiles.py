"""YAML profile loading and validation.

By default the connector reads two files. The shared ``config/profiles.yaml``
in the repository holds settings every user shares and no personal values. The
optional personal ``~/.timed/profiles.local.yaml`` is never committed: an entry
whose ``id`` matches a shared profile overrides that profile's fields, and any
other entry adds a personal profile. Overrides merge mappings key by key and
collections by ``id``; any other value, including a list, replaces the shared
one. An explicit ``--profiles`` file is read alone, exactly as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import Profile, ProfilesDocument

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SHARED_PROFILES = REPOSITORY_ROOT / "config" / "profiles.yaml"
PERSONAL_PROFILES = Path.home() / ".timed" / "profiles.local.yaml"


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


def _read_yaml(source: Path) -> Any:
    try:
        return yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"Could not read profiles file {source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f"Invalid profiles file {source}: {exc}") from exc


def _read_document(source: Path) -> dict[str, Any]:
    raw = _read_yaml(source)
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
        raise ProfileError(f"Invalid profiles file {source}: expected a 'profiles' list")
    return raw


def _validated(raw: Any, source: Path) -> ProfileStore:
    try:
        document = ProfilesDocument.model_validate(raw)
    except ValidationError as exc:
        raise ProfileError(f"Invalid profiles file {source}: {exc}") from exc
    return ProfileStore(document.profiles, source)


def load_profiles(path: Path) -> ProfileStore:
    """Load one profiles file with strict Pydantic validation and no interpolation."""
    source = path.expanduser().resolve()
    return _validated(_read_yaml(source), source)


def _merge_by_id(shared: list[Any], personal: list[Any], where: str) -> list[Any]:
    merged = list(shared)
    positions = {
        item.get("id"): index for index, item in enumerate(merged) if isinstance(item, dict)
    }
    for item in personal:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ProfileError(f"Every entry in {where} needs an id")
        index = positions.get(item["id"])
        if index is None:
            positions[item["id"]] = len(merged)
            merged.append(item)
        else:
            merged[index] = _merge(merged[index], item, f"{where} {item['id']!r}")
    return merged


def _merge(shared: dict[str, Any], personal: dict[str, Any], where: str) -> dict[str, Any]:
    merged = dict(shared)
    for key, value in personal.items():
        current = merged.get(key)
        if key == "collections" and isinstance(current, list) and isinstance(value, list):
            merged[key] = _merge_by_id(current, value, f"{where} collections")
        elif isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge(current, value, f"{where} {key}")
        else:
            merged[key] = value
    return merged


def load_profile_files(
    shared: Path = SHARED_PROFILES,
    personal: Path | None = PERSONAL_PROFILES,
) -> ProfileStore:
    """Load the shared profiles and, when it exists, the personal file on top."""
    shared_source = shared.expanduser().resolve()
    personal_source = None if personal is None else personal.expanduser().resolve()
    if personal_source is None or not personal_source.exists():
        return load_profiles(shared_source)
    raw = _read_document(shared_source)
    personal_raw = _read_document(personal_source)
    ids = [entry.get("id") for entry in personal_raw["profiles"] if isinstance(entry, dict)]
    repeated = sorted({value for value in ids if ids.count(value) > 1 and isinstance(value, str)})
    if repeated:
        raise ProfileError(
            f"Invalid profiles file {personal_source}: repeated ids {', '.join(repeated)}"
        )
    try:
        merged = _merge_by_id(raw["profiles"], personal_raw["profiles"], "profiles")
    except ProfileError as exc:
        raise ProfileError(f"Invalid profiles file {personal_source}: {exc}") from exc
    try:
        document = ProfilesDocument.model_validate({**raw, "profiles": merged})
    except ValidationError as exc:
        raise ProfileError(
            f"Invalid profiles from {shared_source} and {personal_source}: {exc}"
        ) from exc
    return ProfileStore(document.profiles, shared_source)


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
    if profile.ssh_key is not None and not _local_path(profile.ssh_key).is_file():
        raise ProfileError(f"SSH key file does not exist: {_local_path(profile.ssh_key)}")
    return profile


def _local_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path
