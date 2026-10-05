from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
from timed_connector.app import create_app
from timed_connector.hpc_backend import AsyncSSHHPCBackend
from timed_connector.models import ExistingJobChoice, Profile, RespondRequest
from timed_connector.profiles import ProfileStore
from timed_connector.session import ConnectorSession

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="The local SSH/Slurm harness is POSIX-only",
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _make_tiny_image(data_root: Path) -> Path:
    image = data_root / "integration_image.zarr"
    _write_json(image / ".zgroup", {"zarr_format": 2})
    _write_json(
        image / ".zattrs",
        {
            "multiscales": [
                {
                    "version": "0.4",
                    "name": "integration image",
                    "axes": [
                        {"name": "t", "type": "time"},
                        {"name": "c", "type": "channel"},
                        {"name": "y", "type": "space"},
                        {"name": "x", "type": "space"},
                    ],
                    "datasets": [{"path": "s0"}],
                }
            ]
        },
    )
    _write_json(
        image / "s0" / ".zarray",
        {
            "zarr_format": 2,
            "shape": [1, 1, 1, 8],
            "chunks": [1, 1, 1, 8],
            "dtype": "|u1",
        },
    )
    chunk = image / "s0" / "0" / "0" / "0"
    chunk.mkdir(parents=True)
    (chunk / "0").write_bytes(b"01234567")
    return image


async def _wait_for_listener(port: int, process: asyncio.subprocess.Process) -> None:
    for _ in range(200):
        if process.returncode is not None:
            stderr = await process.stderr.read() if process.stderr else b""
            raise AssertionError(f"Test cluster stopped: {stderr.decode(errors='replace')}")
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
        else:
            writer.close()
            await writer.wait_closed()
            return
    raise AssertionError("Timed out waiting for the test-cluster listener")


async def _drive(
    session: ConnectorSession,
    *,
    verification_code: str,
    existing_action: str | None = None,
    timeout: float = 180,
) -> set[str]:
    states: set[str] = set()
    handled_state: str | None = None
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        snapshot = session.snapshot()
        states.add(snapshot.state)
        if snapshot.state in ("connected", "error"):
            return states
        if snapshot.state != handled_state:
            if snapshot.state == "host_key_confirm":
                await session.respond(RespondRequest(accept_host_key=True))
            elif snapshot.state == "awaiting_input":
                answers = []
                for prompt in snapshot.prompts:
                    if "verification" in prompt.prompt.lower():
                        answers.append(verification_code)
                    elif "password" in prompt.prompt.lower():
                        answers.append("dev-password")
                    else:
                        raise AssertionError(f"Unexpected prompt: {prompt.prompt}")
                await session.respond(RespondRequest(answers=answers))
                answers.clear()
            elif snapshot.state == "choose_existing_job":
                if existing_action is None:
                    raise AssertionError("Unexpected existing Slurm job")
                await session.respond(
                    RespondRequest(
                        existing_job=ExistingJobChoice(
                            action=existing_action,
                            job_id=snapshot.existing_jobs[0].job_id,
                        )
                    )
                )
            handled_state = snapshot.state
        await asyncio.sleep(0.02)
    raise AssertionError(f"Session timed out in state {session.snapshot().state}")


@pytest.mark.asyncio
async def test_real_test_cluster_hpc_flow(tmp_path: Path) -> None:
    port = _free_port()
    cluster_state = tmp_path / "cluster"
    data_root = tmp_path / "data"
    data_root.mkdir()
    image = _make_tiny_image(data_root)
    environment = os.environ.copy()
    environment["TEST_CLUSTER_PENDING_SECONDS"] = "0.3"
    environment["TEST_CLUSTER_PASSWORD"] = "dev-password"
    environment["TEST_CLUSTER_VERIFICATION_CODE"] = "123456"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(REPOSITORY_ROOT / "dev" / "test_cluster" / "server.py"),
        "--port",
        str(port),
        "--state-dir",
        str(cluster_state),
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    sessions: list[ConnectorSession] = []
    try:
        await _wait_for_listener(port, process)
        profile = Profile.model_validate(
            {
                "id": "integration",
                "name": "Integration test cluster",
                "kind": "slurm",
                "host": "127.0.0.1",
                "port": port,
                "username": "dev",
                "known_hosts": str(tmp_path / "known_hosts"),
                "data_roots": [str(data_root)],
                "remote_state_dir": "~/.timed",
                "slurm": {
                    "account": "dev",
                    "partition": "cpu",
                    "time": "00:10:00",
                    "cpus": 1,
                    "mem": "1G",
                },
            }
        )
        store = ProfileStore([profile], tmp_path / "profiles.yaml")

        wrong = ConnectorSession(
            store,
            AsyncSSHHPCBackend(store.source),
            poll_interval=0.05,
            startup_timeout=180,
        )
        sessions.append(wrong)
        await wrong.connect(profile.id)
        wrong_states = await _drive(wrong, verification_code="wrong")
        assert "host_key_confirm" in wrong_states
        assert wrong.snapshot().state == "error"
        assert "wrong" not in wrong.snapshot().model_dump_json()
        await wrong.disconnect()

        first = ConnectorSession(
            store,
            AsyncSSHHPCBackend(store.source),
            poll_interval=0.05,
            startup_timeout=180,
        )
        sessions.append(first)
        await first.connect(profile.id)
        first_states = await _drive(first, verification_code="123456")
        assert (
            first.snapshot().state == "connected"
        ), first.snapshot().model_dump_json(indent=2)
        assert "queued" in first_states
        assert any("Installed remote data-server" in line for line in first.snapshot().logs)
        first_job = first.snapshot().job_id
        assert first_job is not None

        app = create_app(store, first, port=8700)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8700",
            timeout=30,
        ) as client:
            handshake = await client.get("/backend/api/handshake")
            assert handshake.status_code == 200
            linked = await client.post(
                "/backend/api/datasets",
                json={"path": str(image)},
            )
            assert linked.status_code == 201, linked.text
            dataset_id = linked.json()["id"]
            ranged = await client.get(
                f"/backend/zarr/{dataset_id}/s0/0/0/0/0",
                headers={"Range": "bytes=2-5"},
            )
            assert ranged.status_code == 206
            assert ranged.content == b"2345"
            assert ranged.headers["content-range"] == "bytes 2-5/8"

        await first.disconnect(cancel_job=False)

        second = ConnectorSession(
            store,
            AsyncSSHHPCBackend(store.source),
            poll_interval=0.05,
            startup_timeout=180,
        )
        sessions.append(second)
        await second.connect(profile.id)
        second_states = await _drive(
            second,
            verification_code="123456",
            existing_action="reuse",
        )
        assert second.snapshot().state == "connected"
        assert "choose_existing_job" in second_states
        assert second.snapshot().job_id == first_job
        assert any("already installed" in line for line in second.snapshot().logs)
        await second.disconnect(cancel_job=True)

        job = json.loads(
            (cluster_state / "jobs" / f"{first_job}.json").read_text(encoding="utf-8")
        )
        assert job["state"] == "CANCELLED"
        assert all(
            json.loads(path.read_text(encoding="utf-8"))["state"]
            not in {"PENDING", "CONFIGURING", "RUNNING"}
            for path in (cluster_state / "jobs").glob("*.json")
        )
    finally:
        for session in sessions:
            with suppress(Exception):
                await session.disconnect(cancel_job=True)
        if process.returncode is None:
            process.terminate()
            with suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5)
        if process.returncode is None:
            process.kill()
            await process.wait()
