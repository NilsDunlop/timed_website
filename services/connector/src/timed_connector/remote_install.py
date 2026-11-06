"""Build, upload, and version the remote TIMED data-server environment."""

from __future__ import annotations

import asyncio
import hashlib
import shlex
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import asyncssh
from timed_dataserver import __version__ as dataserver_version

from .models import Profile
from .ssh_backend import RemotePaths, SSHBackendError, run_remote, sftp_exists

LogCallback = Callable[[str], None]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
BUILD_DIRECTORY = REPOSITORY_ROOT / ".timed" / "connector-build"


def _join(base: str, *parts: str) -> str:
    value = PurePosixPath(base)
    for part in parts:
        value /= part
    return str(value)


def _install_digest(wheel: Path, constraints: Path) -> str:
    """Identify a development install by its wheel and its locked dependencies."""
    digest = hashlib.sha256()
    for path in (wheel, constraints):
        with path.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").digest())
    return digest.hexdigest()[:12]


async def _export_constraints(log: LogCallback, output: Path = BUILD_DIRECTORY) -> Path:
    """Write the data server's runtime dependencies from uv.lock as pip constraints."""
    output.mkdir(parents=True, exist_ok=True)
    constraints = output / f"timed_dataserver-{dataserver_version}-constraints.txt"
    log("Exporting the data server's locked dependencies")
    process = await asyncio.create_subprocess_exec(
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--package",
        "timed-dataserver",
        "--no-emit-workspace",
        "--no-hashes",
        "--no-header",
        "--no-annotate",
        "--format",
        "requirements.txt",
        "--output-file",
        str(constraints),
        cwd=REPOSITORY_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()[-1000:]
        raise SSHBackendError(f"Could not export the data server's locked dependencies: {detail}")
    return constraints


async def _build_wheel(log: LogCallback) -> Path:
    output = BUILD_DIRECTORY
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
    """Return the remote executable for this release or development wheel.

    The remote environment installs the wheel constrained to the versions in
    uv.lock, the ones the local suite ran against.
    """
    wheel: Path | None = None
    constraints: Path | None = None
    install_key = dataserver_version
    if ".dev" in dataserver_version:
        wheel = await _build_wheel(log)
        constraints = await _export_constraints(log)
        install_key = f"{dataserver_version}-{_install_digest(wheel, constraints)}"

    shell_version_dir = _join(paths.shell_state_dir, "server", install_key)
    sftp_version_dir = _join(paths.sftp_state_dir, "server", install_key)
    marker = _join(sftp_version_dir, "installed.ok")
    executable = _join(shell_version_dir, "venv", "bin", "timed-dataserver")
    if await sftp_exists(sftp, marker):
        log(f"Remote data-server {install_key} is already installed")
        return executable

    if wheel is None:
        wheel = await _build_wheel(log)
    if constraints is None:
        constraints = await _export_constraints(log)
    await sftp.makedirs(sftp_version_dir, exist_ok=True)
    remote_wheel_sftp = _join(sftp_version_dir, wheel.name)
    remote_wheel_shell = _join(shell_version_dir, wheel.name)
    remote_constraints_shell = _join(shell_version_dir, constraints.name)
    log("Uploading data-server wheel")
    await sftp.put(wheel, remote_wheel_sftp)
    await sftp.put(constraints, _join(sftp_version_dir, constraints.name))

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
            f"-c {shlex.quote(remote_constraints_shell)} "
            f"{shlex.quote(remote_wheel_shell)}"
        ),
    ]
    if module_prefix:
        steps.insert(0, module_prefix)
    log("Creating remote Python environment")
    await run_remote(connection, " && ".join(steps), timeout=600)
    async with sftp.open(marker, "w") as handle:
        await handle.write(f"{install_key}\n")
    log(f"Installed remote data-server {install_key}")
    return executable
