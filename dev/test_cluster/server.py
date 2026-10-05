#!/usr/bin/env python3
"""Run the local AsyncSSH/Slurm-compatible TIMED test cluster."""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import stat
from pathlib import Path

import asyncssh

ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT / ".state"


class TestClusterServer(asyncssh.SSHServer):
    """Two-prompt keyboard-interactive server with local forwarding enabled."""

    def begin_auth(self, username: str) -> bool:
        del username
        return True

    def kbdint_auth_supported(self) -> bool:
        return True

    def get_kbdint_challenge(
        self, username: str, lang: str, submethods: str
    ) -> tuple[str, str, str, list[tuple[str, bool]]] | bool:
        del lang, submethods
        if username != "dev":
            return False
        return (
            "TIMED test cluster",
            "",
            "",
            [("Password:", False), ("Verification code:", False)],
        )

    def validate_kbdint_response(self, username: str, responses: list[str]) -> bool:
        return (
            username == "dev"
            and list(responses)
            == [
                os.environ.get("TEST_CLUSTER_PASSWORD", "dev-password"),
                os.environ.get("TEST_CLUSTER_VERIFICATION_CODE", "123456"),
            ]
        )

    def connection_requested(
        self,
        dest_host: str,
        dest_port: int,
        orig_host: str,
        orig_port: int,
    ) -> bool:
        del orig_host, orig_port
        return dest_host in ("127.0.0.1", "localhost") and 0 < dest_port <= 65535


def _host_key(state: Path) -> Path:
    path = state / "ssh_host_ed25519_key"
    if not path.exists():
        key = asyncssh.generate_private_key("ssh-ed25519")
        path.write_bytes(key.export_private_key("openssh"))
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


async def _run_process(
    process: asyncssh.SSHServerProcess[str],
    *,
    home: Path,
    state: Path,
) -> None:
    if not process.command:
        process.stderr.write("Interactive shells are not supported\n")
        process.exit(2)
        return
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "PATH": f"{ROOT / 'bin'}{os.pathsep}{environment.get('PATH', '')}",
            "TIMED_TEST_CLUSTER_STATE": str(state),
        }
    )
    child = await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        f"export PATH={shlex.quote(str(ROOT / 'bin'))}:$PATH; {process.command}",
        cwd=home,
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await child.communicate()
    process.stdout.write(stdout.decode(errors="replace"))
    process.stderr.write(stderr.decode(errors="replace"))
    process.exit(child.returncode or 0)


async def run_server(host: str, port: int, state: Path) -> None:
    state = state.resolve()
    home = state / "home"
    home.mkdir(parents=True, exist_ok=True)
    (state / "jobs").mkdir(parents=True, exist_ok=True)
    host_key = _host_key(state)

    async def process_factory(process: asyncssh.SSHServerProcess[str]) -> None:
        await _run_process(process, home=home, state=state)

    listener = await asyncssh.create_server(
        TestClusterServer,
        host,
        port,
        server_host_keys=[host_key],
        process_factory=process_factory,
        encoding="utf-8",
        sftp_factory=lambda channel: asyncssh.SFTPServer(
            channel,
            chroot=os.fsencode(home),
        ),
    )
    print(f"TIMED test cluster listening on {host}:{listener.get_port()}", flush=True)
    await listener.wait_closed()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        asyncio.run(run_server(args.host, args.port, args.state_dir))
    except (OSError, asyncssh.Error) as exc:
        print(f"test cluster error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
