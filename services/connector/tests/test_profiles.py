"""Shared and personal profile files, per-user placeholders and the profile CLI defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import ValidationError
from timed_connector.app import create_app
from timed_connector.cli import main
from timed_connector.local_backend import LocalBackend
from timed_connector.models import Profile
from timed_connector.profiles import (
    SHARED_PROFILES,
    ProfileError,
    ReloadingProfileStore,
    load_profile_files,
    load_profiles,
    validate_profile,
    watched_profiles,
)
from timed_connector.session import ConnectorSession


def _write(path: Path, profiles: list[dict[str, Any]]) -> Path:
    path.write_text(yaml.safe_dump({"profiles": profiles}, sort_keys=False), encoding="utf-8")
    return path


def _shared() -> dict[str, Any]:
    return {
        "id": "cluster",
        "name": "Cluster",
        "kind": "slurm",
        "host": "login.example.org",
        "data_roots": ["/data/images", "/data/cells"],
        "collections": [
            {"id": "timed", "title": "TIMED", "path": "/data/images", "default": True},
            {
                "id": "cells",
                "title": "Cells",
                "path": "/data/cells/index.parquet",
                "qc_export": "/data/qc/export",
                "qc_annotations": "/data/qc/annotations",
            },
        ],
        "slurm": {"account": "shared-project", "partition": "cpu", "time": "04:00:00", "cpus": 2},
    }


def test_personal_entries_override_shared_profiles_by_id_and_add_their_own(
    tmp_path: Path,
) -> None:
    shared = _write(tmp_path / "profiles.yaml", [_shared()])
    personal = _write(
        tmp_path / "profiles.local.yaml",
        [
            {
                "id": "cluster",
                "username": "alice",
                "slurm": {"account": "own-project"},
                "collections": [
                    {"id": "cells", "qc_annotations": "/home/alice/annotations"},
                    {"id": "extra", "title": "Extra", "path": "/data/images/extra"},
                ],
            },
            {
                "id": "laptop",
                "name": "This computer",
                "kind": "local",
                "data_roots": [str(tmp_path)],
            },
        ],
    )
    store = load_profile_files(shared, personal)

    assert [profile.id for profile in store.public()] == ["cluster", "laptop"]
    cluster = store.get("cluster")
    assert cluster.username == "alice"
    assert cluster.slurm is not None
    # Mappings merge key by key: the other Slurm settings stay shared.
    assert (cluster.slurm.account, cluster.slurm.partition, cluster.slurm.cpus) == (
        "own-project",
        "cpu",
        2,
    )
    # Collections merge by id; a new id is added after the shared ones.
    assert [collection.id for collection in cluster.collections] == ["timed", "cells", "extra"]
    cells = cluster.collections[1]
    assert cells.qc_export == "/data/qc/export"
    assert cells.qc_annotations == "/home/alice/annotations"
    assert cluster.data_roots == ["/data/images", "/data/cells"]


def test_lists_in_an_override_replace_the_shared_list(tmp_path: Path) -> None:
    shared = _write(tmp_path / "profiles.yaml", [_shared()])
    personal = _write(
        tmp_path / "profiles.local.yaml",
        [{"id": "cluster", "module_loads": ["Python/3.12"], "data_roots": ["/data"]}],
    )
    cluster = load_profile_files(shared, personal).get("cluster")
    assert cluster.module_loads == ["Python/3.12"]
    assert cluster.data_roots == ["/data"]


def test_without_a_personal_file_the_shared_profiles_load_alone(tmp_path: Path) -> None:
    shared = _write(tmp_path / "profiles.yaml", [_shared()])
    for personal in (tmp_path / "missing.yaml", None):
        store = load_profile_files(shared, personal)
        assert [profile.id for profile in store.public()] == ["cluster"]
        assert store.get("cluster").username is None
        assert store.source == shared.resolve()


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([{"id": "cluster"}, {"id": "cluster"}], "repeated ids cluster"),
        ([{"name": "No id"}], "needs an id"),
        ([{"id": "cluster", "collections": [{"title": "No id"}]}], "needs an id"),
    ],
)
def test_malformed_personal_files_name_the_personal_file(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    message: str,
) -> None:
    shared = _write(tmp_path / "profiles.yaml", [_shared()])
    personal = _write(tmp_path / "profiles.local.yaml", entries)
    with pytest.raises(ProfileError, match=message) as refused:
        load_profile_files(shared, personal)
    assert str(personal.resolve()) in str(refused.value)


def test_an_override_that_breaks_a_profile_names_both_files(tmp_path: Path) -> None:
    shared = _write(tmp_path / "profiles.yaml", [_shared()])
    personal = _write(tmp_path / "profiles.local.yaml", [{"id": "cluster", "kind": "other"}])
    with pytest.raises(ProfileError) as refused:
        load_profile_files(shared, personal)
    assert str(shared.resolve()) in str(refused.value)
    assert str(personal.resolve()) in str(refused.value)


def test_an_explicit_profiles_file_is_read_alone(tmp_path: Path) -> None:
    explicit = _write(tmp_path / "profiles.yaml", [_shared()])
    store = load_profiles(explicit)
    assert [profile.id for profile in store.public()] == ["cluster"]


def test_placeholders_fill_per_user_paths_at_connect() -> None:
    shared = _shared()
    shared["metadata_root"] = "/data/{username}/metadata"
    shared["collections"][1]["qc_annotations"] = "/home/{username}/annotations"
    profile = Profile.model_validate(shared)

    assert profile.collections[1].qc_annotations == "/home/{username}/annotations"
    alice = profile.for_user("alice")
    assert alice.username == "alice"
    assert alice.metadata_root == "/data/alice/metadata"
    assert alice.collections[1].qc_annotations == "/home/alice/annotations"
    # The profile itself is unchanged, so the next user gets their own paths.
    assert profile.for_user("bob").collections[1].qc_annotations == "/home/bob/annotations"
    assert profile.username is None


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(data_roots=["/data/{username}"]),
        lambda p: p.update(remote_state_dir="~/{username}"),
        lambda p: p.update(host="{username}.example.org"),
        lambda p: p["collections"][0].update(path="/data/images/{username}"),
    ],
)
def test_placeholders_are_refused_outside_server_side_per_user_paths(change: Any) -> None:
    values = _shared()
    change(values)
    with pytest.raises(ValidationError, match="is allowed only in metadata_root"):
        Profile.model_validate(values)


def test_local_profiles_take_no_placeholders(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="only for Slurm profiles"):
        Profile.model_validate(
            {
                "id": "laptop",
                "name": "Laptop",
                "kind": "local",
                "data_roots": [str(tmp_path)],
                "metadata_root": str(tmp_path / "{username}"),
            }
        )


@pytest.mark.parametrize("username", ["-oProxyCommand=x", "two words", "a/b", ""])
def test_a_profile_username_must_be_a_plain_login_name(username: str) -> None:
    with pytest.raises(ValidationError):
        Profile.model_validate({**_shared(), "username": username})


def test_check_profile_reads_the_shared_file_and_the_personal_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    personal = _write(tmp_path / "profiles.local.yaml", [{"id": "arrhenius", "username": "alice"}])
    assert main(["check-profile", "arrhenius", "--personal-profiles", str(personal)]) == 0
    merged = json.loads(capsys.readouterr().out)
    assert merged["username"] == "alice"
    assert merged["host"] == load_profiles(SHARED_PROFILES).get("arrhenius").host

    assert main(["check-profile", "arrhenius", "--personal-profiles", str(tmp_path / "none")]) == 0
    assert json.loads(capsys.readouterr().out)["username"] is None

    # An explicit --profiles file is read alone, without the personal file.
    explicit = _write(tmp_path / "only.yaml", [_shared()])
    assert main(["check-profile", "arrhenius", "--profiles", str(explicit)]) == 2
    assert "Unknown profile: arrhenius" in capsys.readouterr().err


def test_a_local_path_this_account_cant_read_still_connects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a missing local path stops connecting; the data server reports the rest."""
    data = tmp_path / "data"
    collection = data / "zarr"
    collection.mkdir(parents=True)
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    profile = {
        "id": "laptop",
        "name": "This computer",
        "kind": "local",
        "data_roots": [str(data)],
        "metadata_root": str(metadata),
        "collections": [{"id": "timed", "title": "TIMED", "path": str(collection)}],
    }
    store = load_profiles(_write(tmp_path / "profiles.yaml", [profile]))
    locked = {str(path.resolve()) for path in (data, collection, metadata)}
    real_stat = os.stat

    def stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if str(path) in locked:
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    validated = validate_profile(store, "laptop")
    assert LocalBackend._data_roots(validated) == [data.resolve()]
    assert LocalBackend._metadata_root(validated) == metadata.resolve()

    gone = {"id": "timed", "title": "TIMED", "path": str(data / "gone")}
    store = load_profiles(_write(tmp_path / "missing.yaml", [{**profile, "collections": [gone]}]))
    with pytest.raises(ProfileError, match="path does not exist"):
        validate_profile(store, "laptop")


def _touch_later(path: Path, seconds: int) -> None:
    """Move a file's modification time on, as a later save in an editor would."""
    later = path.stat().st_mtime_ns + seconds * 1_000_000_000
    os.utime(path, ns=(later, later))


def test_the_connector_follows_edits_to_its_profile_files(tmp_path: Path) -> None:
    shared = _write(tmp_path / "profiles.yaml", [_shared()])
    personal = tmp_path / "profiles.local.yaml"
    store = ReloadingProfileStore(
        lambda: load_profile_files(shared, personal), [shared, personal]
    )
    assert store.get("cluster").username is None

    _write(personal, [{"id": "cluster", "username": "alice"}])
    assert store.get("cluster").username == "alice"

    # A broken edit is reported until it is fixed, never hidden by the old profiles.
    personal.write_text("profiles: [", encoding="utf-8")
    _touch_later(personal, 1)
    with pytest.raises(ProfileError, match="profiles.local.yaml"):
        store.public()
    with pytest.raises(ProfileError, match="profiles.local.yaml"):
        store.get("cluster")

    _write(personal, [{"id": "cluster", "username": "bob"}])
    _touch_later(personal, 2)
    assert [profile.username for profile in store.public()] == ["bob"]

    personal.unlink()
    assert store.get("cluster").username is None


@pytest.mark.asyncio
async def test_the_profiles_route_and_connect_read_the_edited_file(tmp_path: Path) -> None:
    shared = _write(tmp_path / "profiles.yaml", [_shared()])
    store = watched_profiles(shared)
    app = create_app(store, ConnectorSession(store, FakeLoginBackend()), port=8700)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8700",
    ) as client:
        assert (await client.get("/connector/profiles")).json()[0]["name"] == "Cluster"

        _write(shared, [{**_shared(), "name": "Renamed cluster"}])
        _touch_later(shared, 1)
        assert (await client.get("/connector/profiles")).json()[0]["name"] == "Renamed cluster"

        shared.write_text("profiles: [", encoding="utf-8")
        _touch_later(shared, 2)
        listed = await client.get("/connector/profiles")
        assert listed.status_code == 500
        assert "profiles.yaml" in listed.json()["detail"]
        connect = await client.post(
            "/connector/connect",
            headers={"X-Timed-Client": "web"},
            json={"profile_id": "cluster", "username": "alice"},
        )
        assert connect.status_code == 409
        assert "profiles.yaml" in connect.json()["detail"]


class FakeLoginBackend:
    """A cluster backend these tests never reach: they stop before any login."""
