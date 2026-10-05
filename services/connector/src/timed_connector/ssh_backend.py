"""Secure AsyncSSH transport helpers used by the session engine."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import asyncssh

from .models import Profile, Prompt

PromptCallback = Callable[[list[Prompt]], Awaitable[list[str]]]


class SSHBackendError(RuntimeError):
    """Raised for sanitized SSH and remote-command failures."""


@dataclass(frozen=True)
class HostKeyStatus:
    known: bool
    fingerprint: str | None
    public_line: str | None
    custom_known_hosts: Path
    known_hosts_files: tuple[Path, ...]


@dataclass(frozen=True)
class RemotePaths:
    shell_state_dir: str
    sftp_state_dir: str


class PromptingSSHClient(asyncssh.SSHClient):
    def __init__(self, prompt_callback: PromptCallback) -> None:
        self._prompt_callback = prompt_callback

    def kbdint_auth_requested(self) -> str:
        return ""

    async def kbdint_challenge_received(
        self,
        name: str,
        instructions: str,
        lang: str,
        prompts: asyncssh.KbdIntPrompts,
    ) -> list[str] | None:
        del name, instructions, lang
        return await self._prompt_callback(
            [Prompt(prompt=str(prompt), echo=bool(echo)) for prompt, echo in prompts]
        )

    async def password_auth_requested(self) -> str | None:
        answers = await self._prompt_callback([Prompt(prompt="Password:", echo=False)])
        return answers[0] if answers else None


def _custom_known_hosts(profile: Profile, profile_source: Path) -> Path:
    configured = profile.known_hosts
    if configured is None:
        return Path.home() / ".timed" / "known_hosts"
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _known_hosts_files(custom: Path) -> tuple[Path, ...]:
    user_file = Path.home() / ".ssh" / "known_hosts"
    return tuple(path for path in (user_file, custom) if path.is_file())


async def inspect_host_key(profile: Profile, profile_source: Path) -> HostKeyStatus:
    """Return known status or a first-use key offer without disabling checks."""
    if profile.host is None:
        raise SSHBackendError("Slurm profile has no host")
    custom = _custom_known_hosts(profile, profile_source)
    files = _known_hosts_files(custom)
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(
        profile.host,
        profile.port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    address = str(addresses[0][4][0])
    if files:
        known_hosts = asyncssh.read_known_hosts([str(path) for path in files])
        matches = asyncssh.match_known_hosts(
            known_hosts,
            profile.host,
            address,
            profile.port,
        )
        if matches[2]:
            raise SSHBackendError("The server host key is explicitly revoked")
        if matches[0] or matches[1]:
            return HostKeyStatus(
                known=True,
                fingerprint=None,
                public_line=None,
                custom_known_hosts=custom,
                known_hosts_files=files,
            )

    key = await asyncssh.get_server_host_key(profile.host, profile.port)
    if key is None:
        raise SSHBackendError("The SSH server did not provide a host key")
    host_pattern = profile.host if profile.port == 22 else f"[{profile.host}]:{profile.port}"
    exported = key.export_public_key("openssh").decode("ascii").strip()
    return HostKeyStatus(
        known=False,
        fingerprint=key.get_fingerprint("sha256"),
        public_line=f"{host_pattern} {exported}",
        custom_known_hosts=custom,
        known_hosts_files=files,
    )


def accept_host_key(status: HostKeyStatus) -> HostKeyStatus:
    """Append an explicitly accepted key to TIMED's own known-hosts file."""
    if status.public_line is None:
        return status
    path = status.custom_known_hosts
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, (status.public_line + "\n").encode())
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)
    return HostKeyStatus(
        known=True,
        fingerprint=None,
        public_line=None,
        custom_known_hosts=path,
        known_hosts_files=_known_hosts_files(path),
    )


async def _key_passphrase(filename: str | bytes, prompt_callback: PromptCallback) -> str | None:
    path = Path(os.fsdecode(filename))
    try:
        asyncssh.read_private_key(path)
    except asyncssh.KeyEncryptionError:
        answers = await prompt_callback(
            [Prompt(prompt=f"Passphrase for SSH key {path.name}:", echo=False)]
        )
        return answers[0] if answers else None
    except (OSError, asyncssh.KeyImportError):
        return None
    return None


async def connect_ssh(
    profile: Profile,
    status: HostKeyStatus,
    prompt_callback: PromptCallback,
) -> asyncssh.SSHClientConnection:
    """Connect with verified host keys and relayed authentication prompts."""
    if profile.host is None or profile.username is None:
        raise SSHBackendError("Slurm profile has incomplete SSH settings")
    if not status.known or not status.known_hosts_files:
        raise SSHBackendError("Host key must be trusted before connecting")
    client = PromptingSSHClient(prompt_callback)

    async def passphrase(filename: str | bytes) -> str | None:
        return await _key_passphrase(filename, prompt_callback)

    try:
        return await asyncssh.connect(
            profile.host,
            profile.port,
            username=profile.username,
            known_hosts=[str(path) for path in status.known_hosts_files],
            client_factory=lambda: client,
            passphrase=passphrase,
        )
    except (OSError, asyncssh.Error) as exc:
        raise SSHBackendError(f"SSH connection failed: {exc}") from exc


async def run_remote(
    connection: asyncssh.SSHClientConnection,
    command: str,
    *,
    timeout: float = 120,
    check: bool = True,
) -> asyncssh.SSHCompletedProcess:
    """Run a non-interactive command and surface bounded, sanitized output."""
    try:
        result = await connection.run(command, check=False, timeout=timeout)
    except TimeoutError as exc:
        raise SSHBackendError(f"Remote command timed out after {timeout:g} seconds") from exc
    except (OSError, asyncssh.Error) as exc:
        raise SSHBackendError(f"Remote command failed: {exc}") from exc
    if check and result.exit_status != 0:
        stderr = str(result.stderr).strip()[-1000:]
        raise SSHBackendError(
            f"Remote command exited with {result.exit_status}: {stderr or 'no error output'}"
        )
    return result


def _join_posix(base: str, relative: str) -> str:
    return str(PurePosixPath(base) / PurePosixPath(relative))


async def resolve_remote_paths(
    connection: asyncssh.SSHClientConnection,
    sftp: asyncssh.SFTPClient,
    configured: str,
) -> RemotePaths:
    """Resolve one configured state dir in shell and SFTP namespaces."""
    shell_home = str((await run_remote(connection, 'printf "%s" "$HOME"')).stdout)
    sftp_home = str(await sftp.realpath("."))
    if configured == "~":
        return RemotePaths(shell_home, sftp_home)
    if configured.startswith("~/"):
        suffix = configured[2:]
        return RemotePaths(_join_posix(shell_home, suffix), _join_posix(sftp_home, suffix))
    if configured.startswith("/"):
        return RemotePaths(configured, configured)
    return RemotePaths(
        _join_posix(shell_home, configured),
        _join_posix(sftp_home, configured),
    )


async def sftp_exists(sftp: asyncssh.SFTPClient, path: str) -> bool:
    try:
        await sftp.stat(path)
    except asyncssh.SFTPNoSuchFile:
        return False
    return True


async def read_sftp_json(sftp: asyncssh.SFTPClient, path: str) -> dict[str, Any]:
    try:
        async with sftp.open(path, "r") as handle:
            contents = await handle.read()
        value = json.loads(str(contents))
    except (OSError, asyncssh.SFTPError, json.JSONDecodeError) as exc:
        raise SSHBackendError(f"Could not read remote JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SSHBackendError(f"Remote JSON file is not an object: {path}")
    return value


async def remove_sftp_file(sftp: asyncssh.SFTPClient, path: str) -> None:
    try:
        await sftp.remove(path)
    except asyncssh.SFTPNoSuchFile:
        return
    except asyncssh.SFTPError as exc:
        raise SSHBackendError(f"Could not remove remote file {path}: {exc}") from exc


async def close_ssh(connection: asyncssh.SSHClientConnection | None) -> None:
    if connection is None:
        return
    connection.close()
    await connection.wait_closed()
