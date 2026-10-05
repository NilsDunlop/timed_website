#!/usr/bin/env python3
"""Minimal stateful Slurm command implementation for the TIMED test cluster."""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

ACTIVE_STATES = {"PENDING", "CONFIGURING", "RUNNING"}
OUTPUT_DIRECTIVE = re.compile(r"^#SBATCH\s+--output=(?P<path>.+)$", re.MULTILINE)


def _state_root() -> Path:
    configured = os.environ.get("TIMED_TEST_CLUSTER_STATE")
    if not configured:
        raise RuntimeError("TIMED_TEST_CLUSTER_STATE is not set")
    root = Path(configured).resolve()
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    return root


def _job_path(job_id: str) -> Path:
    if not job_id.isdigit():
        raise ValueError(f"Invalid job id: {job_id}")
    return _state_root() / "jobs" / f"{job_id}.json"


def _write_job(job_id: str, value: dict[str, Any]) -> None:
    path = _job_path(job_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def _read_job(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return value if isinstance(value, dict) else None


def _allocate_job_id() -> str:
    counter = _state_root() / "next_job_id"
    with counter.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read().strip()
        value = int(raw) if raw else 1000
        handle.seek(0)
        handle.truncate()
        handle.write(str(value + 1))
        handle.flush()
        os.fsync(handle.fileno())
        return str(value)


def _output_path(script: Path, job_id: str) -> Path:
    match = OUTPUT_DIRECTIVE.search(script.read_text(encoding="utf-8"))
    if match is None:
        return _state_root() / "jobs" / f"{job_id}.out"
    configured = Path(match.group("path").strip()).expanduser()
    return configured.with_name(configured.name.replace("%j", job_id)).resolve()


def _run_job(job_id: str, script: Path) -> int:
    delay = float(os.environ.get("TEST_CLUSTER_PENDING_SECONDS", "0"))
    if delay > 0:
        time.sleep(delay)
    job = _read_job(job_id)
    if job is None or job.get("state") == "CANCELLED":
        return 0
    job.update({"state": "RUNNING", "reason": "None", "node": "127.0.0.1"})
    _write_job(job_id, job)
    output = _output_path(script, job_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "SLURM_JOB_ID": job_id,
            "TIMED_NODE_HOST": "127.0.0.1",
        }
    )
    with output.open("ab") as log:
        child = subprocess.Popen(
            ["bash", str(script)],
            cwd=os.environ["HOME"],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        job["child_pid"] = child.pid
        _write_job(job_id, job)
        returncode = child.wait()
    current = _read_job(job_id)
    if current is not None and current.get("state") != "CANCELLED":
        current.update(
            {
                "state": "COMPLETED" if returncode == 0 else "FAILED",
                "reason": "None" if returncode == 0 else "NonZeroExitCode",
            }
        )
        _write_job(job_id, current)
    return returncode


def sbatch(arguments: list[str]) -> int:
    if "--parsable" not in arguments or not arguments:
        print("sbatch: only --parsable is supported", file=sys.stderr)
        return 2
    script = Path(arguments[-1]).expanduser().resolve()
    if not script.is_file():
        print(f"sbatch: script not found: {script}", file=sys.stderr)
        return 2
    job_id = _allocate_job_id()
    job = {
        "job_id": job_id,
        "state": "PENDING",
        "reason": "Resources",
        "node": "-",
        "script": str(script),
    }
    _write_job(job_id, job)
    runner = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "run", job_id, str(script)],
        cwd=os.environ["HOME"],
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    current = _read_job(job_id) or job
    current["runner_pid"] = runner.pid
    _write_job(job_id, current)
    print(job_id)
    return 0


def _argument(arguments: list[str], flag: str) -> str | None:
    try:
        return arguments[arguments.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def squeue(arguments: list[str]) -> int:
    selected = _argument(arguments, "-j")
    output_format = _argument(arguments, "-o") or "%i %T %r %N"
    paths = [_job_path(selected)] if selected else sorted((_state_root() / "jobs").glob("*.json"))
    for path in paths:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        if job.get("state") not in ACTIVE_STATES:
            continue
        line = output_format
        for token, value in (
            ("%i", job.get("job_id", "")),
            ("%T", job.get("state", "")),
            ("%r", job.get("reason", "None")),
            ("%N", job.get("node", "-")),
        ):
            line = line.replace(token, str(value))
        print(line)
    return 0


def scancel(arguments: list[str]) -> int:
    if not arguments:
        print("scancel: job id required", file=sys.stderr)
        return 2
    for job_id in arguments:
        job = _read_job(job_id)
        if job is None or job.get("state") not in ACTIVE_STATES:
            continue
        job.update({"state": "CANCELLED", "reason": "Cancelled"})
        _write_job(job_id, job)
        runner_pid = job.get("runner_pid")
        if isinstance(runner_pid, int):
            with suppress(ProcessLookupError):
                os.killpg(runner_pid, signal.SIGTERM)
    return 0


def main(command: str, arguments: list[str]) -> int:
    if command == "sbatch":
        return sbatch(arguments)
    if command == "squeue":
        return squeue(arguments)
    if command == "scancel":
        return scancel(arguments)
    if command == "run":
        return _run_job(arguments[0], Path(arguments[1]).resolve())
    raise ValueError(f"Unknown command: {command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
