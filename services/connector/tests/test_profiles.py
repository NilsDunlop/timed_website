"""Shared and personal profile files, per-user placeholders and the profile CLI defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from timed_connector.cli import main
from timed_connector.models import Profile
from timed_connector.profiles import (
    SHARED_PROFILES,
    ProfileError,
    load_profile_files,
    load_profiles,
)


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
