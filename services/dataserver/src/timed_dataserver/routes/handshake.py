"""Handshake route: server identity, data roots and capabilities."""

from __future__ import annotations

import platform
import socket
from typing import Annotated

from fastapi import APIRouter, Depends

from .. import API_VERSION, __version__
from ..context import ServerContext, get_context
from ..models import Capabilities, HandshakeResponse

router = APIRouter()


@router.get("/api/handshake", response_model=HandshakeResponse)
async def handshake(ctx: Annotated[ServerContext, Depends(get_context)]) -> HandshakeResponse:
    return HandshakeResponse(
        api_version=API_VERSION,
        server_version=__version__,
        environment=ctx.settings.environment,
        hostname=socket.gethostname(),
        data_roots=[str(root) for root in ctx.guard.roots],
        python_version=platform.python_version(),
        capabilities=Capabilities(
            plate_metadata=ctx.metadata_catalog is not None,
            packed_datasets=True,
            qc_review=any(config.export_readable for config in ctx.qc_paths.values()),
            qc_annotations_writable=any(
                config.annotations_writable for config in ctx.qc_paths.values()
            ),
        ),
    )
