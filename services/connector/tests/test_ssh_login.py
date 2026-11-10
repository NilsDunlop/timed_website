"""SSH login options: the typed username, a configured key alone, and a keyless retry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncssh
import pytest
from timed_connector import ssh_backend
from timed_connector.models import Profile, Prompt
from timed_connector.profiles import ProfileError, ProfileStore, validate_profile
from timed_connector.ssh_backend import HostKeyStatus, SSHBackendError, connect_ssh, login_options

HOST = "login.example.org"


def _profile(**changes: Any) -> Profile:
    values: dict[str, Any] = {
        "id": "cluster",
        "name": "Cluster",
        "kind": "slurm",
        "host": HOST,
        "username": "typed",
        "data_roots": ["/data"],
        "slurm": {"account": "project", "time": "01:00:00", "cpus": 1},
    }
    values.update(changes)
    return Profile.model_validate(values)


def _key(path: Path) -> Path:
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(path)
    return path


def _options(
    tmp_path: Path, profile: Profile, **changes: Any
) -> asyncssh.SSHClientConnectionOptions:
    config = tmp_path / "ssh_config"
    config_key = _key(tmp_path / "config_key")
    config.write_text(
        f"Host {HOST}\n  User from-config\n  IdentityFile {config_key.as_posix()}\n",
        encoding="utf-8",
    )
    options = {**login_options(profile), **changes}
    return asyncssh.SSHClientConnectionOptions(config=[str(config)], host=HOST, port=22, **options)


def test_the_typed_username_wins_over_a_user_line_in_ssh_config(tmp_path: Path) -> None:
    options = _options(tmp_path, _profile())
    assert options.username == "typed"


def test_without_a_configured_key_ssh_config_identities_are_offered(tmp_path: Path) -> None:
    options = _options(tmp_path, _profile())
    assert options.client_keys is not None
    assert len(options.client_keys) == 1


def test_a_configured_key_is_the_only_key_offered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "agent.sock"))
    key = _key(tmp_path / "id_ed25519")
    profile = _profile(ssh_key=str(key))
    options = _options(tmp_path, profile)
    assert options.client_keys is not None
    configured = asyncssh.read_private_key(key).public_data
    assert [pair.public_data for pair in options.client_keys] == [configured]
    # No agent key passes the identity filter (AsyncSSH skips an agent key whose
    # blob is not in a non-empty filter), whatever agent the platform finds.
    assert options.agent_identities
    assert configured not in options.agent_identities
    assert not options.agent_path
    assert options.pkcs11_provider is None
    # Without the key, the same environment would have used the agent unfiltered.
    unconfigured = _options(tmp_path, _profile())
    assert unconfigured.agent_path == str(tmp_path / "agent.sock")
    assert unconfigured.agent_identities is None


def test_without_keys_no_public_key_is_offered(tmp_path: Path) -> None:
    options = asyncssh.SSHClientConnectionOptions(
        config=[], host=HOST, port=22, **login_options(_profile(), keys=False)
    )
    assert options.client_keys is None
    assert options.agent_path is None


def test_a_missing_ssh_key_file_stops_before_connecting(tmp_path: Path) -> None:
    profile = _profile(ssh_key=str(tmp_path / "missing_key"))
    with pytest.raises(ProfileError, match="SSH key file does not exist"):
        validate_profile(ProfileStore([profile], tmp_path / "profiles.yaml"), "cluster")


def _status(tmp_path: Path) -> HostKeyStatus:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("", encoding="utf-8")
    return HostKeyStatus(
        known=True,
        fingerprint=None,
        public_line=None,
        custom_known_hosts=known_hosts,
        known_hosts_files=(known_hosts,),
    )


async def _no_prompts(prompts: list[Prompt]) -> list[str]:
    del prompts
    return []


@pytest.mark.asyncio
async def test_too_many_authentication_failures_retries_without_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    connected = object()

    async def fake_connect(*args: Any, **kwargs: Any) -> Any:
        del args
        calls.append(kwargs)
        if len(calls) == 1:
            raise asyncssh.ProtocolError("Too many authentication failures")
        return connected

    monkeypatch.setattr(ssh_backend.asyncssh, "connect", fake_connect)
    assert await connect_ssh(_profile(), _status(tmp_path), _no_prompts) is connected
    assert len(calls) == 2
    assert "client_keys" not in calls[0]
    assert calls[1]["client_keys"] is None
    assert calls[1]["username"] == "typed"


@pytest.mark.asyncio
async def test_other_failures_and_configured_keys_are_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def failing(reason: str):  # type: ignore[no-untyped-def]
        async def fake_connect(*args: Any, **kwargs: Any) -> Any:
            del args
            calls.append(kwargs)
            raise asyncssh.ProtocolError(reason)

        return fake_connect

    monkeypatch.setattr(ssh_backend.asyncssh, "connect", failing("Connection lost"))
    with pytest.raises(SSHBackendError, match="Connection lost"):
        await connect_ssh(_profile(), _status(tmp_path), _no_prompts)
    assert len(calls) == 1

    calls.clear()
    key = _key(tmp_path / "id_ed25519")
    monkeypatch.setattr(
        ssh_backend.asyncssh, "connect", failing("Too many authentication failures")
    )
    with pytest.raises(SSHBackendError, match="Too many authentication failures"):
        await connect_ssh(_profile(ssh_key=str(key)), _status(tmp_path), _no_prompts)
    assert len(calls) == 1
