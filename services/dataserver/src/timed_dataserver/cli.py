"""Command-line interface and process lifecycle for the TIMED data server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from . import API_VERSION, __version__
from .app import ServerSettings, create_app
from .qc_config import QcConfigurationError, parse_qc_path_configs
from .registry import atomic_json_write


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="timed-dataserver")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="run the authenticated data server")
    serve_parser.add_argument("--host", required=True)
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument(
        "--data-root",
        action="append",
        required=True,
        type=Path,
        help="allowed data root; may be repeated",
    )
    serve_parser.add_argument(
        "--metadata-root",
        type=Path,
        help="optional directory containing acquisition JSON and per-plate CSV metadata",
    )
    serve_parser.add_argument("--connection-file", required=True, type=Path)
    serve_parser.add_argument("--environment", choices=("local", "hpc"), required=True)
    serve_parser.add_argument("--state-dir", type=Path, default=Path.home() / ".timed")
    serve_parser.add_argument(
        "--packed-cache-mb",
        type=int,
        default=1024,
        help="decoded packed-field cache size in MiB (default: 1024)",
    )
    serve_parser.add_argument(
        "--qc-export",
        action="append",
        default=[],
        metavar="INDEX=FOLDER",
        help="QC export folder keyed by a packed index path; may be repeated",
    )
    serve_parser.add_argument(
        "--qc-annotations",
        action="append",
        default=[],
        metavar="INDEX=FOLDER",
        help="QC annotations folder keyed by a packed index path; may be repeated",
    )

    subparsers.add_parser("export-openapi", help="print the OpenAPI document as JSON")
    return parser


def _bound_socket(host: str, port: int) -> socket.socket:
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    flags = socket.AI_PASSIVE
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=flags)
    if not addresses:
        raise OSError(f"Could not resolve listen host: {host}")
    family, socket_type, protocol, _canonical_name, address = addresses[0]
    listener = socket.socket(family, socket_type, protocol)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(address)
    except BaseException:
        listener.close()
        raise
    return listener


async def serve(
    settings: ServerSettings,
    *,
    host: str,
    port: int,
    connection_file: Path,
    token: str | None = None,
    on_started: Callable[[uvicorn.Server], None] | None = None,
) -> None:
    """Run Uvicorn and maintain its atomic connection file."""
    bearer_token = token or secrets.token_urlsafe(32)
    app = create_app(settings, bearer_token)
    listener = _bound_socket(host, port)
    actual_port = int(listener.getsockname()[1])
    config = uvicorn.Config(
        app,
        host=host,
        port=actual_port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    wrote_connection_file = False
    try:
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("Data server stopped before startup completed")
            await asyncio.sleep(0.01)
        atomic_json_write(
            connection_file.expanduser(),
            {
                "api_version": API_VERSION,
                "server_version": __version__,
                "host": host,
                "port": actual_port,
                "token": bearer_token,
                "pid": os.getpid(),
                "started_at": datetime.now(UTC).isoformat(),
                "environment": settings.environment,
            },
        )
        wrote_connection_file = True
        if on_started is not None:
            on_started(server)
        await server_task
    finally:
        server.should_exit = True
        if not server_task.done():
            await server_task
        listener.close()
        if wrote_connection_file:
            connection_file.expanduser().unlink(missing_ok=True)


def export_openapi() -> None:
    """Print a deterministic OpenAPI document without starting a server."""
    settings = ServerSettings(
        data_roots=(Path.cwd(),),
        state_dir=Path.cwd() / ".timed" / "openapi",
        environment="local",
    )
    document = create_app(settings, token="openapi-placeholder").openapi()
    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the CLI."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "export-openapi":
        export_openapi()
        return 0

    try:
        qc_paths = parse_qc_path_configs(args.qc_export, args.qc_annotations)
    except QcConfigurationError as exc:
        parser.error(str(exc))
    settings = ServerSettings(
        data_roots=tuple(args.data_root),
        state_dir=args.state_dir,
        environment=args.environment,
        metadata_root=args.metadata_root,
        packed_cache_mb=args.packed_cache_mb,
        qc_paths=qc_paths,
    )
    if settings.packed_cache_mb <= 0:
        raise SystemExit("--packed-cache-mb must be greater than zero")
    try:
        asyncio.run(
            serve(
                settings,
                host=args.host,
                port=args.port,
                connection_file=args.connection_file,
            )
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
