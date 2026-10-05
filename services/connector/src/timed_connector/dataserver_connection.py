"""Shared parsing and verification for data-server connections."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from timed_dataserver import API_VERSION


class DataServerConnectionError(RuntimeError):
    """Raised when a data-server connection cannot be trusted or verified."""


@dataclass(frozen=True)
class DataServerConnection:
    host: str
    port: int
    token: str
    api_version: str


def parse_connection(
    value: Mapping[str, Any],
    *,
    source: str = "Connection file",
) -> DataServerConnection:
    """Parse the private fields needed to contact one data-server process."""
    try:
        connection = DataServerConnection(
            host=str(value["host"]),
            port=int(value["port"]),
            token=str(value["token"]),
            api_version=str(value["api_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataServerConnectionError(f"{source} is incomplete") from exc
    if (
        connection.port < 1
        or connection.port > 65535
        or not connection.host
        or not connection.token
    ):
        raise DataServerConnectionError(f"{source} has invalid values")
    return connection


async def verify_handshake(
    base_url: str,
    connection: DataServerConnection,
) -> dict[str, Any]:
    """Authenticate to a data server and verify its API major version."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{base_url}/api/handshake",
                headers={"Authorization": f"Bearer {connection.token}"},
            )
            response.raise_for_status()
            value = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise DataServerConnectionError(f"Data-server handshake failed: {exc}") from exc
    if not isinstance(value, dict):
        raise DataServerConnectionError("Data-server handshake was not a JSON object")
    expected_major = API_VERSION.split(".", 1)[0]
    actual_major = str(value.get("api_version", "")).split(".", 1)[0]
    if actual_major != expected_major:
        raise DataServerConnectionError(
            f"Incompatible data-server API {value.get('api_version')!r}; "
            f"expected major {expected_major}"
        )
    return value
