"""FastAPI application for the local TIMED connector."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .models import (
    ConnectRequest,
    DisconnectRequest,
    Profile,
    RespondRequest,
    SessionSnapshot,
)
from .profiles import ProfileStore
from .proxy import proxy_request
from .session import ConnectorSession, SessionError


def _client_header(
    x_timed_client: Annotated[str | None, Header(alias="X-Timed-Client")] = None,
) -> None:
    if x_timed_client != "web":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Timed-Client: web is required",
        )


def create_app(
    profiles: ProfileStore,
    session: ConnectorSession,
    *,
    port: int = 8700,
    serve_ui: Path | None = None,
) -> FastAPI:
    """Create the loopback-only connector API."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        limits = httpx.Limits(
            max_connections=32,
            max_keepalive_connections=16,
            keepalive_expiry=30.0,
        )
        async with httpx.AsyncClient(
            timeout=None,
            limits=limits,
            trust_env=False,
        ) as proxy_client:
            app.state.proxy_client = proxy_client
            try:
                yield
            finally:
                await session.disconnect(cancel_job=True)

    app = FastAPI(
        title="TIMED connector",
        version=__version__,
        lifespan=lifespan,
    )
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @app.middleware("http")
    async def validate_host(request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.headers.get("host", "").lower()
        if host not in allowed_hosts:
            return Response(
                content='{"detail":"Invalid Host header"}',
                status_code=400,
                media_type="application/json",
            )
        return await call_next(request)

    @app.get("/connector/profiles", response_model=list[Profile])
    async def list_profiles() -> list[Profile]:
        return profiles.public()

    @app.get("/connector/session", response_model=SessionSnapshot)
    async def get_session() -> SessionSnapshot:
        return session.snapshot()

    @app.post(
        "/connector/connect",
        response_model=SessionSnapshot,
        status_code=202,
        dependencies=[Depends(_client_header)],
    )
    async def connect(request: ConnectRequest) -> SessionSnapshot:
        try:
            return await session.connect(request.profile_id)
        except SessionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/connector/respond",
        response_model=SessionSnapshot,
        dependencies=[Depends(_client_header)],
    )
    async def respond(request: RespondRequest) -> SessionSnapshot:
        try:
            return await session.respond(request)
        except SessionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/connector/disconnect",
        response_model=SessionSnapshot,
        dependencies=[Depends(_client_header)],
    )
    async def disconnect(request: DisconnectRequest) -> SessionSnapshot:
        return await session.disconnect(cancel_job=request.cancel_job)

    @app.api_route(
        "/backend/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    async def backend(request: Request, path: str):
        return await proxy_request(
            request,
            path,
            session,
            request.app.state.proxy_client,
        )

    if serve_ui is not None:
        ui = serve_ui.expanduser().resolve()
        if not ui.is_dir():
            raise ValueError(f"UI build directory does not exist: {ui}")
        app.mount("/", StaticFiles(directory=ui, html=True), name="ui")

    return app
