"""Build, upload, and version the remote TIMED data-server environment."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import asyncssh
from timed_dataserver import __version__ as dataserver_version

from .models import Profile
from .ssh_backend import RemotePaths, SSHBackendError, run_remote, sftp_exists

LogCallback = Callable[[str], None]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _join(base: str, *parts: str) -> str:
    value = PurePosixPath(base)
    for part in parts:
        value /= part
    return str(value)


async def _build_wheel(log: LogCallback) -> Path:
    output = REPOSITORY_ROOT / ".timed" / "connector-build"
    output.mkdir(parents=True, exist_ok=True)
    log(f"Building timed-dataserver {dataserver_version} wheel")
    process = await asyncio.create_subprocess_exec(
        "uv",
        "build",
        "--package",
        "timed-dataserver",
        "--out-dir",
        str(output),
        cwd=REPOSITORY_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()[-1000:]
        raise SSHBackendError(f"Could not build data-server wheel: {detail}")
    wheels = sorted(output.glob(f"timed_dataserver-{dataserver_version}-*.whl"))
    if not wheels:
        raise SSHBackendError("uv build completed without producing a data-server wheel")
    return wheels[-1]


def _module_prefix(profile: Profile) -> str:
    commands = [f"module load {shlex.quote(module)}" for module in profile.module_loads]
    return " && ".join(commands)


async def prepare_remote_server(
    connection: asyncssh.SSHClientConnection,
    sftp: asyncssh.SFTPClient,
    profile: Profile,
    paths: RemotePaths,
    log: LogCallback,
) -> str:
    """Return the installed remote executable, installing it once per version."""
    shell_version_dir = _join(paths.shell_state_dir, "server", dataserver_version)
    sftp_version_dir = _join(paths.sftp_state_dir, "server", dataserver_version)
    marker = _join(sftp_version_dir, "installed.ok")
    executable = _join(shell_version_dir, "venv", "bin", "timed-dataserver")
    if await sftp_exists(sftp, marker):
        log(f"Remote data-server {dataserver_version} is already installed")
        return executable

    wheel = await _build_wheel(log)
    await sftp.makedirs(sftp_version_dir, exist_ok=True)
    remote_wheel_sftp = _join(sftp_version_dir, wheel.name)
    remote_wheel_shell = _join(shell_version_dir, wheel.name)
    log("Uploading data-server wheel")
    await sftp.put(wheel, remote_wheel_sftp)

    module_prefix = _module_prefix(profile)
    find_uv = f"{module_prefix} && command -v uv" if module_prefix else "command -v uv"
    result = await run_remote(connection, find_uv, check=False)
    uv_path = str(result.stdout).strip() if result.exit_status == 0 else ""
    if not uv_path:
        if not profile.auto_install_uv:
            raise SSHBackendError("uv is not available remotely and auto_install_uv is disabled")
        log("Installing uv remotely with Astral's official installer")
        await run_remote(connection, "curl -LsSf https://astral.sh/uv/install.sh | sh")
        home_result = await run_remote(connection, 'printf "%s" "$HOME"')
        uv_path = _join(str(home_result.stdout), ".local", "bin", "uv")

    venv = _join(shell_version_dir, "venv")
    steps = [
        f"mkdir -p {shlex.quote(shell_version_dir)}",
        f"{shlex.quote(uv_path)} venv --python 3.12 {shlex.quote(venv)}",
        (
            f"{shlex.quote(uv_path)} pip install --python "
            f"{shlex.quote(_join(venv, 'bin', 'python'))} "
            f"{shlex.quote(remote_wheel_shell)}"
        ),
    ]
    if module_prefix:
        steps.insert(0, module_prefix)
    log("Creating remote Python environment")
    await run_remote(connection, " && ".join(steps), timeout=600)
    async with sftp.open(marker, "w") as handle:
        await handle.write(f"{dataserver_version}\n")
    log(f"Installed remote data-server {dataserver_version}")
    return executable
