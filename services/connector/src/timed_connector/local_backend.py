"""Local data-server subprocess lifecycle for connector local mode."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .dataserver_connection import (
    DataServerConnection,
    DataServerConnectionError,
    parse_connection,
    verify_handshake,
)
from .models import Profile


class LocalBackendError(RuntimeError):
    """Raised when the local data-server process cannot be used."""


@dataclass
class LocalContext:
    process: asyncio.subprocess.Process
    runtime_dir: Path
    connection_file: Path
    output_task: asyncio.Task[None]


class LocalBackend:
    """Start and supervise an authenticated data server on loopback."""

    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        runtime_parent: Path | None = None,
    ) -> None:
        self.state_dir = (state_dir or Path.home() / ".timed" / "local").expanduser()
        self.runtime_parent = runtime_parent

    @staticmethod
    def _data_roots(profile: Profile) -> list[Path]:
        roots: list[Path] = []
        for value in profile.data_roots:
            root = Path(value).expanduser()
            if not root.is_absolute():
                root = Path.cwd() / root
            resolved = root.resolve()
            if not resolved.is_dir():
                raise LocalBackendError(f"Local data root does not exist: {resolved}")
            roots.append(resolved)
        return roots

    @staticmethod
    def _metadata_root(profile: Profile) -> Path | None:
        if profile.metadata_root is None:
            return None
        root = Path(profile.metadata_root).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        resolved = root.resolve()
        if not resolved.is_dir():
            raise LocalBackendError(f"Local metadata root does not exist: {resolved}")
        return resolved

    def _runtime_dir(self) -> Path:
        if self.runtime_parent is None:
            return Path(tempfile.mkdtemp(prefix="timed-local-"))
        parent = self.runtime_parent.expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        runtime = parent / uuid.uuid4().hex
        runtime.mkdir(mode=0o700)
        return runtime

    @staticmethod
    async def _stream_output(
        process: asyncio.subprocess.Process,
        log: Callable[[str], None],
    ) -> None:
        if process.stdout is None:
            return
        while line := await process.stdout.readline():
            log(line.decode("utf-8", errors="replace"))

    async def start(
        self,
        profile: Profile,
        log: Callable[[str], None],
    ) -> LocalContext:
        roots = self._data_roots(profile)
        metadata_root = self._metadata_root(profile)
        runtime_dir = self._runtime_dir()
        connection_file = runtime_dir / "connection.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        arguments = [
            sys.executable,
            "-m",
            "timed_dataserver.cli",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--environment",
            "local",
            "--connection-file",
            str(connection_file),
            "--state-dir",
            str(self.state_dir),
        ]
        for root in roots:
            arguments.extend(("--data-root", str(root)))
        if metadata_root is not None:
            arguments.extend(("--metadata-root", str(metadata_root)))
        for collection in profile.collections:
            if collection.masks_root is not None:
                arguments.extend(("--timed-masks", f"{collection.path}={collection.masks_root}"))
            if collection.segmentation_root is not None:
                arguments.extend(
                    (
                        "--timed-segmentation",
                        f"{collection.path}={collection.segmentation_root}",
                    )
                )
            if collection.qc_export is None or collection.qc_annotations is None:
                continue
            arguments.extend(("--qc-export", f"{collection.path}={collection.qc_export}"))
            arguments.extend(
                (
                    "--qc-annotations",
                    f"{collection.path}={collection.qc_annotations}",
                )
            )
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except BaseException:
            shutil.rmtree(runtime_dir, ignore_errors=True)
            raise
        output_task = asyncio.create_task(self._stream_output(process, log))
        return LocalContext(process, runtime_dir, connection_file, output_task)

    async def wait_for_connection(
        self,
        context: LocalContext,
        *,
        timeout: float,
        poll_interval: float,
    ) -> DataServerConnection:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if context.connection_file.is_file():
                try:
                    value = json.loads(
                        await asyncio.to_thread(
                            context.connection_file.read_text,
                            encoding="utf-8",
                        )
                    )
                    if not isinstance(value, dict):
                        raise LocalBackendError("Local connection file was not a JSON object")
                    connection = parse_connection(
                        value,
                        source="Local connection file",
                    )
                except (OSError, json.JSONDecodeError, DataServerConnectionError) as exc:
                    raise LocalBackendError(str(exc)) from exc
                if connection.host != "127.0.0.1":
                    raise LocalBackendError(
                        "Local data server did not advertise a loopback address"
                    )
                return connection
            if context.process.returncode is not None:
                await context.output_task
                raise LocalBackendError(
                    f"Local data server exited with code {context.process.returncode}"
                )
            await asyncio.sleep(poll_interval)
        raise LocalBackendError("Timed out waiting for the local data server")

    async def handshake(
        self,
        context: LocalContext,
        connection: DataServerConnection,
    ) -> dict[str, object]:
        del context
        try:
            value = await verify_handshake(
                f"http://127.0.0.1:{connection.port}",
                connection,
            )
        except DataServerConnectionError as exc:
            raise LocalBackendError(str(exc)) from exc
        if value.get("environment") != "local":
            raise LocalBackendError("Local data server reported the wrong environment")
        return value

    @staticmethod
    def returncode(context: LocalContext) -> int | None:
        return context.process.returncode

    @staticmethod
    def proxy_target(
        context: LocalContext,
        connection: DataServerConnection,
    ) -> tuple[str, str]:
        del context
        return f"http://127.0.0.1:{connection.port}", connection.token

    @staticmethod
    async def close(context: LocalContext | None) -> None:
        if context is None:
            return
        process = context.process
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=8)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        await context.output_task
        context.connection_file.unlink(missing_ok=True)
        shutil.rmtree(context.runtime_dir, ignore_errors=True)
