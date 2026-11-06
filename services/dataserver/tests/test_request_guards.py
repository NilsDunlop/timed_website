"""Request guards that every data-server route must keep: token, roots and stores."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app

from scripts import make_test_data
from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "request-guards-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SENTINEL = b"request-guard sentinel outside every linked store\n"


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory, write_audit: WriteAudit) -> Path:
    root = tmp_path_factory.mktemp("request-guards-data")
    generate_test_data(root, include_packed=False)
    (root / "sentinel.txt").write_bytes(SENTINEL)
    write_audit.watch(root)
    return root


def _client(root: Path | tuple[Path, ...], state_dir: Path) -> TestClient:
    roots = root if isinstance(root, tuple) else (root,)
    settings = ServerSettings(data_roots=roots, state_dir=state_dir, environment="local")
    return TestClient(create_app(settings, TOKEN))


def _link(client: TestClient, path: Path) -> str:
    response = client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_every_route_requires_the_bearer_token(tmp_path: Path) -> None:
    app = create_app(
        ServerSettings(data_roots=(tmp_path,), state_dir=tmp_path / "state", environment="local"),
        TOKEN,
    )
    # Included routers appear in app.routes as one lazy entry each; this public
    # iterator yields every effective route, in matching order.
    routes = list(iter_route_contexts(app.routes))
    assert routes
    assert all(isinstance(route.original_route, APIRoute) for route in routes)
    requests = sorted(
        (method, re.sub(r"\{[^}]+\}", "fixture", route.path))
        for route in routes
        for method in route.methods
    )
    # Every documented operation is checked, and so is the schema-hidden HEAD route.
    operations = [
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
    ]
    assert len(requests) == len(operations) + 1
    assert ("HEAD", "/zarr/fixture/fixture") in requests

    with TestClient(app) as client:
        for method, path in requests:
            for headers in (
                {},
                {"Authorization": "Bearer wrong-token"},
                {"Authorization": f"Basic {TOKEN}"},
            ):
                response = client.request(method, path, headers=headers)
                assert response.status_code == 401, (method, path, headers)
                assert response.headers["www-authenticate"] == "Bearer", (method, path)


def _drive_forms(base: Path) -> tuple[str, str]:
    """Return a same-drive and an other-drive absolute form for ``base``."""
    if os.name == "nt":
        drive = base.drive
        other = "Z:" if drive.upper() != "Z:" else "Y:"
        return drive, other
    return "C:", "D:"


def test_zarr_resource_paths_cannot_escape_the_linked_store(
    data_root: Path, tmp_path: Path
) -> None:
    same_drive, other_drive = _drive_forms(data_root)
    sentinel = (data_root / "sentinel.txt").as_posix().split(":", 1)[-1].lstrip("/")
    parent = (403, "Parent path segments are not allowed")
    posix_only = (403, "Store paths must be relative POSIX paths")
    attempts: list[tuple[str, tuple[int, str]]] = [
        ("%2e%2e/sentinel.txt", parent),
        ("..%2fsentinel.txt", parent),
        ("%2e%2e%2f%2e%2e%2fsentinel.txt", parent),
        ("..%5csentinel.txt", parent),
        ("A01_s1.zarr/s0/%2e%2e/%2e%2e/%2e%2e/sentinel.txt", parent),
        ("s0%5czarr.json", posix_only),
        ("%2fetc%2fpasswd", posix_only),
        (f"{same_drive}%5c{sentinel}", posix_only),
        (
            f"{same_drive}/{sentinel}",
            (404, f"Path does not exist: {same_drive}/{sentinel}"),
        ),
        (
            f"{other_drive}/{sentinel}",
            (403, "Path escapes the linked dataset")
            if os.name == "nt"
            else (404, f"Path does not exist: {other_drive}/{sentinel}"),
        ),
    ]

    with _client(data_root, tmp_path / "state") as client:
        for store in ("test_plate.zarr", "test_image_v04.zarr", "test_image_v05.zarr"):
            dataset_id = _link(client, data_root / store)
            for resource, (expected_status, expected_detail) in attempts:
                for method in ("GET", "HEAD"):
                    response = client.request(
                        method,
                        f"/zarr/{dataset_id}/{resource}",
                        headers={**AUTH, "Range": "bytes=0-"},
                    )
                    assert response.status_code == expected_status, (store, resource, method)
                    assert SENTINEL not in response.content
                    if method == "GET":
                        assert response.json() == {"detail": expected_detail}, (store, resource)


def _path_routes(dataset_id: str) -> tuple[tuple[str, dict[str, str]], ...]:
    return (
        (f"/api/datasets/{dataset_id}/summary", {}),
        (f"/api/datasets/{dataset_id}/wells/A01", {}),
        (f"/api/datasets/{dataset_id}/metadata", {}),
        (f"/api/datasets/{dataset_id}/matches", {"well": "A01", "by": "gene"}),
        (f"/zarr/{dataset_id}/zarr.json", {}),
    )


def _expect_everywhere(
    client: TestClient,
    dataset_id: str,
    status: int,
    detail: str,
) -> None:
    for path, params in _path_routes(dataset_id):
        response = client.get(path, headers=AUTH, params=params)
        assert (response.status_code, response.json()) == (status, {"detail": detail}), path


def test_linked_records_are_revalidated_against_current_roots(
    data_root: Path, tmp_path: Path
) -> None:
    first_root = tmp_path / "first-root"
    second_root = tmp_path / "second-root"
    first_root.mkdir()
    second_root.mkdir()
    deleted_store = Path(
        shutil.copytree(data_root / "test_image_v05.zarr", first_root / "gone.zarr")
    )
    moved_store = Path(
        shutil.copytree(data_root / "test_image_v05.zarr", second_root / "elsewhere.zarr")
    )
    state = tmp_path / "shared-state"

    with _client((first_root, second_root), state) as client:
        deleted_id = _link(client, deleted_store)
        outside_id = _link(client, moved_store)

    deleted_record = deleted_store.resolve()
    shutil.rmtree(deleted_store)
    with _client(first_root, state) as client:
        _expect_everywhere(client, "0" * 32, 404, "Dataset is not linked")
        _expect_everywhere(client, outside_id, 403, "Path is outside the configured data roots")
        _expect_everywhere(client, deleted_id, 404, f"Path does not exist: {deleted_record}")
        listed = client.get("/api/datasets", headers=AUTH)

    assert listed.status_code == 200
    assert {record["id"] for record in listed.json()["datasets"]} == {deleted_id, outside_id}


def test_linked_packed_plate_is_revalidated_against_the_current_index(tmp_path: Path) -> None:
    packed_root = make_test_data._write_packed_fixture(tmp_path / "packed")
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()

    with _client(packed_root, tmp_path / "state") as client:
        dataset_id = _link(client, index / "idr0080-A549" / "14610")
        assert client.get(f"/api/datasets/{dataset_id}/summary", headers=AUTH).status_code == 200
        table = pq.read_table(index)
        pq.write_table(table.filter(pc.not_equal(table["plate"], 14610)), index)
        _expect_everywhere(client, dataset_id, 404, "Unknown packed plate: 14610")
        index.unlink()
        _expect_everywhere(client, dataset_id, 404, f"Path does not exist: {index}")


def test_link_rejects_outside_and_missing_paths(data_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.zarr"
    shutil.copytree(data_root / "test_image_v05.zarr", outside)
    missing = data_root / "missing.zarr"

    with _client(data_root, tmp_path / "state") as client:
        responses = {
            "outside": client.post("/api/datasets", headers=AUTH, json={"path": str(outside)}),
            "missing": client.post("/api/datasets", headers=AUTH, json={"path": str(missing)}),
            "parent": client.post(
                "/api/datasets",
                headers=AUTH,
                json={"path": str(data_root) + "/../outside.zarr"},
            ),
            "listed": client.get("/api/datasets", headers=AUTH),
        }

    assert (responses["outside"].status_code, responses["outside"].json()) == (
        403,
        {"detail": "Path is outside the configured data roots"},
    )
    assert (responses["missing"].status_code, responses["missing"].json()) == (
        404,
        {"detail": f"Path does not exist: {missing}"},
    )
    assert (responses["parent"].status_code, responses["parent"].json()) == (
        403,
        {"detail": "Parent path segments are not allowed"},
    )
    assert responses["listed"].json() == {"datasets": []}
