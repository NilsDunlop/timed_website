"""Command-line interface for the local TIMED connector."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, cast

import httpx
import uvicorn

from .app import create_app
from .hpc_backend import AsyncSSHHPCBackend
from .local_backend import LocalBackend
from .models import ExistingJobChoice, RespondRequest
from .profiles import ProfileError, ProfileStore, load_profiles, validate_profile
from .session import ConnectorSession, SessionBackend


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="timed-connector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="run the connector HTTP API")
    serve_parser.add_argument("--port", type=int, default=8700)
    serve_parser.add_argument(
        "--profiles",
        type=Path,
        default=Path.home() / ".timed" / "profiles.yaml",
    )
    serve_parser.add_argument("--serve-ui", type=Path)
    serve_parser.add_argument(
        "--local-state-dir",
        type=Path,
        default=Path.home() / ".timed" / "local",
    )

    try_parser = subparsers.add_parser("try", help="connect using the terminal")
    try_parser.add_argument("profile_id")
    try_parser.add_argument(
        "--profiles",
        type=Path,
        default=Path.home() / ".timed" / "profiles.yaml",
    )
    try_parser.add_argument("--list", dest="list_path")
    try_parser.add_argument("--keep-job", action="store_true")
    try_parser.add_argument(
        "--local-state-dir",
        type=Path,
        default=Path.home() / ".timed" / "local",
    )

    check_parser = subparsers.add_parser(
        "check-profile", help="validate a profile without connecting"
    )
    check_parser.add_argument("profile_id")
    check_parser.add_argument(
        "--profiles",
        type=Path,
        default=Path.home() / ".timed" / "profiles.yaml",
    )
    subparsers.add_parser("export-openapi", help="print the OpenAPI document as JSON")
    return parser


async def _api_call(
    session: ConnectorSession,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    base_url, token = session.proxy_target()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method,
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})},
            **kwargs,
        )
    response.raise_for_status()
    return response


def _chunk_path(summary: dict[str, Any]) -> str | None:
    image = summary.get("first_image") if summary.get("kind") == "plate" else summary
    if not isinstance(image, dict):
        return None
    image_path = str(image.get("path") or "")
    levels = image.get("levels")
    if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
        return None
    level_path = str(levels[0].get("path") or "")
    shape = levels[0].get("shape")
    if not isinstance(shape, list):
        return None
    zeros = "/".join("0" for _ in shape)
    components = [part for part in (image_path, level_path) if part]
    prefix = str(PurePosixPath(*components)) if components else ""
    if int(image.get("zarr_format", 2)) == 3:
        return f"{prefix}/c/{zeros}" if prefix else f"c/{zeros}"
    return f"{prefix}/{zeros}" if prefix else zeros


def _first_plate(entries: Any) -> dict[str, Any] | None:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ome_zarr = entry.get("ome_zarr")
        if isinstance(ome_zarr, dict) and ome_zarr.get("kind") == "plate":
            return entry
    return None


def _export_openapi() -> None:
    profiles = ProfileStore([], Path.cwd() / ".timed" / "openapi-profiles.yaml")
    backend = cast(SessionBackend, object())
    session = ConnectorSession(profiles, backend)
    document = create_app(profiles, session).openapi()
    print(json.dumps(document, separators=(",", ":"), sort_keys=True))


async def _terminal_try(args: argparse.Namespace) -> int:
    store = load_profiles(args.profiles)
    profile = validate_profile(store, args.profile_id)
    backend = AsyncSSHHPCBackend(store.source)
    session = ConnectorSession(
        store,
        backend,
        local_backend=LocalBackend(state_dir=args.local_state_dir),
    )
    printed_logs = 0
    last_state = ""
    await session.connect(profile.id)
    try:
        while True:
            snapshot = session.snapshot()
            if snapshot.state != last_state:
                print(f"state: {snapshot.state}")
                last_state = snapshot.state
            for line in snapshot.logs[printed_logs:]:
                print(f"  {line}")
            printed_logs = len(snapshot.logs)
            if snapshot.state == "host_key_confirm":
                print(f"SSH host key: {snapshot.host_key_fingerprint}")
                answer = input("Accept this host key? [y/N] ").strip().lower()
                await session.respond(RespondRequest(accept_host_key=answer in ("y", "yes")))
            elif snapshot.state == "awaiting_input":
                answers = [
                    input(prompt.prompt) if prompt.echo else getpass.getpass(prompt.prompt)
                    for prompt in snapshot.prompts
                ]
                await session.respond(RespondRequest(answers=answers))
                answers.clear()
            elif snapshot.state == "choose_existing_job":
                for job in snapshot.existing_jobs:
                    print(f"existing job {job.job_id}: {job.state} {job.node or ''}".rstrip())
                job_id = input("Job id: ").strip()
                action = input("Reuse or cancel? [reuse/cancel] ").strip().lower()
                await session.respond(
                    RespondRequest(
                        existing_job=ExistingJobChoice(action=action, job_id=job_id)
                    )
                )
            elif snapshot.state == "error":
                print(f"error: {snapshot.message}", file=sys.stderr)
                if snapshot.hint:
                    print(f"hint: {snapshot.hint}", file=sys.stderr)
                return 1
            elif snapshot.state == "connected":
                break
            await asyncio.sleep(0.1)

        snapshot = session.snapshot()
        print(json.dumps(snapshot.handshake, indent=2))
        list_path = args.list_path or profile.data_roots[0]
        listing_response = await _api_call(
            session,
            "GET",
            "/api/fs/list",
            params={"path": list_path},
        )
        listing = listing_response.json()
        print(json.dumps(listing, indent=2))
        plate = _first_plate(listing.get("entries"))
        if plate is not None:
            linked = (
                await _api_call(
                    session,
                    "POST",
                    "/api/datasets",
                    json={"path": plate["path"]},
                )
            ).json()
            summary = (
                await _api_call(
                    session,
                    "GET",
                    f"/api/datasets/{linked['id']}/summary",
                )
            ).json()
            print(json.dumps(summary, indent=2))
            chunk_path = _chunk_path(summary)
            if chunk_path:
                chunk = await _api_call(
                    session,
                    "GET",
                    f"/zarr/{linked['id']}/{chunk_path}",
                    headers={"Range": "bytes=0-31"},
                )
                print(f"fetched chunk bytes: {len(chunk.content)}")
        return 0
    finally:
        await session.disconnect(cancel_job=not args.keep_job)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "export-openapi":
            _export_openapi()
            return 0
        store = load_profiles(args.profiles)
        if args.command == "check-profile":
            profile = validate_profile(store, args.profile_id)
            print(profile.model_dump_json(indent=2))
            return 0
        if args.command == "try":
            return asyncio.run(_terminal_try(args))

        if not 1 <= args.port <= 65535:
            raise ProfileError("Port must be between 1 and 65535")
        backend = AsyncSSHHPCBackend(store.source)
        session = ConnectorSession(
            store,
            backend,
            local_backend=LocalBackend(state_dir=args.local_state_dir),
        )
        app = create_app(store, session, port=args.port, serve_ui=args.serve_ui)
        uvicorn.run(app, host="127.0.0.1", port=args.port)
        return 0
    except (ProfileError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
