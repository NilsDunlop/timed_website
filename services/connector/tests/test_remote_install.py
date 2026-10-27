from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from timed_connector import remote_install
from timed_connector.ssh_backend import RemotePaths


@pytest.mark.asyncio
async def test_development_install_is_keyed_by_wheel_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "timed_dataserver-0.4.0.dev0-py3-none-any.whl"
    wheel.write_bytes(b"development wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()[:12]
    events: list[str] = []

    async def build_wheel(log: Any) -> Path:
        del log
        events.append("build")
        return wheel

    async def marker_exists(sftp: Any, path: str) -> bool:
        del sftp
        events.append(f"marker:{path}")
        return True

    monkeypatch.setattr(remote_install, "dataserver_version", "0.4.0.dev0")
    monkeypatch.setattr(remote_install, "_build_wheel", build_wheel)
    monkeypatch.setattr(remote_install, "sftp_exists", marker_exists)

    logs: list[str] = []
    executable = await remote_install.prepare_remote_server(
        object(),
        object(),
        object(),
        RemotePaths("/shell/.timed", "/sftp/.timed"),
        logs.append,
    )

    install_key = f"0.4.0.dev0-{digest}"
    assert events == [
        "build",
        f"marker:/sftp/.timed/server/{install_key}/installed.ok",
    ]
    assert executable == (f"/shell/.timed/server/{install_key}/venv/bin/timed-dataserver")
    assert logs == [f"Remote data-server {install_key} is already installed"]


@pytest.mark.asyncio
async def test_development_build_changes_use_distinct_install_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheels: list[Path] = []
    for index, contents in enumerate((b"first wheel", b"second wheel")):
        wheel_dir = tmp_path / str(index)
        wheel_dir.mkdir()
        wheel = wheel_dir / "timed_dataserver-0.4.0.dev0-py3-none-any.whl"
        wheel.write_bytes(contents)
        wheels.append(wheel)
    wheel_iterator = iter(wheels)

    async def build_wheel(log: Any) -> Path:
        del log
        return next(wheel_iterator)

    async def marker_exists(sftp: Any, path: str) -> bool:
        del sftp, path
        return True

    monkeypatch.setattr(remote_install, "dataserver_version", "0.4.0.dev0")
    monkeypatch.setattr(remote_install, "_build_wheel", build_wheel)
    monkeypatch.setattr(remote_install, "sftp_exists", marker_exists)
    paths = RemotePaths("/shell/.timed", "/sftp/.timed")

    first = await remote_install.prepare_remote_server(
        object(),
        object(),
        object(),
        paths,
        lambda _line: None,
    )
    second = await remote_install.prepare_remote_server(
        object(),
        object(),
        object(),
        paths,
        lambda _line: None,
    )

    expected = [hashlib.sha256(wheel.read_bytes()).hexdigest()[:12] for wheel in wheels]
    assert first == (f"/shell/.timed/server/0.4.0.dev0-{expected[0]}/venv/bin/timed-dataserver")
    assert second == (f"/shell/.timed/server/0.4.0.dev0-{expected[1]}/venv/bin/timed-dataserver")
    assert first != second


@pytest.mark.asyncio
async def test_installed_release_is_reused_without_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def unexpected_build(log: Any) -> Path:
        del log
        raise AssertionError("installed releases must not be rebuilt")

    async def marker_exists(sftp: Any, path: str) -> bool:
        del sftp
        events.append(path)
        return True

    monkeypatch.setattr(remote_install, "dataserver_version", "0.4.0")
    monkeypatch.setattr(remote_install, "_build_wheel", unexpected_build)
    monkeypatch.setattr(remote_install, "sftp_exists", marker_exists)

    logs: list[str] = []
    executable = await remote_install.prepare_remote_server(
        object(),
        object(),
        object(),
        RemotePaths("/shell/.timed", "/sftp/.timed"),
        logs.append,
    )

    assert events == ["/sftp/.timed/server/0.4.0/installed.ok"]
    assert executable == "/shell/.timed/server/0.4.0/venv/bin/timed-dataserver"
    assert logs == ["Remote data-server 0.4.0 is already installed"]
