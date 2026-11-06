"""HTTP caching, validator and byte-range responses for data-server resources."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Iterator
from datetime import UTC
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

from fastapi import HTTPException, Response, status
from fastapi.responses import StreamingResponse

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
STREAM_BLOCK_SIZE = 1024 * 1024
PRIVATE_CACHE_CONTROL = "private, max-age=3600"
PRIVATE_RANGE_CACHE_CONTROL = "private, no-store"


def range_bounds(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    match = RANGE_PATTERN.fullmatch(value.strip())
    if match is None or size == 0:
        raise ValueError("Invalid byte range")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("Invalid byte range")
    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        if start >= size or end < start:
            raise ValueError("Unsatisfiable byte range")
        return start, min(end, size - 1)
    suffix_length = int(end_text)
    if suffix_length <= 0:
        raise ValueError("Invalid byte range")
    return max(0, size - suffix_length), size - 1


def cache_headers(etag: str, *, modified: float | None = None) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": PRIVATE_CACHE_CONTROL,
        "ETag": etag,
    }
    if modified is not None:
        headers["Last-Modified"] = formatdate(modified, usegmt=True)
    return headers


def etag_matches(value: str | None, etag: str) -> bool:
    if value is None:
        return False
    expected = etag.removeprefix("W/")
    return any(
        candidate == "*" or candidate.removeprefix("W/") == expected
        for candidate in (part.strip() for part in value.split(","))
    )


def range_for_validator(
    range_header: str | None,
    if_range: str | None,
    etag: str,
    *,
    modified: float | None = None,
) -> str | None:
    if range_header is None or if_range is None:
        return range_header
    validator = if_range.strip()
    if validator.startswith('"') or validator.startswith("W/"):
        return range_header if not validator.startswith("W/") and validator == etag else None
    if modified is None:
        return None
    try:
        parsed = parsedate_to_datetime(validator)
        validator_time = (
            parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        ).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    return range_header if int(modified) <= int(validator_time) else None


def memory_response(
    content: bytes,
    content_type: str,
    range_header: str | None,
    if_none_match: str | None,
    if_range: str | None,
    *,
    etag: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    size = len(content)
    if etag is None:
        digest = hashlib.blake2s(content, digest_size=16).hexdigest()
        etag = f'"{digest}"'
    headers = cache_headers(etag)
    if extra_headers is not None:
        headers.update(extra_headers)
    if etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    range_header = range_for_validator(range_header, if_range, etag)
    try:
        bounds = range_bounds(range_header, size)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail=str(exc),
            headers={**headers, "Content-Range": f"bytes */{size}"},
        ) from exc
    if bounds is None:
        headers["Content-Length"] = str(size)
        return Response(content, media_type=content_type, headers=headers)
    start, end = bounds
    body = content[start : end + 1]
    headers["Cache-Control"] = PRIVATE_RANGE_CACHE_CONTROL
    headers.update(
        {
            "Content-Length": str(len(body)),
            "Content-Range": f"bytes {start}-{end}/{size}",
        }
    )
    return Response(
        body,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=content_type,
        headers=headers,
    )


def file_blocks(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining:
            block = handle.read(min(STREAM_BLOCK_SIZE, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def file_response(
    path: Path,
    range_header: str | None,
    if_none_match: str | None,
    if_range: str | None,
) -> Response:
    metadata = path.stat()
    size = metadata.st_size
    etag = f'"{metadata.st_mtime_ns:x}-{size:x}"'
    headers = cache_headers(etag, modified=metadata.st_mtime)
    if etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    range_header = range_for_validator(
        range_header,
        if_range,
        etag,
        modified=metadata.st_mtime,
    )
    try:
        bounds = range_bounds(range_header, size)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail=str(exc),
            headers={**headers, "Content-Range": f"bytes */{size}"},
        ) from exc
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if bounds is None:
        start, end = 0, size - 1
        response_status = status.HTTP_200_OK
    else:
        start, end = bounds
        response_status = status.HTTP_206_PARTIAL_CONTENT
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)
    if bounds is not None:
        headers["Cache-Control"] = PRIVATE_RANGE_CACHE_CONTROL
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        file_blocks(path, start, length),
        status_code=response_status,
        media_type=content_type,
        headers=headers,
    )
