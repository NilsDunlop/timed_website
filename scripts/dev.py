#!/usr/bin/env python3
"""Run the connector and Astro development servers as one process group."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles",
        type=Path,
        help=(
            "read only this profiles file; by default the connector reads "
            "config/profiles.yaml plus the optional ~/.timed/profiles.local.yaml"
        ),
    )
    parser.add_argument("--port", type=int, default=8700)
    return parser


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)


def main() -> int:
    args = _parser().parse_args()
    pnpm = "pnpm.cmd" if os.name == "nt" else "pnpm"
    options: dict[str, object] = {"cwd": REPOSITORY_ROOT}
    if os.name != "nt":
        options["start_new_session"] = True
    profiles = [] if args.profiles is None else ["--profiles", str(args.profiles)]
    connector = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "timed_connector.cli",
            "serve",
            "--port",
            str(args.port),
            *profiles,
        ],
        **options,
    )
    web = subprocess.Popen([pnpm, "-C", "apps/web", "dev"], **options)
    processes = (connector, web)
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        return next(
            (int(process.returncode) for process in processes if process.returncode is not None),
            0,
        )
    except KeyboardInterrupt:
        return 130
    finally:
        for process in processes:
            _terminate(process)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
