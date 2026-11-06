from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from timed_connector import remote_install
from timed_connector.ssh_backend import RemotePaths

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PATHS = RemotePaths("/shell/.timed", "/sftp/.timed")


def _install_digest(wheel: Path, constraints: Path) -> str:
    digest = hashlib.sha256()
    for path in (wheel, constraints):
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:12]


def _artifact(directory: Path, name: str, contents: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(contents)
    return path


class FakeSftp:
    """Records uploads and the install marker of one remote installation."""

    def __init__(self) -> None:
        self.directories: list[str] = []
        self.uploads: list[tuple[Path, str]] = []
        self.markers: dict[str, str] = {}

    async def makedirs(self, path: str, *, exist_ok: bool = False) -> None:
        assert exist_ok
        self.directories.append(path)

    async def put(self, local: Path, remote: str) -> None:
        self.uploads.append((local, remote))

    def open(self, path: str, mode: str) -> FakeSftpFile:
        assert mode == "w"
        return FakeSftpFile(self.markers, path)


class FakeSftpFile:
    def __init__(self, markers: dict[str, str], path: str) -> None:
        self._markers = markers
        self._path = path

    async def __aenter__(self) -> FakeSftpFile:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def write(self, text: str) -> None:
        self._markers[self._path] = self._markers.get(self._path, "") + text


@pytest.mark.asyncio
async def test_release_install_is_constrained_to_the_locked_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = _artifact(tmp_path, "timed_dataserver-0.4.0-py3-none-any.whl", b"wheel")
    constraints = _artifact(tmp_path, "timed_dataserver-0.4.0-constraints.txt", b"anyio==1\n")
    commands: list[str] = []

    async def build_wheel(log: Any) -> Path:
        del log
        return wheel

    async def export_constraints(log: Any) -> Path:
        del log
        return constraints

    async def marker_exists(sftp: Any, path: str) -> bool:
        del sftp, path
        return False

    async def run_remote(connection: Any, command: str, **options: Any) -> Any:
        del connection, options
        commands.append(command)
        return SimpleNamespace(exit_status=0, stdout="/opt/uv/bin/uv\n")

    monkeypatch.setattr(remote_install, "dataserver_version", "0.4.0")
    monkeypatch.setattr(remote_install, "_build_wheel", build_wheel)
    monkeypatch.setattr(remote_install, "_export_constraints", export_constraints)
    monkeypatch.setattr(remote_install, "sftp_exists", marker_exists)
    monkeypatch.setattr(remote_install, "run_remote", run_remote)
    profile = SimpleNamespace(module_loads=[], auto_install_uv=True)
    sftp = FakeSftp()

    logs: list[str] = []
    executable = await remote_install.prepare_remote_server(
        object(),
        sftp,
        profile,
        PATHS,
        logs.append,
    )

    version_dir = "/shell/.timed/server/0.4.0"
    assert executable == f"{version_dir}/venv/bin/timed-dataserver"
    assert sftp.uploads == [
        (wheel, f"/sftp/.timed/server/0.4.0/{wheel.name}"),
        (constraints, f"/sftp/.timed/server/0.4.0/{constraints.name}"),
    ]
    assert commands == [
        "command -v uv",
        f"mkdir -p {version_dir}"
        f" && /opt/uv/bin/uv venv --python 3.12 {version_dir}/venv"
        f" && /opt/uv/bin/uv pip install --python {version_dir}/venv/bin/python"
        f" -c {version_dir}/{constraints.name} {version_dir}/{wheel.name}",
    ]
    assert sftp.markers == {"/sftp/.timed/server/0.4.0/installed.ok": "0.4.0\n"}
    assert logs[-1] == "Installed remote data-server 0.4.0"


@pytest.mark.asyncio
async def test_exported_constraints_pin_the_locked_runtime_dependencies(
    tmp_path: Path,
) -> None:
    logs: list[str] = []
    constraints = await remote_install._export_constraints(logs.append, output=tmp_path)

    lines = constraints.read_text(encoding="utf-8").splitlines()
    # A platform-specific pin carries an environment marker after ";".
    pins = dict(line.split(";", 1)[0].strip().split("==", 1) for line in lines)
    lock = tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {package["name"]: package.get("version") for package in lock["package"]}

    assert constraints.parent == tmp_path
    assert all("==" in line and "--hash" not in line for line in lines)
    assert "timed-dataserver" not in pins
    assert "timed-connector" not in pins
    assert {"anyio", "fastapi", "numcodecs", "numpy", "starlette", "uvicorn"} <= set(pins)
    assert all(pins[name] == locked[name] for name in pins)
    assert logs == ["Exporting the data server's locked dependencies"]


@pytest.mark.asyncio
async def test_development_install_is_keyed_by_wheel_and_locked_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = _artifact(tmp_path, "timed_dataserver-0.4.0.dev0-py3-none-any.whl", b"wheel")
    constraints = _artifact(tmp_path, "constraints.txt", b"anyio==1\n")
    events: list[str] = []

    async def build_wheel(log: Any) -> Path:
        del log
        events.append("build")
        return wheel

    async def export_constraints(log: Any) -> Path:
        del log
        events.append("export")
        return constraints

    async def marker_exists(sftp: Any, path: str) -> bool:
        del sftp
        events.append(f"marker:{path}")
        return True

    monkeypatch.setattr(remote_install, "dataserver_version", "0.4.0.dev0")
    monkeypatch.setattr(remote_install, "_build_wheel", build_wheel)
    monkeypatch.setattr(remote_install, "_export_constraints", export_constraints)
    monkeypatch.setattr(remote_install, "sftp_exists", marker_exists)

    logs: list[str] = []
    executable = await remote_install.prepare_remote_server(
        object(),
        object(),
        object(),
        PATHS,
        logs.append,
    )

    install_key = f"0.4.0.dev0-{_install_digest(wheel, constraints)}"
    assert events == [
        "build",
        "export",
        f"marker:/sftp/.timed/server/{install_key}/installed.ok",
    ]
    assert executable == (f"/shell/.timed/server/{install_key}/venv/bin/timed-dataserver")
    assert logs == [f"Remote data-server {install_key} is already installed"]


@pytest.mark.parametrize("changed", ("wheel", "constraints"))
@pytest.mark.asyncio
async def test_development_changes_use_distinct_install_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    builds = [
        (
            _artifact(tmp_path / str(index), "timed_dataserver-0.4.0.dev0.whl", wheel),
            _artifact(tmp_path / str(index), "constraints.txt", lock),
        )
        for index, (wheel, lock) in enumerate(
            (
                (b"first wheel", b"anyio==1\n"),
                (b"second wheel", b"anyio==1\n")
                if changed == "wheel"
                else (b"first wheel", b"anyio==2\n"),
            )
        )
    ]
    current: list[tuple[Path, Path]] = []

    async def build_wheel(log: Any) -> Path:
        del log
        current.append(builds[len(current)])
        return current[-1][0]

    async def export_constraints(log: Any) -> Path:
        del log
        return current[-1][1]

    async def marker_exists(sftp: Any, path: str) -> bool:
        del sftp, path
        return True

    monkeypatch.setattr(remote_install, "dataserver_version", "0.4.0.dev0")
    monkeypatch.setattr(remote_install, "_build_wheel", build_wheel)
    monkeypatch.setattr(remote_install, "_export_constraints", export_constraints)
    monkeypatch.setattr(remote_install, "sftp_exists", marker_exists)

    executables = [
        await remote_install.prepare_remote_server(
            object(),
            object(),
            object(),
            PATHS,
            lambda _line: None,
        )
        for _ in builds
    ]

    expected = [_install_digest(wheel, lock) for wheel, lock in builds]
    assert executables == [
        f"/shell/.timed/server/0.4.0.dev0-{digest}/venv/bin/timed-dataserver"
        for digest in expected
    ]
    assert executables[0] != executables[1]


@pytest.mark.asyncio
async def test_installed_release_is_reused_without_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def unexpected_build(log: Any) -> Path:
        del log
        raise AssertionError("installed releases must not be rebuilt")

    async def unexpected_export(log: Any) -> Path:
        del log
        raise AssertionError("installed releases must not export their lock again")

    async def marker_exists(sftp: Any, path: str) -> bool:
        del sftp
        events.append(path)
        return True

    monkeypatch.setattr(remote_install, "dataserver_version", "0.4.0")
    monkeypatch.setattr(remote_install, "_build_wheel", unexpected_build)
    monkeypatch.setattr(remote_install, "_export_constraints", unexpected_export)
    monkeypatch.setattr(remote_install, "sftp_exists", marker_exists)

    logs: list[str] = []
    executable = await remote_install.prepare_remote_server(
        object(),
        object(),
        object(),
        PATHS,
        logs.append,
    )

    assert events == ["/sftp/.timed/server/0.4.0/installed.ok"]
    assert executable == "/shell/.timed/server/0.4.0/venv/bin/timed-dataserver"
    assert logs == ["Remote data-server 0.4.0 is already installed"]
