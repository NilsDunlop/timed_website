"""High-level HPC operations implemented with AsyncSSH and Slurm commands."""

from __future__ import annotations

import shlex
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import asyncssh

from .dataserver_connection import (
    DataServerConnection,
    DataServerConnectionError,
    parse_connection,
    verify_handshake,
)
from .models import ExistingJob, Profile
from .remote_install import prepare_remote_server
from .slurm import (
    QueueStatus,
    parse_existing_jobs,
    parse_job_id,
    parse_queue_status,
    render_job_script,
)
from .ssh_backend import (
    HostKeyStatus,
    PromptCallback,
    RemotePaths,
    SSHBackendError,
    accept_host_key,
    close_ssh,
    connect_ssh,
    inspect_host_key,
    read_sftp_json,
    remove_sftp_file,
    resolve_remote_paths,
    run_remote,
    sftp_exists,
)


@dataclass
class SSHContext:
    connection: asyncssh.SSHClientConnection
    sftp: asyncssh.SFTPClient
    paths: RemotePaths
    executable: str | None = None


@dataclass
class Forward:
    listener: asyncssh.SSHListener
    port: int


def _join(base: str, *parts: str) -> str:
    value = PurePosixPath(base)
    for part in parts:
        value /= part
    return str(value)


class AsyncSSHHPCBackend:
    """Concrete session backend for a real or test SSH/Slurm cluster."""

    def __init__(self, profile_source: Path) -> None:
        self.profile_source = profile_source

    async def inspect_host_key(self, profile: Profile) -> HostKeyStatus:
        return await inspect_host_key(profile, self.profile_source)

    async def accept_host_key(self, status: HostKeyStatus) -> HostKeyStatus:
        return accept_host_key(status)

    async def connect(
        self,
        profile: Profile,
        status: HostKeyStatus,
        prompt_callback: PromptCallback,
    ) -> SSHContext:
        connection = await connect_ssh(profile, status, prompt_callback)
        try:
            sftp = await connection.start_sftp_client()
            paths = await resolve_remote_paths(
                connection,
                sftp,
                profile.remote_state_dir,
            )
        except BaseException:
            await close_ssh(connection)
            raise
        return SSHContext(connection, sftp, paths)

    async def prepare(
        self,
        context: SSHContext,
        profile: Profile,
        log: Callable[[str], None],
    ) -> None:
        context.executable = await prepare_remote_server(
            context.connection,
            context.sftp,
            profile,
            context.paths,
            log,
        )

    async def list_jobs(self, context: SSHContext) -> list[ExistingJob]:
        result = await run_remote(
            context.connection,
            'squeue -u "$USER" -n timed-dataserver -h -o "%i %T %N"',
        )
        return parse_existing_jobs(str(result.stdout))

    async def cancel_job(self, context: SSHContext, job_id: str) -> None:
        if not job_id.isdigit():
            raise SSHBackendError("Invalid Slurm job id")
        await run_remote(context.connection, f"scancel {job_id}")

    async def submit_job(self, context: SSHContext, profile: Profile) -> str:
        if context.executable is None:
            raise SSHBackendError("Remote data server has not been prepared")
        for directory in ("jobs", "logs", "sessions"):
            await context.sftp.makedirs(
                _join(context.paths.sftp_state_dir, directory),
                exist_ok=True,
            )
        script = render_job_script(
            profile,
            executable=context.executable,
            shell_state_dir=context.paths.shell_state_dir,
        )
        basename = f"submit-{uuid.uuid4().hex}.sh"
        sftp_path = _join(context.paths.sftp_state_dir, "jobs", basename)
        shell_path = _join(context.paths.shell_state_dir, "jobs", basename)
        async with context.sftp.open(sftp_path, "w") as handle:
            await handle.write(script)
        result = await run_remote(
            context.connection,
            f"sbatch --parsable {shlex.quote(shell_path)}",
        )
        return parse_job_id(str(result.stdout))

    async def queue_status(self, context: SSHContext, job_id: str) -> QueueStatus | None:
        if not job_id.isdigit():
            raise SSHBackendError("Invalid Slurm job id")
        result = await run_remote(
            context.connection,
            f'squeue -j {job_id} -h -o "%T %r %N"',
        )
        return parse_queue_status(str(result.stdout))

    def connection_file(self, context: SSHContext, job_id: str) -> str:
        return _join(context.paths.sftp_state_dir, "sessions", f"{job_id}.json")

    async def connection_file_exists(self, context: SSHContext, job_id: str) -> bool:
        return await sftp_exists(context.sftp, self.connection_file(context, job_id))

    async def read_connection(
        self, context: SSHContext, job_id: str
    ) -> DataServerConnection:
        value = await read_sftp_json(context.sftp, self.connection_file(context, job_id))
        try:
            return parse_connection(value, source="Remote connection file")
        except DataServerConnectionError as exc:
            raise SSHBackendError(str(exc)) from exc

    async def forward(
        self,
        context: SSHContext,
        connection: DataServerConnection,
    ) -> Forward:
        listener = await context.connection.forward_local_port(
            "127.0.0.1",
            0,
            connection.host,
            connection.port,
        )
        return Forward(listener=listener, port=listener.get_port())

    async def handshake(
        self,
        forward: Forward,
        connection: DataServerConnection,
    ) -> dict[str, Any]:
        try:
            return await verify_handshake(
                f"http://127.0.0.1:{forward.port}",
                connection,
            )
        except DataServerConnectionError as exc:
            raise SSHBackendError(str(exc)) from exc

    async def log_tail(self, context: SSHContext, job_id: str) -> list[str]:
        path = _join(context.paths.sftp_state_dir, "logs", f"{job_id}.out")
        if not await sftp_exists(context.sftp, path):
            return []
        try:
            attributes = await context.sftp.stat(path)
            async with context.sftp.open(path, "r") as handle:
                if attributes.size and attributes.size > 64 * 1024:
                    await handle.seek(attributes.size - 64 * 1024)
                contents = str(await handle.read())
        except asyncssh.SFTPError:
            return []
        return contents.splitlines()[-40:]

    async def remove_connection_file(self, context: SSHContext, job_id: str) -> None:
        await remove_sftp_file(context.sftp, self.connection_file(context, job_id))

    async def close_forward(self, forward: Forward | None) -> None:
        if forward is None:
            return
        forward.listener.close()
        await forward.listener.wait_closed()

    async def close(self, context: SSHContext | None) -> None:
        if context is None:
            return
        context.sftp.exit()
        await close_ssh(context.connection)

    async def wait_closed(self, context: SSHContext) -> None:
        await context.connection.wait_closed()

    def proxy_target(
        self,
        forward: Forward,
        connection: DataServerConnection,
    ) -> tuple[str, str]:
        return f"http://127.0.0.1:{forward.port}", connection.token
