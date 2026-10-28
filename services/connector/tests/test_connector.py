from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from timed_connector.app import create_app
from timed_connector.cli import _first_plate, main
from timed_connector.dataserver_connection import DataServerConnection
from timed_connector.local_backend import LocalBackend
from timed_connector.models import (
    ExistingJob,
    ExistingJobChoice,
    Profile,
    Prompt,
    RespondRequest,
)
from timed_connector.profiles import (
    ProfileError,
    ProfileStore,
    load_profiles,
    validate_profile,
)
from timed_connector.proxy import proxy_request
from timed_connector.session import ConnectorSession, SessionError
from timed_connector.slurm import QueueStatus, parse_job_id, render_job_script
from timed_connector.ssh_backend import HostKeyStatus

CLIENT_HEADER = {"X-Timed-Client": "web"}


def slurm_profile(**changes: Any) -> Profile:
    values: dict[str, Any] = {
        "id": "test",
        "name": "Test cluster",
        "kind": "slurm",
        "host": "127.0.0.1",
        "port": 2222,
        "username": "dev",
        "data_roots": ["/data"],
        "slurm": {
            "account": "dev",
            "partition": "cpu",
            "time": "01:00:00",
            "cpus": 2,
            "mem": "4G",
        },
    }
    values.update(changes)
    return Profile.model_validate(values)


def local_profile(data_root: Path) -> Profile:
    return Profile.model_validate(
        {
            "id": "local",
            "name": "This computer",
            "kind": "local",
            "data_roots": [str(data_root)],
        }
    )


class FakeBackend:
    def __init__(
        self,
        tmp_path: Path,
        *,
        host_known: bool = False,
        expected_answers: list[str] | None = None,
        jobs: list[ExistingJob] | None = None,
        proxy_base: str = "http://127.0.0.1:9",
    ) -> None:
        self.tmp_path = tmp_path
        self.host_known = host_known
        self.expected_answers = expected_answers or ["dev-password", "123456"]
        self.jobs = list(jobs or [])
        self.proxy_base = proxy_base
        self.accepted = False
        self.prepared = 0
        self.submitted = 0
        self.cancelled: list[str] = []
        self.removed: list[str] = []
        self.closed = 0
        self.forward_closed = 0
        self.answers_seen = False
        self.queue = [
            QueueStatus("PENDING", "Resources", None),
            QueueStatus("RUNNING", None, "node01"),
        ]
        self.connection = DataServerConnection(
            host="node01",
            port=9010,
            token="never-expose-this-value",
            api_version="1.0",
        )

    async def inspect_host_key(self, profile: Profile) -> HostKeyStatus:
        del profile
        return HostKeyStatus(
            known=self.host_known,
            fingerprint=None if self.host_known else "SHA256:test-fingerprint",
            public_line=None if self.host_known else "[127.0.0.1]:2222 ssh-ed25519 AAAA",
            custom_known_hosts=self.tmp_path / "known_hosts",
            known_hosts_files=(self.tmp_path / "known_hosts",) if self.host_known else (),
        )

    async def accept_host_key(self, status: HostKeyStatus) -> HostKeyStatus:
        self.accepted = True
        return HostKeyStatus(
            known=True,
            fingerprint=None,
            public_line=None,
            custom_known_hosts=status.custom_known_hosts,
            known_hosts_files=(status.custom_known_hosts,),
        )

    async def connect(
        self,
        profile: Profile,
        status: HostKeyStatus,
        prompt_callback: Callable[[list[Prompt]], Any],
    ) -> object:
        del profile, status
        answers = await prompt_callback(
            [
                Prompt(prompt="Password:", echo=False),
                Prompt(prompt="Verification code:", echo=False),
            ]
        )
        self.answers_seen = answers == self.expected_answers
        if not self.answers_seen:
            raise RuntimeError("Authentication failed")
        answers.clear()
        return object()

    async def prepare(self, context: object, profile: Profile, log: Callable[[str], None]) -> None:
        del context, profile
        self.prepared += 1
        log("Remote server is ready")

    async def list_jobs(self, context: object) -> list[ExistingJob]:
        del context
        return list(self.jobs)

    async def cancel_job(self, context: object, job_id: str) -> None:
        del context
        self.cancelled.append(job_id)
        self.jobs = [job for job in self.jobs if job.job_id != job_id]

    async def submit_job(self, context: object, profile: Profile) -> str:
        del context, profile
        self.submitted += 1
        return "42"

    async def queue_status(self, context: object, job_id: str) -> QueueStatus | None:
        del context, job_id
        if len(self.queue) > 1:
            return self.queue.pop(0)
        return self.queue[0]

    async def connection_file_exists(self, context: object, job_id: str) -> bool:
        del context, job_id
        return True

    async def read_connection(self, context: object, job_id: str) -> DataServerConnection:
        del context, job_id
        return self.connection

    async def forward(self, context: object, connection: DataServerConnection) -> object:
        del context, connection
        return object()

    async def handshake(self, forward: object, connection: DataServerConnection) -> dict[str, Any]:
        del forward, connection
        return {"api_version": "1.0", "environment": "hpc"}

    async def log_tail(self, context: object, job_id: str) -> list[str]:
        del context, job_id
        return []

    async def remove_connection_file(self, context: object, job_id: str) -> None:
        del context
        self.removed.append(job_id)

    async def close_forward(self, forward: object | None) -> None:
        if forward is not None:
            self.forward_closed += 1

    async def close(self, context: object | None) -> None:
        if context is not None:
            self.closed += 1

    async def wait_closed(self, context: object) -> None:
        del context

    def proxy_target(self, forward: object, connection: DataServerConnection) -> tuple[str, str]:
        del forward
        return self.proxy_base, connection.token


async def wait_for_state(
    session: ConnectorSession,
    state: str,
    *,
    timeout: float = 3,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if session.snapshot().state == state:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(
        f"Session did not reach {state!r}; current state is {session.snapshot().state!r}"
    )


async def authenticate_session(
    session: ConnectorSession,
    *,
    accept_host: bool = True,
    answers: list[str] | None = None,
) -> None:
    if session.snapshot().state == "host_key_confirm":
        await session.respond(RespondRequest(accept_host_key=accept_host))
    await wait_for_state(session, "awaiting_input")
    await session.respond(RespondRequest(answers=answers or ["dev-password", "123456"]))


def test_profiles_validate_and_keep_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test-data").mkdir()
    (tmp_path / "test-metadata").mkdir()
    profile_file = tmp_path / "profiles.yaml"
    profile_file.write_text(
        """
profiles:
  - id: cluster
    name: Cluster
    kind: slurm
    host: example.test
    username: alice
    data_roots: [/data]
    slurm: {account: dev, partition: cpu, time: "01:00:00", cpus: 2, mem: 4G}
  - id: local
    name: Local
    kind: local
    data_roots: [./test-data]
    metadata_root: ./test-metadata
""".strip(),
        encoding="utf-8",
    )
    store = load_profiles(profile_file)
    assert [profile.id for profile in store.public()] == ["cluster", "local"]
    assert validate_profile(store, "local").kind == "local"
    assert store.get("local").metadata_root == "./test-metadata"
    with pytest.raises(ProfileError, match="Unknown profile"):
        store.get("missing")


def test_slurm_script_is_complete_and_shell_quotes_values() -> None:
    external_template = (
        Path(__file__).parents[1] / "templates" / "dataserver.sbatch.j2"
    ).read_text(encoding="utf-8")
    packaged_template = (
        resources.files("timed_connector")
        .joinpath("templates", "dataserver.sbatch.j2")
        .read_text(encoding="utf-8")
    )
    assert packaged_template == external_template
    profile = slurm_profile(
        data_roots=["/data/with space"],
        metadata_root="/metadata/with space",
        module_loads=["Python/3.12"],
        collections=[
            {
                "id": "cell-health",
                "title": "Cell Health",
                "path": "/data/with space/cell-health.index.parquet",
                "qc_export": "/qc export/with space",
                "qc_annotations": "/qc annotations/with space",
            }
        ],
    )
    script = render_job_script(
        profile,
        executable="/state/server/0.1.0/venv/bin/timed-dataserver",
        shell_state_dir="/state dir",
    )
    assert "#SBATCH --job-name=timed-dataserver" in script
    assert "#SBATCH --account=dev" in script
    assert "umask 077" in script
    assert "module load Python/3.12" in script
    assert "'/data/with space'" in script
    assert "'/metadata/with space'" in script
    assert "'/data/with space/cell-health.index.parquet=/qc export/with space'" in script
    assert "'/data/with space/cell-health.index.parquet=/qc annotations/with space'" in script
    assert '"${TIMED_NODE_HOST:-$(hostname)}"' in script
    assert "${SLURM_JOB_ID}.json" in script
    assert parse_job_id("1234;cluster\n") == "1234"


def test_arrhenius_profile_renders_verified_slurm_directives() -> None:
    repository_root = Path(__file__).parents[3]
    store = load_profiles(repository_root / "config" / "profiles.example.yaml")
    profile = store.get("arrhenius")

    assert profile.slurm is not None
    assert profile.slurm.account == "naiss2026-3-679-cpu"
    assert profile.slurm.partition == "cpu"
    assert profile.metadata_root == (
        "/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/metadata"
    )
    assert profile.data_roots == [
        "/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/zarr",
        "/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/biomorph/idr0080",
    ]
    assert [collection.model_dump() for collection in profile.collections] == [
        {
            "id": "timed",
            "title": "TIMED",
            "description": "Live-cell time-lapse · 8 plates · 48 h, hourly · 3 channels",
            "path": "/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/zarr",
            "default": True,
            "qc_available": False,
        },
        {
            "id": "idr0080",
            "title": "Cell Health",
            "description": (
                "idr0080 CRISPR Cell Painting - A549, ES2, HCC44 - 9 plates - 5 channels"
            ),
            "path": (
                "/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/biomorph/"
                "idr0080/idr0080-raw.index.parquet"
            ),
            "default": False,
            "qc_available": True,
        },
    ]

    cell_health = profile.collections[1]
    assert cell_health.qc_export == (
        "/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/"
        "qc_metrics_compute/website_export"
    )
    assert cell_health.qc_annotations == (
        "/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_review/annotations"
    )

    script = render_job_script(
        profile,
        executable="/state/server/0.1.0/venv/bin/timed-dataserver",
        shell_state_dir="/state",
    )
    assert "#SBATCH --account=naiss2026-3-679-cpu" in script
    assert "#SBATCH --partition=cpu" in script
    assert f"--qc-export {cell_health.path}={cell_health.qc_export}" in script
    assert f"--qc-annotations {cell_health.path}={cell_health.qc_annotations}" in script


def test_example_fixture_profiles_enable_qc_with_isolated_annotations() -> None:
    repository_root = Path(__file__).parents[3]
    store = load_profiles(repository_root / "config" / "profiles.example.yaml")

    cluster_collection = store.get("test-cluster-datasets").collections[1]
    assert cluster_collection.qc_export == "/absolute/path/to/repo/test-data/qc-export"
    assert cluster_collection.qc_annotations == ("/absolute/path/to/repo/.timed/qc-annotations")

    local_collection = store.get("local-datasets").collections[1]
    assert local_collection.qc_export == str(
        (repository_root / "test-data" / "qc-export").resolve()
    )
    assert local_collection.qc_annotations == str(
        (repository_root / ".timed" / "qc-annotations").resolve()
    )


def test_first_plate_skips_entries_without_ome_zarr_metadata() -> None:
    plate = {
        "name": "PB100087_P1.1.zarr",
        "path": "/data/PB100087_P1.1.zarr",
        "kind": "dir",
        "ome_zarr": {"kind": "plate", "version": "0.5", "zarr_format": 3},
    }
    entries = [
        {"name": "CONVERSION.md", "kind": "file", "ome_zarr": None},
        "invalid",
        plate,
    ]

    assert _first_plate(entries) == plate
    assert _first_plate(None) is None
    assert _first_plate([{"ome_zarr": None}]) is None


def test_export_openapi(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export-openapi"]) == 0
    document = json.loads(capsys.readouterr().out)

    assert document["info"]["title"] == "TIMED connector"
    assert "/connector/session" in document["paths"]
    assert "/connector/connect" in document["paths"]
    collection_schema = document["components"]["schemas"]["CollectionConfig"]
    assert "qc_available" in collection_schema["properties"]
    assert "qc_export" not in collection_schema["properties"]
    assert "qc_annotations" not in collection_schema["properties"]


@pytest.mark.asyncio
async def test_full_session_flow_never_exposes_answers_or_token(tmp_path: Path) -> None:
    profile = slurm_profile()
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    backend = FakeBackend(tmp_path)
    session = ConnectorSession(store, backend, poll_interval=0.001)

    await session.connect(profile.id)
    await wait_for_state(session, "host_key_confirm")
    snapshot_json = session.snapshot().model_dump_json()
    assert "SHA256:test-fingerprint" in snapshot_json
    await authenticate_session(session)
    await wait_for_state(session, "connected")

    snapshot = session.snapshot()
    serialized = snapshot.model_dump_json()
    assert snapshot.job_id == "42"
    assert snapshot.node == "node01"
    assert snapshot.local_forward_port == 9
    assert backend.accepted
    assert backend.answers_seen
    assert backend.prepared == 1
    assert backend.submitted == 1
    assert "dev-password" not in serialized
    assert "123456" not in serialized
    assert backend.connection.token not in serialized
    assert all(backend.connection.token not in line for line in snapshot.logs)

    disconnected = await session.disconnect()
    assert disconnected.state == "disconnected"
    assert backend.cancelled == ["42"]
    assert backend.removed == ["42"]
    assert backend.forward_closed == 1
    assert backend.closed == 1


@pytest.mark.asyncio
async def test_disconnect_closes_pooled_forward_connection_before_cleanup(
    tmp_path: Path,
) -> None:
    forwarded_writers: set[asyncio.StreamWriter] = set()

    async def upstream(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        forwarded_writers.add(writer)
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nConnection: keep-alive\r\n\r\nx")
            await writer.drain()
            await reader.read()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            forwarded_writers.discard(writer)
            writer.close()

    forward_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    forward_port = forward_server.sockets[0].getsockname()[1]

    class PooledForwardBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__(
                tmp_path,
                proxy_base=f"http://127.0.0.1:{forward_port}",
            )
            self.disconnect_started = asyncio.Event()
            self.continue_disconnect = asyncio.Event()

        async def cancel_job(self, context: object, job_id: str) -> None:
            await super().cancel_job(context, job_id)
            self.disconnect_started.set()
            await self.continue_disconnect.wait()

        async def forward(
            self,
            context: object,
            connection: DataServerConnection,
        ) -> asyncio.Server:
            del context, connection
            return forward_server

        async def close_forward(self, forward: object | None) -> None:
            if forward is not None:
                assert forward is forward_server
                assert not forwarded_writers
                forward_server.close()
                await forward_server.wait_closed()
                self.forward_closed += 1

    profile = slurm_profile()
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    backend = PooledForwardBackend()
    session = ConnectorSession(store, backend, poll_interval=0.001)
    app = create_app(store, session, port=8700)
    disconnect_task: asyncio.Task[httpx.Response] | None = None

    async with app.router.lifespan_context(app):
        pooled_client: httpx.AsyncClient = app.state.proxy_client
        try:
            await session.connect(profile.id)
            await wait_for_state(session, "host_key_confirm")
            await authenticate_session(session)
            await wait_for_state(session, "connected")

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1:8700",
            ) as browser:
                proxied = await browser.get("/backend/chunk")
                assert proxied.text == "x"
                assert forwarded_writers

                disconnect_task = asyncio.create_task(
                    browser.post(
                        "/connector/disconnect",
                        headers=CLIENT_HEADER,
                        json={"cancel_job": True},
                    )
                )
                await asyncio.wait_for(backend.disconnect_started.wait(), timeout=1)
                assert session.snapshot().state == "disconnecting"
                assert not forwarded_writers
                backend.continue_disconnect.set()

                disconnected = await asyncio.wait_for(disconnect_task, timeout=1)
                assert disconnected.status_code == 200
                assert disconnected.json()["state"] == "disconnected"
                assert pooled_client.is_closed
        finally:
            backend.continue_disconnect.set()
            if session.snapshot().state != "disconnected":
                await pooled_client.aclose()
            if disconnect_task is not None and not disconnect_task.done():
                await asyncio.gather(disconnect_task, return_exceptions=True)
            if session.snapshot().state != "disconnected":
                await session.disconnect()

    forward_server.close()
    await forward_server.wait_closed()


@pytest.mark.asyncio
async def test_disconnect_timeout_reaches_error_and_continues_cleanup(
    tmp_path: Path,
) -> None:
    class HangingSSHBackend(FakeBackend):
        async def close(self, context: object | None) -> None:
            if context is not None:
                self.closed += 1
                await asyncio.Event().wait()

    profile = slurm_profile()
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    backend = HangingSSHBackend(tmp_path)
    session = ConnectorSession(
        store,
        backend,
        poll_interval=0.001,
        cleanup_timeout=0.01,
    )

    await session.connect(profile.id)
    await wait_for_state(session, "host_key_confirm")
    await authenticate_session(session)
    await wait_for_state(session, "connected")

    disconnected = await asyncio.wait_for(session.disconnect(), timeout=1)

    assert disconnected.state == "error"
    assert disconnected.message is not None
    assert "Timed out while closing the SSH connection" in disconnected.message
    assert backend.closed == 1
    assert backend.forward_closed == 1
    with pytest.raises(SessionError, match="No data server is connected"):
        session.proxy_target()


@pytest.mark.asyncio
async def test_local_backend_forwards_qc_paths_without_prechecking_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    index = data_root / "cell-health.index.parquet"
    index.touch()
    qc_export = tmp_path / "future-export"
    qc_annotations = tmp_path / "future-annotations"
    profile = Profile.model_validate(
        {
            "id": "local",
            "name": "This computer",
            "kind": "local",
            "data_roots": [str(data_root)],
            "collections": [
                {
                    "id": "cell-health",
                    "title": "Cell Health",
                    "path": str(index),
                    "qc_export": str(qc_export),
                    "qc_annotations": str(qc_annotations),
                }
            ],
        }
    )
    arguments: list[str] = []

    class CompletedProcess:
        stdout = None
        returncode = 0

    async def create_subprocess_exec(*values: str, **options: object) -> Any:
        arguments.extend(values)
        assert options["stdout"] == asyncio.subprocess.PIPE
        return CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    backend = LocalBackend(
        state_dir=tmp_path / "state",
        runtime_parent=tmp_path / "runtime",
    )
    context = await backend.start(profile, lambda line: None)
    try:
        export_position = arguments.index("--qc-export")
        annotations_position = arguments.index("--qc-annotations")
        assert arguments[export_position + 1] == (f"{index.resolve()}={qc_export.resolve()}")
        assert arguments[annotations_position + 1] == (
            f"{index.resolve()}={qc_annotations.resolve()}"
        )
        assert not qc_export.exists()
        assert not qc_annotations.exists()
    finally:
        await backend.close(context)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_local_session_starts_proxies_and_stops_process(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    profile = local_profile(data_root)
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    runtime_parent = tmp_path / "runtime"
    session = ConnectorSession(
        store,
        FakeBackend(tmp_path),
        local_backend=LocalBackend(
            state_dir=tmp_path / "state",
            runtime_parent=runtime_parent,
        ),
        poll_interval=0.01,
        startup_timeout=20,
    )

    await session.connect(profile.id)
    await wait_for_state(session, "connected", timeout=20)
    snapshot = session.snapshot()
    serialized = snapshot.model_dump_json()
    assert snapshot.handshake is not None
    assert snapshot.handshake["environment"] == "local"
    assert snapshot.job_id is None
    assert snapshot.slurm_state is None
    assert snapshot.node is None
    assert snapshot.local_forward_port is not None
    assert any("Starting local data server" in line for line in snapshot.logs)

    base_url, token = session.proxy_target()
    assert token not in serialized
    assert all(token not in line for line in snapshot.logs)
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        handshake = await client.get(
            "/api/handshake",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert handshake.status_code == 200
        roots = await client.get(
            "/api/fs/list",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert roots.status_code == 200
        assert roots.json()["entries"][0]["path"] == str(data_root.resolve())

    connection_files = list(runtime_parent.glob("*/connection.json"))
    assert len(connection_files) == 1
    disconnected = await session.disconnect(cancel_job=False)
    assert disconnected.state == "disconnected"
    assert list(runtime_parent.iterdir()) == []

    with pytest.raises(httpx.TransportError):
        async with httpx.AsyncClient(base_url=base_url, timeout=1) as client:
            await client.get("/api/handshake")


@pytest.mark.asyncio
async def test_local_session_rejects_missing_data_root_before_launch(tmp_path: Path) -> None:
    profile = local_profile(tmp_path / "missing")
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    session = ConnectorSession(
        store,
        FakeBackend(tmp_path),
        local_backend=LocalBackend(
            state_dir=tmp_path / "state",
            runtime_parent=tmp_path / "runtime",
        ),
        poll_interval=0.01,
    )

    with pytest.raises(SessionError, match="Local profile path does not exist"):
        await session.connect(profile.id)
    assert session.snapshot().state == "disconnected"
    assert not (tmp_path / "runtime").exists()


@pytest.mark.asyncio
async def test_local_process_exit_becomes_visible_error(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    profile = local_profile(data_root)
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    runtime_parent = tmp_path / "runtime"
    session = ConnectorSession(
        store,
        FakeBackend(tmp_path),
        local_backend=LocalBackend(
            state_dir=tmp_path / "state",
            runtime_parent=runtime_parent,
        ),
        poll_interval=0.01,
        startup_timeout=20,
    )

    try:
        await session.connect(profile.id)
        await wait_for_state(session, "connected", timeout=20)
        connection_file = next(runtime_parent.glob("*/connection.json"))
        process_id = int(json.loads(connection_file.read_text(encoding="utf-8"))["pid"])
        os.kill(process_id, signal.SIGTERM)

        await wait_for_state(session, "error", timeout=5)
        snapshot = session.snapshot()
        assert snapshot.message == "The local data-server process ended"
        assert snapshot.local_forward_port is None
        assert snapshot.handshake is None
        assert list(runtime_parent.iterdir()) == []
    finally:
        await session.disconnect()


@pytest.mark.asyncio
async def test_wrong_authentication_enters_error_without_secrets(tmp_path: Path) -> None:
    profile = slurm_profile()
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    backend = FakeBackend(tmp_path)
    session = ConnectorSession(store, backend, poll_interval=0.001)

    await session.connect(profile.id)
    await wait_for_state(session, "host_key_confirm")
    await session.respond(RespondRequest(accept_host_key=True))
    await wait_for_state(session, "awaiting_input")
    await session.respond(RespondRequest(answers=["dev-password", "wrong-code"]))
    await wait_for_state(session, "error")

    serialized = session.snapshot().model_dump_json()
    assert "Authentication failed" in serialized
    assert "dev-password" not in serialized
    assert "wrong-code" not in serialized
    await session.disconnect()


@pytest.mark.asyncio
async def test_existing_job_can_be_reused_or_cancelled(tmp_path: Path) -> None:
    profile = slurm_profile()
    store = ProfileStore([profile], tmp_path / "profiles.yaml")

    reuse_backend = FakeBackend(
        tmp_path,
        host_known=True,
        jobs=[ExistingJob(job_id="77", state="RUNNING", node="node77")],
    )
    reuse_session = ConnectorSession(store, reuse_backend, poll_interval=0.001)
    await reuse_session.connect(profile.id)
    await authenticate_session(reuse_session)
    await wait_for_state(reuse_session, "choose_existing_job")
    await reuse_session.respond(
        RespondRequest(existing_job=ExistingJobChoice(action="reuse", job_id="77"))
    )
    await wait_for_state(reuse_session, "connected")
    assert reuse_session.snapshot().job_id == "77"
    assert reuse_backend.submitted == 0
    await reuse_session.disconnect()
    assert reuse_backend.cancelled == ["77"]

    cancel_backend = FakeBackend(
        tmp_path,
        host_known=True,
        jobs=[ExistingJob(job_id="88", state="RUNNING", node="node88")],
    )
    cancel_session = ConnectorSession(store, cancel_backend, poll_interval=0.001)
    await cancel_session.connect(profile.id)
    await authenticate_session(cancel_session)
    await wait_for_state(cancel_session, "choose_existing_job")
    await cancel_session.respond(
        RespondRequest(existing_job=ExistingJobChoice(action="cancel", job_id="88"))
    )
    await wait_for_state(cancel_session, "connected")
    assert cancel_backend.cancelled == ["88"]
    assert cancel_backend.submitted == 1
    await cancel_session.disconnect()
    assert cancel_backend.cancelled == ["88", "42"]


@pytest.mark.asyncio
async def test_connector_http_guards_and_public_payloads(tmp_path: Path) -> None:
    profile = slurm_profile()
    store = ProfileStore([profile], tmp_path / "profiles.yaml")
    session = ConnectorSession(store, FakeBackend(tmp_path), poll_interval=0.001)
    app = create_app(store, session, port=8700)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:8700",
    ) as client:
        profiles = await client.get("/connector/profiles")
        assert profiles.status_code == 200
        serialized = profiles.text.lower()
        assert "password" not in serialized
        assert "token" not in serialized
        assert "access-control-allow-origin" not in profiles.headers

        missing_client_header = await client.post(
            "/connector/connect",
            json={"profile_id": profile.id},
        )
        assert missing_client_header.status_code == 403

        connected = await client.post(
            "/connector/connect",
            headers=CLIENT_HEADER,
            json={"profile_id": profile.id},
        )
        assert connected.status_code == 202

    invalid_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=invalid_transport,
        base_url="http://attacker.example",
    ) as client:
        invalid = await client.get("/connector/session")
        assert invalid.status_code == 400
    await session.disconnect()


@pytest.mark.asyncio
async def test_proxy_streams_range_and_hides_bearer_token() -> None:
    observed: dict[str, str] = {}

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        headers = await reader.readuntil(b"\r\n\r\n")
        text = headers.decode("latin-1")
        for line in text.split("\r\n")[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                observed[name.lower()] = value.strip()
        body = b"four"
        writer.write(
            b"HTTP/1.1 206 Partial Content\r\n"
            b"Content-Length: 4\r\n"
            b"Content-Range: bytes 2-5/10\r\n"
            b"Accept-Ranges: bytes\r\n"
            b'ETag: "shard-version"\r\n'
            b"Cache-Control: private, max-age=3600\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class ProxySession:
        def proxy_target(self) -> tuple[str, str]:
            return f"http://127.0.0.1:{port}", "proxy-private-token"

    proxy_app = FastAPI()
    proxy_client = httpx.AsyncClient(timeout=None, trust_env=False)

    @proxy_app.api_route("/backend/{path:path}", methods=["GET"])
    async def backend(request: Request, path: str):
        async def unexpected_body():
            raise AssertionError("A GET proxy request must not consume the client stream")
            yield b""

        request.stream = unexpected_body  # type: ignore[method-assign]
        return await proxy_request(  # type: ignore[arg-type]
            request,
            path,
            ProxySession(),
            proxy_client,
        )

    try:
        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://connector.test",
        ) as client:
            response = await client.get(
                "/backend/zarr/id/chunk",
                headers={
                    "Range": "bytes=2-5",
                    "If-Range": '"previous-version"',
                    "X-Timed-Client": "web",
                },
            )
        assert response.status_code == 206
        assert response.content == b"four"
        assert response.headers["content-range"] == "bytes 2-5/10"
        assert response.headers["etag"] == '"shard-version"'
        assert response.headers["cache-control"] == "private, max-age=3600"
        assert observed["range"] == "bytes=2-5"
        assert observed["if-range"] == '"previous-version"'
        assert observed["authorization"] == "Bearer proxy-private-token"
        assert "x-timed-client" not in observed
        assert "proxy-private-token" not in response.text
        assert not proxy_client.is_closed
    finally:
        await proxy_client.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_proxy_reuses_upstream_connections() -> None:
    connection_count = 0
    request_count = 0

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count, request_count
        connection_count += 1
        try:
            while request_count < 2:
                await reader.readuntil(b"\r\n\r\n")
                request_count += 1
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Length: 1\r\n"
                    b"Connection: keep-alive\r\n\r\n"
                    b"x"
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class ProxySession:
        def proxy_target(self) -> tuple[str, str]:
            return f"http://127.0.0.1:{port}", "proxy-private-token"

    proxy_app = FastAPI()
    proxy_client = httpx.AsyncClient(timeout=None, trust_env=False)

    @proxy_app.get("/backend/{path:path}")
    async def backend(request: Request, path: str):
        return await proxy_request(  # type: ignore[arg-type]
            request,
            path,
            ProxySession(),
            proxy_client,
        )

    try:
        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://connector.test",
        ) as browser:
            first = await browser.get("/backend/first")
            second = await browser.get("/backend/second")
        assert first.content == b"x"
        assert second.content == b"x"
        assert request_count == 2
        assert connection_count == 1
    finally:
        await proxy_client.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_proxy_returns_503_while_disconnected() -> None:
    class DisconnectedSession:
        def proxy_target(self) -> tuple[str, str]:
            raise SessionError("not connected")

    app = FastAPI()
    proxy_client = httpx.AsyncClient(timeout=None, trust_env=False)

    @app.get("/backend/{path:path}")
    async def backend(request: Request, path: str):
        return await proxy_request(  # type: ignore[arg-type]
            request,
            path,
            DisconnectedSession(),
            proxy_client,
        )

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/backend/api/handshake")
        assert response.status_code == 503
        assert response.json() == {"detail": "No data server is connected"}
        assert not proxy_client.is_closed
    finally:
        await proxy_client.aclose()
