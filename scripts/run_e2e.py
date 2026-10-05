#!/usr/bin/env python3
"""Run the browser suite with an isolated SSH/Slurm test cluster."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import suppress
from pathlib import Path
from typing import IO

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = REPOSITORY_ROOT / "test-data"
PORTS = (2222, 8700, 4321)


def _pnpm() -> str:
    return "pnpm.cmd" if os.name == "nt" else "pnpm"


def _require_free_port(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port {port} is already in use")


def _wait_port(port: int, process: subprocess.Popen[bytes], timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited before opening port {port}")
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for port {port}")


def _wait_http(url: str, process: subprocess.Popen[bytes], timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited before responding at {url}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        with suppress(OSError):
            process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=8)
        return
    except subprocess.TimeoutExpired:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _start(
    arguments: list[str],
    log: IO[bytes],
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    options: dict[str, object] = {
        "cwd": REPOSITORY_ROOT,
        "env": environment,
        "stdout": log,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(arguments, **options)


def _wsl_path(path: Path) -> str:
    result = subprocess.run(
        ["wsl.exe", "--", "wslpath", "-a", path.resolve().as_posix()],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _profile(
    path: Path,
    cluster_data_root: str,
    local_data_root: str,
    known_hosts: Path,
) -> None:
    value = {
        "profiles": [
            {
                "id": "test-cluster",
                "name": "Test cluster (local SSH/Slurm harness)",
                "kind": "slurm",
                "host": "127.0.0.1",
                "port": 2222,
                "username": "dev",
                "known_hosts": str(known_hosts),
                "data_roots": [cluster_data_root],
                "slurm": {
                    "account": "dev",
                    "partition": "cpu",
                    "time": "00:10:00",
                    "cpus": 1,
                    "mem": "1G",
                },
                "remote_state_dir": "~/.timed",
                "auto_install_uv": True,
                "module_loads": [],
            },
            {
                "id": "local",
                "name": "This computer",
                "kind": "local",
                "data_roots": [local_data_root],
            },
        ]
    }
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _local_profile(path: Path, local_data_root: Path) -> None:
    value = {
        "profiles": [
            {
                "id": "local",
                "name": "This computer",
                "kind": "local",
                "data_roots": [str(local_data_root)],
            },
        ]
    }
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _active_jobs(state: str, repository: str) -> str:
    script = f"{repository}/dev/test_cluster/bin/_slurm.py"
    if os.name == "nt":
        command = (
            "export TIMED_TEST_CLUSTER_STATE="
            + shlex.quote(state)
            + "; python3 "
            + shlex.quote(script)
            + " squeue -o %i"
        )
        result = subprocess.run(
            ["wsl.exe", "bash", "-lc", command],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        environment = os.environ.copy()
        environment["TIMED_TEST_CLUSTER_STATE"] = state
        result = subprocess.run(
            [sys.executable, script, "squeue", "-o", "%i"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    return result.stdout.strip()


def _remove_wsl_cluster_state(state: str) -> None:
    prefix = "/tmp/timed-e2e-"
    if not state.startswith(prefix) or len(state) != len(prefix) + 32:
        raise RuntimeError(f"Refusing to remove unexpected WSL state path: {state}")
    subprocess.run(
        [
            "wsl.exe",
            "--",
            "python3",
            "-c",
            "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)",
            state,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the TIMED browser tests with isolated services."
    )
    parser.add_argument(
        "--real-local-root",
        type=Path,
        help="Run only the opt-in real-sample flow against this local data root.",
    )
    parser.add_argument(
        "--real-local-store",
        help="OME-Zarr store name inside --real-local-root.",
    )
    arguments, playwright_arguments = parser.parse_known_args()

    if bool(arguments.real_local_root) != bool(arguments.real_local_store):
        parser.error("--real-local-root and --real-local-store must be provided together")

    real_local_root = arguments.real_local_root.resolve() if arguments.real_local_root else None
    real_local_store = arguments.real_local_store
    real_local = real_local_root is not None

    if real_local_root and not real_local_root.is_dir():
        raise RuntimeError(f"Local data root does not exist: {real_local_root}")
    if real_local_root and not (real_local_root / str(real_local_store)).is_dir():
        raise RuntimeError(
            f"OME-Zarr store does not exist: {real_local_root / str(real_local_store)}"
        )
    if not real_local and not TEST_DATA.is_dir():
        raise RuntimeError("Run `make test-data` before the browser suite")
    for port in (8700, 4321) if real_local else PORTS:
        _require_free_port(port)

    run_id = uuid.uuid4().hex
    runtime = REPOSITORY_ROOT / ".timed" / "e2e" / run_id
    runtime.mkdir(parents=True)
    profiles = runtime / "profiles.yaml"
    known_hosts = runtime / "known_hosts"
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[IO[bytes]] = []
    result_code = 1

    cluster_state: str | None = None
    repository_for_cluster: str | None = None
    services: list[tuple[str, list[str], dict[str, str] | None]] = []

    if real_local_root:
        _local_profile(profiles, real_local_root)
    else:
        cluster_environment = None
        if os.name == "nt":
            repository_for_cluster = _wsl_path(REPOSITORY_ROOT)
            cluster_data_root = _wsl_path(TEST_DATA)
            cluster_state = "/tmp/timed-e2e-" + run_id
            cluster_command = (
                "cd "
                + shlex.quote(repository_for_cluster)
                + " && export UV_PROJECT_ENVIRONMENT=/tmp/timed-wsl-e2e-venv"
                + " && export TEST_CLUSTER_PASSWORD=dev-password"
                + " && export TEST_CLUSTER_VERIFICATION_CODE=123456"
                + " && export TEST_CLUSTER_PENDING_SECONDS=2"
                + " && exec uv run python dev/test_cluster/server.py --state-dir "
                + shlex.quote(cluster_state)
            )
            cluster_arguments = ["wsl.exe", "bash", "-lc", cluster_command]
        else:
            repository_for_cluster = str(REPOSITORY_ROOT)
            cluster_data_root = str(TEST_DATA.resolve())
            cluster_state = str(runtime / "cluster")
            cluster_environment = os.environ.copy()
            cluster_environment.update(
                {
                    "TEST_CLUSTER_PASSWORD": "dev-password",
                    "TEST_CLUSTER_VERIFICATION_CODE": "123456",
                    "TEST_CLUSTER_PENDING_SECONDS": "2",
                }
            )
            cluster_arguments = [
                sys.executable,
                "dev/test_cluster/server.py",
                "--state-dir",
                cluster_state,
            ]

        _profile(
            profiles,
            cluster_data_root,
            str(TEST_DATA.resolve()),
            known_hosts,
        )
        services.append(("cluster", cluster_arguments, cluster_environment))

    services.extend(
        [
            (
                "connector",
                [
                    sys.executable,
                    "-m",
                    "timed_connector.cli",
                    "serve",
                    "--profiles",
                    str(profiles),
                    "--local-state-dir",
                    str(runtime / "local-state"),
                ],
                None,
            ),
            (
                "web",
                [_pnpm(), "-C", "apps/web", "dev", "--host", "127.0.0.1"],
                None,
            ),
        ]
    )

    try:
        for name, service_arguments, process_environment in services:
            log = (runtime / f"{name}.log").open("wb")
            logs.append(log)
            process = _start(service_arguments, log, environment=process_environment)
            processes.append(process)
            if name == "cluster":
                _wait_port(2222, process)
            elif name == "connector":
                _wait_http("http://127.0.0.1:8700/connector/session", process)
            else:
                _wait_http("http://127.0.0.1:4321/", process)

        command = [
            _pnpm(),
            "-C",
            "apps/web",
            "run",
            "test:playwright",
            *(["tests/e2e/real-local-flow.spec.ts"] if real_local else playwright_arguments),
        ]
        test_environment = os.environ.copy()
        if real_local_root:
            test_environment.update(
                {
                    "TIMED_REAL_LOCAL_ROOT_NAME": real_local_root.name,
                    "TIMED_REAL_LOCAL_STORE": str(real_local_store),
                }
            )
        result_code = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            env=test_environment,
        ).returncode
        if result_code == 0 and cluster_state and repository_for_cluster:
            active = _active_jobs(cluster_state, repository_for_cluster)
            if active:
                print(f"E2E cleanup failed; active test-cluster jobs: {active}", file=sys.stderr)
                result_code = 1
        return result_code
    finally:
        for process in reversed(processes):
            _stop(process)
        for log in logs:
            log.close()
        if result_code == 0:
            if os.name == "nt" and cluster_state:
                _remove_wsl_cluster_state(cluster_state)
            shutil.rmtree(runtime)
        else:
            print(f"E2E service logs: {runtime}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
