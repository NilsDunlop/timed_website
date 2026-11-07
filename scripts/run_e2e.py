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
CLUSTER_PORT = 2222


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
    cluster_idr0080_index: str,
    local_idr0080_index: str,
    known_hosts: Path,
    *,
    cluster_qc_export: str,
    local_qc_export: str,
    cluster_qc_annotations: str,
    local_qc_annotations: str,
    include_cluster: bool = True,
) -> None:
    cluster_idr0080_root = cluster_idr0080_index.rsplit("/", 1)[0]
    local_idr0080_root = str(Path(local_idr0080_index).parent)
    timed_collection = {
        "id": "timed",
        "title": "TIMED",
        "description": "Live-cell time-lapse · test fixture · 3 channels",
        "default": True,
    }
    idr0080_collection = {
        "id": "idr0080",
        "title": "Cell Health",
        "description": "idr0080-like CRISPR Cell Painting test collection",
    }
    cluster_profile = {
        "id": "test-cluster",
        "name": "Test cluster (local SSH/Slurm harness)",
        "kind": "slurm",
        "host": "127.0.0.1",
        "port": 2222,
        "username": "dev",
        "known_hosts": str(known_hosts),
        "data_roots": [cluster_data_root],
        "metadata_root": cluster_data_root.rstrip("/") + "/metadata",
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
    }
    local_profile = {
        "id": "local",
        "name": "This computer",
        "kind": "local",
        "data_roots": [local_data_root],
        "metadata_root": str(Path(local_data_root) / "metadata"),
    }
    value = {
        "profiles": [
            cluster_profile,
            local_profile,
            {
                **cluster_profile,
                "id": "test-cluster-datasets",
                "name": "Test cluster with datasets",
                "data_roots": [cluster_data_root, cluster_idr0080_root],
                "collections": [
                    {
                        **timed_collection,
                        "path": cluster_data_root,
                        "masks_root": cluster_data_root.rstrip("/") + "/timed-masks",
                    },
                    {
                        **idr0080_collection,
                        "path": cluster_idr0080_index,
                        "qc_export": cluster_qc_export,
                        "qc_annotations": cluster_qc_annotations,
                    },
                ],
            },
            {
                **local_profile,
                "id": "local-datasets",
                "name": "This computer with datasets",
                "data_roots": [local_data_root, local_idr0080_root],
                "collections": [
                    {
                        **timed_collection,
                        "path": local_data_root,
                        "masks_root": str(Path(local_data_root) / "timed-masks"),
                    },
                    {
                        **idr0080_collection,
                        "path": local_idr0080_index,
                        "qc_export": local_qc_export,
                        "qc_annotations": local_qc_annotations,
                    },
                ],
            },
        ]
    }
    if not include_cluster:
        value["profiles"] = [
            profile for profile in value["profiles"] if profile["kind"] == "local"
        ]
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _local_profile(path: Path, local_data_root: Path) -> None:
    profile = {
        "id": "local",
        "name": "This computer",
        "kind": "local",
        "data_roots": [str(local_data_root)],
    }
    metadata_root = local_data_root / "metadata"
    if metadata_root.is_dir():
        profile["metadata_root"] = str(metadata_root)
    value = {"profiles": [profile]}
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _real_timed_profile(path: Path, copy_root: Path) -> None:
    data_root = copy_root / "zarr"
    metadata_root = copy_root / "metadata"
    masks_root = copy_root / "corrected"
    timed_collection: dict[str, object] = {
        "id": "timed",
        "title": "TIMED",
        "description": "Live-cell time-lapse · PB100140_P2.1 · 48 h, hourly · 3 channels",
        "path": str(data_root),
        "default": True,
    }
    if masks_root.is_dir():
        timed_collection["masks_root"] = str(masks_root)
    value = {
        "profiles": [
            {
                "id": "local-timed",
                "name": "Local TIMED (external drive)",
                "kind": "local",
                "data_roots": [str(data_root)],
                "metadata_root": str(metadata_root),
                "collections": [timed_collection],
            }
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
    parser.add_argument(
        "--real-timed-copy",
        type=Path,
        help=(
            "Run the opt-in Berzelius-layout browser check against a local TIMED "
            "copy containing zarr/ and metadata/."
        ),
    )
    parser.add_argument(
        "--connector-port",
        type=int,
        default=8700,
        help="Loopback connector port (default: 8700).",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=4321,
        help="Loopback Astro port (default: 4321).",
    )
    parser.add_argument(
        "--built-ui",
        action="store_true",
        help="Serve apps/web/dist from the connector instead of starting Astro dev.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Skip the SSH/Slurm test cluster and the browser flow that needs it.",
    )
    arguments, playwright_arguments = parser.parse_known_args()

    if bool(arguments.real_local_root) != bool(arguments.real_local_store):
        parser.error("--real-local-root and --real-local-store must be provided together")
    if arguments.real_timed_copy and arguments.real_local_root:
        parser.error("--real-timed-copy cannot be combined with --real-local-root")
    if arguments.local_only and (arguments.real_timed_copy or arguments.real_local_root):
        parser.error("--local-only cannot be combined with the real-data modes")
    if not 1 <= arguments.connector_port <= 65535:
        parser.error("--connector-port must be between 1 and 65535")
    if not 1 <= arguments.web_port <= 65535:
        parser.error("--web-port must be between 1 and 65535")
    if arguments.connector_port == arguments.web_port:
        parser.error("--connector-port and --web-port must be different")

    real_timed_copy = arguments.real_timed_copy.resolve() if arguments.real_timed_copy else None
    real_local_root = (
        real_timed_copy / "zarr"
        if real_timed_copy
        else arguments.real_local_root.resolve()
        if arguments.real_local_root
        else None
    )
    real_local_store = "PB100140_P2.1.zarr" if real_timed_copy else arguments.real_local_store
    real_local = real_local_root is not None
    built_ui = REPOSITORY_ROOT / "apps" / "web" / "dist"

    if real_local_root and not real_local_root.is_dir():
        raise RuntimeError(f"Local data root does not exist: {real_local_root}")
    if real_local_root and not (real_local_root / str(real_local_store)).is_dir():
        raise RuntimeError(
            f"OME-Zarr store does not exist: {real_local_root / str(real_local_store)}"
        )
    if real_timed_copy and not (real_timed_copy / "metadata").is_dir():
        raise RuntimeError(f"Local metadata root does not exist: {real_timed_copy / 'metadata'}")
    if arguments.built_ui and not built_ui.is_dir():
        raise RuntimeError("Build the web application before using --built-ui")
    if not real_local and (not TEST_DATA.is_dir() or not (TEST_DATA / "metadata").is_dir()):
        raise RuntimeError("Run `make test-data` before the browser suite")
    packed_source = TEST_DATA / "packed" / "idr0080-like"
    packed_source_index = packed_source / "idr0080-raw.index.parquet"
    if not real_local and not packed_source_index.is_file():
        raise RuntimeError("Run `make test-data` to create the packed browser fixture")
    qc_export = TEST_DATA / "qc-export"
    if not real_local and not qc_export.is_dir():
        raise RuntimeError("Run `make test-data` to create the QC export fixture")
    required_ports = [arguments.connector_port]
    if not arguments.built_ui:
        required_ports.append(arguments.web_port)
    if not real_local and not arguments.local_only:
        required_ports.insert(0, CLUSTER_PORT)
    for port in required_ports:
        _require_free_port(port)

    run_id = uuid.uuid4().hex
    runtime = REPOSITORY_ROOT / ".timed" / "e2e" / run_id
    runtime.mkdir(parents=True)
    packed_fixture = runtime / "idr0080-like"
    if not real_local:
        shutil.copytree(packed_source, packed_fixture)
    packed_index = packed_fixture / "idr0080-raw.index.parquet"
    cluster_qc_annotations_path = runtime / "qc-annotations" / "cluster"
    local_qc_annotations_path = runtime / "qc-annotations" / "local"
    if not real_local:
        cluster_qc_annotations_path.mkdir(parents=True)
        local_qc_annotations_path.mkdir(parents=True)
    profiles = runtime / "profiles.yaml"
    known_hosts = runtime / "known_hosts"
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[IO[bytes]] = []
    result_code = 1

    cluster_state: str | None = None
    repository_for_cluster: str | None = None
    services: list[tuple[str, list[str], dict[str, str] | None]] = []

    if real_local_root:
        if real_timed_copy:
            _real_timed_profile(profiles, real_timed_copy)
        else:
            _local_profile(profiles, real_local_root)
    elif arguments.local_only:
        local_index = str(packed_index.resolve())
        _profile(
            profiles,
            "/unused",
            str(TEST_DATA.resolve()),
            "/unused/" + packed_index.name,
            local_index,
            known_hosts,
            cluster_qc_export="/unused",
            local_qc_export=str(qc_export.resolve()),
            cluster_qc_annotations="/unused",
            local_qc_annotations=str(local_qc_annotations_path.resolve()),
            include_cluster=False,
        )
    else:
        cluster_environment = None
        if os.name == "nt":
            repository_for_cluster = _wsl_path(REPOSITORY_ROOT)
            cluster_data_root = _wsl_path(TEST_DATA)
            cluster_idr0080_index = _wsl_path(packed_index)
            cluster_qc_export = _wsl_path(qc_export)
            cluster_qc_annotations = _wsl_path(cluster_qc_annotations_path)
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
            cluster_idr0080_index = str(packed_index.resolve())
            cluster_qc_export = str(qc_export.resolve())
            cluster_qc_annotations = str(cluster_qc_annotations_path.resolve())
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
            cluster_idr0080_index,
            str(packed_index.resolve()),
            known_hosts,
            cluster_qc_export=cluster_qc_export,
            local_qc_export=str(qc_export.resolve()),
            cluster_qc_annotations=cluster_qc_annotations,
            local_qc_annotations=str(local_qc_annotations_path.resolve()),
        )
        services.append(("cluster", cluster_arguments, cluster_environment))

    connector_arguments = [
        sys.executable,
        "-m",
        "timed_connector.cli",
        "serve",
        "--profiles",
        str(profiles),
        "--local-state-dir",
        str(runtime / "local-state"),
        "--port",
        str(arguments.connector_port),
    ]
    if arguments.built_ui:
        connector_arguments.extend(["--serve-ui", str(built_ui)])
    services.append(("connector", connector_arguments, None))

    if not arguments.built_ui:
        services.append(
            (
                "web",
                [
                    _pnpm(),
                    "-C",
                    "apps/web",
                    "dev",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(arguments.web_port),
                ],
                {
                    **os.environ,
                    "TIMED_CONNECTOR_ORIGIN": (f"http://127.0.0.1:{arguments.connector_port}"),
                },
            )
        )

    try:
        for name, service_arguments, process_environment in services:
            log = (runtime / f"{name}.log").open("wb")
            logs.append(log)
            process = _start(service_arguments, log, environment=process_environment)
            processes.append(process)
            if name == "cluster":
                _wait_port(CLUSTER_PORT, process)
            elif name == "connector":
                _wait_http(
                    f"http://127.0.0.1:{arguments.connector_port}/connector/session",
                    process,
                )
            else:
                _wait_http(f"http://127.0.0.1:{arguments.web_port}/", process)

        command = [
            _pnpm(),
            "-C",
            "apps/web",
            "run",
            "test:playwright",
            *(
                ["tests/e2e/real-timed-copy.spec.ts"]
                if real_timed_copy
                else ["tests/e2e/real-local-flow.spec.ts"]
                if real_local
                else playwright_arguments
            ),
        ]
        test_environment = os.environ.copy()
        browser_port = arguments.connector_port if arguments.built_ui else arguments.web_port
        test_environment["TIMED_WEB_ORIGIN"] = f"http://127.0.0.1:{browser_port}"
        if real_local_root:
            test_environment.update(
                {
                    "TIMED_REAL_LOCAL_ROOT_NAME": real_local_root.name,
                    "TIMED_REAL_LOCAL_STORE": str(real_local_store),
                }
            )
        if real_timed_copy:
            test_environment["TIMED_REAL_TIMED_COPY"] = "1"
        test_environment.pop("TIMED_E2E_LOCAL_ONLY", None)
        if arguments.local_only:
            test_environment["TIMED_E2E_LOCAL_ONLY"] = "1"
        result_code = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            env=test_environment,
        ).returncode
        for log in logs:
            log.flush()
        connector_log = (runtime / "connector.log").read_text(
            encoding="utf-8",
            errors="replace",
        )
        if result_code == 0 and "ERROR:    Exception in ASGI application" in connector_log:
            print("E2E connector log contains an unhandled ASGI exception", file=sys.stderr)
            result_code = 1
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
