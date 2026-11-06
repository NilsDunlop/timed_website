"""Byte ranges, validators and the suffix ranges the browser's Zarr store sends."""

from __future__ import annotations

from email.utils import formatdate
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.http_cache import range_bounds, range_for_validator

from scripts import make_test_data
from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "http-ranges-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INVALID = "Invalid byte range"
UNSATISFIABLE = "Unsatisfiable byte range"


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        (None, 10, None),
        ("bytes=0-4", 10, (0, 4)),
        ("bytes=5-", 10, (5, 9)),
        ("bytes=3-100", 10, (3, 9)),
        (" bytes=1-2 ", 10, (1, 2)),
        ("bytes=-3", 10, (7, 9)),
        ("bytes=-20", 10, (0, 9)),
        ("bytes=-0", 10, INVALID),
        ("bytes=-", 10, INVALID),
        ("items=0-1", 10, INVALID),
        ("bytes=0-1,3-4", 10, INVALID),
        ("bytes=0-0", 0, INVALID),
        ("bytes=10-", 10, UNSATISFIABLE),
        ("bytes=5-3", 10, UNSATISFIABLE),
    ],
)
def test_range_bounds_table(
    header: str | None, size: int, expected: tuple[int, int] | str | None
) -> None:
    if isinstance(expected, str):
        with pytest.raises(ValueError, match=f"^{expected}$"):
            range_bounds(header, size)
    else:
        assert range_bounds(header, size) == expected


MODIFIED = 1_760_000_000.5


@pytest.mark.parametrize(
    ("range_header", "if_range", "etag", "modified", "expected"),
    [
        (None, '"tag"', '"tag"', None, None),
        ("bytes=0-1", None, '"tag"', None, "bytes=0-1"),
        ("bytes=0-1", '"tag"', '"tag"', None, "bytes=0-1"),
        ("bytes=0-1", ' "tag" ', '"tag"', None, "bytes=0-1"),
        ("bytes=0-1", '"other"', '"tag"', None, None),
        ("bytes=0-1", 'W/"tag"', 'W/"tag"', None, None),
        ("bytes=0-1", formatdate(MODIFIED, usegmt=True), '"tag"', None, None),
        ("bytes=0-1", formatdate(MODIFIED, usegmt=True), '"tag"', MODIFIED, "bytes=0-1"),
        ("bytes=0-1", formatdate(MODIFIED + 60, usegmt=True), '"tag"', MODIFIED, "bytes=0-1"),
        ("bytes=0-1", formatdate(MODIFIED - 60, usegmt=True), '"tag"', MODIFIED, None),
        ("bytes=0-1", "Wed, 21 Oct 2015 07:28:00", '"tag"', MODIFIED, None),
        ("bytes=0-1", "not a date", '"tag"', MODIFIED, None),
    ],
)
def test_range_for_validator_table(
    range_header: str | None,
    if_range: str | None,
    etag: str,
    modified: float | None,
    expected: str | None,
) -> None:
    assert range_for_validator(range_header, if_range, etag, modified=modified) == expected


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory, write_audit: WriteAudit) -> Path:
    root = tmp_path_factory.mktemp("http-ranges-data")
    generate_test_data(root, include_packed=False)
    make_test_data._write_packed_fixture(root)
    write_audit.watch(root)
    return root.resolve()


def _link(client: TestClient, path: Path) -> str:
    response = client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.parametrize("suffix", [1, 16, 10_000_000])
def test_suffix_and_validator_ranges_on_timed_and_packed_resources(
    data_root: Path, tmp_path: Path, suffix: int
) -> None:
    settings = ServerSettings(data_roots=(data_root,), state_dir=tmp_path, environment="local")
    shard = data_root / "test_plate.zarr" / "A01_s1.zarr" / "c" / "0" / "0" / "0" / "0"
    index = data_root / make_test_data.GENERATED_PACKED_DIRECTORY
    index = index / make_test_data.PACKED_FIXTURE_DIRECTORY / make_test_data.PACKED_INDEX_NAME

    with TestClient(create_app(settings, TOKEN)) as client:
        timed = _link(client, data_root / "test_plate.zarr")
        packed = _link(client, index / "idr0080-A549" / "14610")
        resources = {
            "timed shard": (f"/zarr/{timed}/A01_s1.zarr/s0/c/0/0/0/0", shard.read_bytes()),
            "packed chunk": (f"/zarr/{packed}/A01_s1.zarr/s0/c/0/0/0", None),
        }
        for label, (url, expected) in resources.items():
            full = client.get(url, headers=AUTH)
            assert full.status_code == 200, label
            content = full.content if expected is None else expected
            assert full.content == content, label
            size = len(content)
            start = max(0, size - suffix)
            etag = full.headers["etag"]

            ranged = client.get(url, headers={**AUTH, "Range": f"bytes=-{suffix}"})
            current = client.get(
                url, headers={**AUTH, "Range": f"bytes=-{suffix}", "If-Range": etag}
            )
            stale = client.get(
                url, headers={**AUTH, "Range": f"bytes=-{suffix}", "If-Range": '"stale"'}
            )

            for response in (ranged, current):
                assert response.status_code == 206, label
                assert response.content == content[start:], label
                assert response.headers["content-range"] == f"bytes {start}-{size - 1}/{size}"
                assert response.headers["cache-control"] == "private, no-store"
            assert stale.status_code == 200, label
            assert stale.content == content, label
            assert stale.headers["cache-control"] == "private, max-age=3600"
