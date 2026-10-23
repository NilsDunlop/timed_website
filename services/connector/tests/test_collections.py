from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError
from timed_connector.app import create_app
from timed_connector.models import Profile
from timed_connector.profiles import (
    ProfileError,
    ProfileStore,
    load_profiles,
    validate_profile,
)
from timed_connector.session import ConnectorSession, SessionBackend, SessionError


def slurm_profile(*, collections: list[dict[str, Any]] | None = None) -> Profile:
    return Profile.model_validate(
        {
            "id": "test",
            "name": "Test cluster",
            "kind": "slurm",
            "host": "cluster.example",
            "username": "alice",
            "data_roots": ["/data/root"],
            "slurm": {
                "account": "dev",
                "partition": "cpu",
                "time": "01:00:00",
                "cpus": 2,
                "mem": "4G",
            },
            "collections": collections or [],
        }
    )


def test_legacy_profile_loads_with_no_collections(tmp_path: Path) -> None:
    profile_file = tmp_path / "profiles.yaml"
    profile_file.write_text(
        """
profiles:
  - id: cluster
    name: Cluster
    kind: slurm
    host: cluster.example
    username: alice
    data_roots: [/data]
    slurm: {account: dev, partition: cpu, time: "01:00:00", cpus: 2, mem: 4G}
""".strip(),
        encoding="utf-8",
    )

    profile = load_profiles(profile_file).get("cluster")

    assert profile.collections == []
    assert profile.model_dump()["collections"] == []


def test_collection_id_is_a_slug_and_optional_fields_have_defaults() -> None:
    profile = slurm_profile(
        collections=[{"id": "idr0080-raw", "title": "idr0080", "path": "/data/root"}]
    )

    collection = profile.collections[0]
    assert collection.description is None
    assert collection.default is False

    with pytest.raises(ValidationError, match="String should match pattern"):
        slurm_profile(collections=[{"id": "Not_A_Slug", "title": "Invalid", "path": "/data/root"}])


@pytest.mark.parametrize(
    ("collections", "message"),
    [
        (
            [
                {"id": "timed", "title": "TIMED", "path": "/data/root/timed"},
                {"id": "timed", "title": "Other", "path": "/data/root/other"},
            ],
            "Collection ids must be unique",
        ),
        (
            [
                {
                    "id": "timed",
                    "title": "TIMED",
                    "path": "/data/root/timed",
                    "default": True,
                },
                {
                    "id": "idr0080",
                    "title": "idr0080",
                    "path": "/data/root/idr0080",
                    "default": True,
                },
            ],
            "At most one collection may be the default",
        ),
    ],
)
def test_collection_ids_and_default_are_unambiguous(
    collections: list[dict[str, Any]], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        slurm_profile(collections=collections)


@pytest.mark.parametrize(
    "path",
    [
        "/data/root/../outside",
        "/data/root-sibling/dataset",
        "/outside/dataset",
    ],
)
def test_slurm_collection_paths_must_be_lexically_inside_a_data_root(
    path: str,
) -> None:
    with pytest.raises(ValidationError, match="must be inside a data root"):
        slurm_profile(collections=[{"id": "outside", "title": "Outside", "path": path}])


def test_slurm_collection_path_can_equal_or_descend_from_a_data_root() -> None:
    profile = slurm_profile(
        collections=[
            {"id": "root", "title": "Root", "path": "/data/root"},
            {"id": "nested", "title": "Nested", "path": "/data/root/a/../b"},
        ]
    )

    assert [collection.id for collection in profile.collections] == ["root", "nested"]


def test_local_collection_paths_are_resolved_existing_and_contained(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    nested = data_root / "nested"
    nested.mkdir()
    index = data_root / "dataset.index.parquet"
    index.touch()
    outside = tmp_path / "outside"
    outside.mkdir()

    profile = Profile.model_validate(
        {
            "id": "local",
            "name": "Local",
            "kind": "local",
            "data_roots": [str(data_root)],
            "collections": [
                {
                    "id": "dataset",
                    "title": "Dataset",
                    "path": str(nested / ".." / index.name),
                }
            ],
        }
    )
    store = ProfileStore([profile], tmp_path / "valid-profiles.yaml")
    assert validate_profile(store, profile.id) is profile

    missing = Profile.model_validate(
        {
            "id": "missing",
            "name": "Missing",
            "kind": "local",
            "data_roots": [str(data_root)],
            "collections": [
                {
                    "id": "missing",
                    "title": "Missing",
                    "path": str(data_root / "missing"),
                }
            ],
        }
    )
    with pytest.raises(ProfileError, match="path does not exist"):
        validate_profile(ProfileStore([missing], tmp_path / "missing-profiles.yaml"), missing.id)

    escaped = Profile.model_validate(
        {
            "id": "escaped",
            "name": "Escaped",
            "kind": "local",
            "data_roots": [str(data_root)],
            "collections": [
                {
                    "id": "outside",
                    "title": "Outside",
                    "path": str(data_root / ".." / outside.name),
                }
            ],
        }
    )
    with pytest.raises(ProfileError, match="must be inside a data root"):
        validate_profile(ProfileStore([escaped], tmp_path / "escaped-profiles.yaml"), escaped.id)


@pytest.mark.asyncio
async def test_missing_local_collection_loads_but_fails_when_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_root = tmp_path / "data"
    data_root.mkdir()
    missing = data_root / "future-dataset"
    profile_file = tmp_path / "profiles.yaml"
    profile_file.write_text(
        """
profiles:
  - id: future
    name: Future fixture
    kind: local
    data_roots: [data]
    collections:
      - id: future
        title: Future fixture
        path: data/future-dataset
""".strip(),
        encoding="utf-8",
    )

    store = load_profiles(profile_file)
    canonical_missing = str(missing.resolve(strict=False))
    assert store.get("future").collections[0].path == canonical_missing

    with pytest.raises(ProfileError, match="Local collection 'future' path does not exist"):
        validate_profile(store, "future")

    backend = cast(SessionBackend, object())
    session = ConnectorSession(store, backend)
    app = create_app(store, session, port=8700)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8700",
    ) as client:
        response = await client.get("/connector/profiles")
    assert response.status_code == 200
    assert response.json()[0]["collections"][0]["path"] == canonical_missing

    with pytest.raises(SessionError, match="Local collection 'future' path does not exist"):
        await session.connect("future")
    assert session.snapshot().state == "disconnected"


def test_local_collection_symlinks_are_checked_after_resolution(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    inside = data_root / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    inside_link = data_root / "inside-link"
    outside_link = data_root / "outside-link"
    try:
        inside_link.symlink_to(inside, target_is_directory=True)
        outside_link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")

    valid = Profile.model_validate(
        {
            "id": "valid-link",
            "name": "Valid link",
            "kind": "local",
            "data_roots": [str(data_root)],
            "collections": [{"id": "inside", "title": "Inside", "path": str(inside_link)}],
        }
    )
    assert validate_profile(ProfileStore([valid], tmp_path / "valid-link.yaml"), valid.id) is valid

    escaped = Profile.model_validate(
        {
            "id": "escaped-link",
            "name": "Escaped link",
            "kind": "local",
            "data_roots": [str(data_root)],
            "collections": [{"id": "outside", "title": "Outside", "path": str(outside_link)}],
        }
    )
    with pytest.raises(ProfileError, match="must be inside a data root"):
        validate_profile(ProfileStore([escaped], tmp_path / "escaped-link.yaml"), escaped.id)


@pytest.mark.asyncio
async def test_profiles_endpoint_exposes_typed_collections_without_secrets(
    tmp_path: Path,
) -> None:
    profile = slurm_profile(
        collections=[
            {
                "id": "timed",
                "title": "TIMED",
                "description": "Live-cell imaging",
                "path": "/data/root/timed",
                "default": True,
            }
        ]
    )
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    backend = cast(SessionBackend, object())
    app = create_app(store, ConnectorSession(store, backend), port=8700)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8700",
    ) as client:
        response = await client.get("/connector/profiles")

    assert response.status_code == 200
    assert response.json()[0]["collections"] == [
        {
            "id": "timed",
            "title": "TIMED",
            "description": "Live-cell imaging",
            "path": "/data/root/timed",
            "default": True,
        }
    ]
    serialized = response.text.lower()
    assert "password" not in serialized
    assert "token" not in serialized
