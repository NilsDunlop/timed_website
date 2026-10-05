from __future__ import annotations

import asyncio
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from timed_connector.app import create_app
from timed_connector.hpc_backend import DataServerConnection
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

    async def prepare(
        self, context: object, profile: Profile, log: Callable[[str], None]
    ) -> None:
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

    async def read_connection(
        self, context: object, job_id: str
    ) -> DataServerConnection:
        del context, job_id
        return self.connection

    async def forward(self, context: object, connection: DataServerConnection) -> object:
        del context, connection
        return object()

    async def handshake(
        self, forward: object, connection: DataServerConnection
    ) -> dict[str, Any]:
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

    def proxy_target(
        self, forward: object, connection: DataServerConnection
    ) -> tuple[str, str]:
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
    await session.respond(
        RespondRequest(answers=answers or ["dev-password", "123456"])
    )


def test_profiles_validate_and_keep_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test-data").mkdir()
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
""".strip(),
        encoding="utf-8",
    )
    store = load_profiles(profile_file)
    assert [profile.id for profile in store.public()] == ["cluster", "local"]
    assert validate_profile(store, "local").kind == "local"
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
        module_loads=["Python/3.12"],
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

    script = render_job_script(
        profile,
        executable="/state/server/0.1.0/venv/bin/timed-dataserver",
        shell_state_dir="/state",
    )
    assert "#SBATCH --account=naiss2026-3-679-cpu" in script
    assert "#SBATCH --partition=cpu" in script


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
        RespondRequest(
            existing_job=ExistingJobChoice(action="reuse", job_id="77")
        )
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
        RespondRequest(
            existing_job=ExistingJobChoice(action="cancel", job_id="88")
        )
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

    @proxy_app.api_route("/backend/{path:path}", methods=["GET"])
    async def backend(request: Request, path: str):
        return await proxy_request(request, path, ProxySession())  # type: ignore[arg-type]

    try:
        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://connector.test",
        ) as client:
            response = await client.get(
                "/backend/zarr/id/chunk",
                headers={"Range": "bytes=2-5", "X-Timed-Client": "web"},
            )
        assert response.status_code == 206
        assert response.content == b"four"
        assert response.headers["content-range"] == "bytes 2-5/10"
        assert observed["range"] == "bytes=2-5"
        assert observed["authorization"] == "Bearer proxy-private-token"
        assert "x-timed-client" not in observed
        assert "proxy-private-token" not in response.text
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_proxy_returns_503_while_disconnected() -> None:
    class DisconnectedSession:
        def proxy_target(self) -> tuple[str, str]:
            raise SessionError("not connected")

    app = FastAPI()

    @app.get("/backend/{path:path}")
    async def backend(request: Request, path: str):
        return await proxy_request(request, path, DisconnectedSession())  # type: ignore[arg-type]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/backend/api/handshake")
    assert response.status_code == 503
    assert response.json() == {"detail": "No data server is connected"}
