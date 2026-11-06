"""HTTP error responses shared by the data-server routes.

Routes translate domain errors into these responses by hand. The per-route
differences below are deliberate and frozen until a separate API decision:

| Exception | Status | Where |
| --- | --- | --- |
| PathForbiddenError | 403 | every path-taking route |
| PathNotFoundError | 404 | every path-taking route |
| OmeZarrError | 422 | dataset summary and /zarr resources |
| OmeZarrError | 404 | dataset wells |
| PackedIndexError, PackedProfileError, PackedReadError | 422 | /api/fs, /api/datasets, /zarr |
| PackedMetadataError | 422 | /api/datasets metadata and matches |
| PackedMetadataError | 503 | /api/qc payloads |
| PackedFacadeNotFoundError, PackedMetadataNotFoundError | 404 | packed routes |
| PlateMetadataError | 422 | TIMED plate metadata |
| QcFieldNotFoundError | 404 | /api/qc/field |
| QcPayloadError, QcAnnotationUnavailableError | 503 | /api/qc |
| QcAnnotationValidationError | 422 | POST /api/qc/annotations |

Errors under /api/qc/ use the QC JSON response contract: gzip, a weak ETag and
private no-cache headers. Their ``blind`` parser accepts 1, true, on and yes
in any case, which differs from the routes' boolean query parsing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial

import anyio
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .paths import PathForbiddenError, PathNotFoundError
from .qc_http import qc_json_response


def forbidden(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def service_unavailable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, message)


def unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@contextmanager
def translate_path_errors() -> Iterator[None]:
    """Answer 403 for a forbidden path and 404 for a missing one."""
    try:
        yield
    except PathForbiddenError as exc:
        raise forbidden(exc) from exc
    except PathNotFoundError as exc:
        raise not_found(str(exc)) from exc


async def qc_error_response(request: Request, response: Response) -> Response:
    if not request.url.path.startswith("/api/qc/"):
        return response
    try:
        payload = json.loads(response.body)
    except (AttributeError, UnicodeError, json.JSONDecodeError):
        payload = {"error": "QC request failed"}
    blind = request.query_params.get("blind", "").casefold() in {
        "1",
        "true",
        "on",
        "yes",
    }
    state_identity = f"{response.status_code}:{response.body!r}"
    return await anyio.to_thread.run_sync(
        partial(
            qc_json_response,
            lambda: payload,
            export_id=f"{__version__}:error",
            endpoint=request.url.path,
            blind=blind,
            accept_encoding=request.headers.get("accept-encoding"),
            if_none_match=request.headers.get("if-none-match"),
            state=state_identity,
            status_code=response.status_code,
            extra_headers=dict(response.headers),
        )
    )


def install_error_handlers(app: FastAPI) -> None:
    """Route HTTP and validation errors through the QC error contract."""

    @app.exception_handler(StarletteHTTPException)
    async def timed_http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> Response:
        response = await http_exception_handler(request, exc)
        return await qc_error_response(request, response)

    @app.exception_handler(RequestValidationError)
    async def timed_validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> Response:
        response = await request_validation_exception_handler(request, exc)
        return await qc_error_response(request, response)
