"""HTTP response helpers used only by the QC API routes."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence

from fastapi import Response, status

QC_CACHE_CONTROL = "private, no-cache"


def qc_etag(
    export_id: str,
    endpoint: str,
    *,
    blind: bool,
    state: str | None = None,
) -> str:
    """Build a weak validator from the complete QC representation identity.

    ``endpoint`` must include selectors that alter a representation (for example,
    plate, well, and site for a field response). Mutable status responses must
    also provide a ``state`` discriminator for readability or error changes.
    """
    identity = json.dumps(
        [export_id, endpoint, "blind" if blind else "core", state],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.blake2s(identity, digest_size=16).hexdigest()
    return f'W/"qc-{digest}"'


def _etag_matches(value: str | None, etag: str) -> bool:
    if value is None:
        return False
    expected = etag.removeprefix("W/")
    return any(
        candidate == "*" or candidate.removeprefix("W/") == expected
        for candidate in (part.strip() for part in value.split(","))
    )


def _accepts_gzip(value: str | None) -> bool:
    if value is None:
        return False
    wildcard_quality: float | None = None
    gzip_quality: float | None = None
    for item in value.split(","):
        parts = [part.strip() for part in item.split(";")]
        encoding = parts[0].casefold()
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, raw_value = parameter.partition("=")
            if separator and name.strip().casefold() == "q":
                try:
                    quality = float(raw_value.strip())
                except ValueError:
                    quality = 0.0
        quality = min(1.0, max(0.0, quality))
        if encoding == "gzip":
            gzip_quality = quality
        elif encoding == "*":
            wildcard_quality = quality
    selected = gzip_quality if gzip_quality is not None else wildcard_quality
    return selected is not None and selected > 0


def _json_safe(value: object) -> object:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return value


def serialize_qc_json(value: object) -> bytes:
    """Serialize a payload as stable, standards-compliant UTF-8 JSON."""
    return json.dumps(
        _json_safe(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def qc_json_response(
    payload: Callable[[], object],
    *,
    export_id: str,
    endpoint: str,
    blind: bool,
    accept_encoding: str | None = None,
    if_none_match: str | None = None,
    state: str | None = None,
    status_code: int = status.HTTP_200_OK,
    extra_headers: Mapping[str, str] | None = None,
) -> Response:
    """Return one cache-aware QC JSON response.

    This helper is synchronous so callers can run payload construction,
    serialization, and compression together in a worker thread. It is not
    middleware and therefore cannot change image-route responses.
    """
    etag = qc_etag(export_id, endpoint, blind=blind, state=state)
    blocked_headers = {
        "cache-control",
        "content-encoding",
        "content-length",
        "etag",
        "vary",
    }
    headers = {
        key: value
        for key, value in (extra_headers or {}).items()
        if key.casefold() not in blocked_headers
    }
    headers.update({
        "Cache-Control": QC_CACHE_CONTROL,
        "ETag": etag,
        "Vary": "Accept-Encoding",
    })
    if 200 <= status_code < 300 and _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    content = serialize_qc_json(payload())
    if _accepts_gzip(accept_encoding):
        content = gzip.compress(content, compresslevel=6, mtime=0)
        headers["Content-Encoding"] = "gzip"
    return Response(
        content=content,
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )
