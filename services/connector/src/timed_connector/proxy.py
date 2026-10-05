"""Streaming reverse proxy from the browser to the active data server."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from .session import ConnectorSession, SessionError

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


async def proxy_request(
    request: Request,
    path: str,
    session: ConnectorSession,
) -> StreamingResponse | JSONResponse:
    """Proxy one request while keeping the private bearer token in memory."""
    try:
        base_url, token = session.proxy_target()
    except SessionError:
        return JSONResponse({"detail": "No data server is connected"}, status_code=503)

    query = request.url.query
    url = f"{base_url}/{path}"
    if query:
        url = f"{url}?{query}"
    request_headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in HOP_BY_HOP
        and name.lower() not in {"host", "authorization", "x-timed-client"}
    }
    request_headers["Authorization"] = f"Bearer {token}"
    client = httpx.AsyncClient(timeout=None)
    upstream_request = client.build_request(
        request.method,
        url,
        headers=request_headers,
        content=request.stream(),
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(
            {"detail": f"Data-server proxy request failed: {exc}"},
            status_code=502,
        )

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in HOP_BY_HOP
    }

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
