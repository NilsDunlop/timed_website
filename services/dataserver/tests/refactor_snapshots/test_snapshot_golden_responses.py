"""Temporary refactor snapshot: data-server responses for every dataset kind.

Recorded from unchanged code on 2026-11-05 with TIMED_UPDATE_GOLDENS=1. A golden
changes only with an approved step; otherwise stop and report. The last commit
of the Gate A refactor deletes this folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from conftest import Normaliser, ServedTree, Tree

TOKEN = "refactor-snapshot-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
GOLDEN = Path(__file__).with_name("golden")
UPDATE = os.environ.get("TIMED_UPDATE_GOLDENS") == "1"
_KEPT_HEADERS = (
    "accept-ranges",
    "cache-control",
    "content-encoding",
    "content-range",
    "content-type",
    "vary",
    "x-timed-cache",
)
type Request = tuple[str, str, dict[str, object] | None]


def _etag_format(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"[0-9a-f]{6,}", "<hex>", value)


class Recorder:
    def __init__(self, client: TestClient, normaliser: Normaliser) -> None:
        self.client = client
        self.normaliser = normaliser
        self.entries: dict[str, object] = {}

    def link(self, label: str, path: Path) -> str:
        response = self.client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
        assert response.status_code == 201, response.text
        dataset_id = str(response.json()["id"])
        self.normaliser.dataset(dataset_id)
        self.entries[label] = self._entry(response)
        return dataset_id

    def get(self, label: str, path: str, params: dict[str, object] | None = None) -> None:
        self.entries[label] = self._entry(self.client.get(path, headers=AUTH, params=params))

    def send(
        self,
        label: str,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: object = None,
    ) -> httpx.Response:
        response = self.client.request(method, path, headers=AUTH, params=params, json=json_body)
        self.entries[label] = self._entry(response)
        return response

    def _entry(self, response: httpx.Response) -> dict[str, object]:
        entry: dict[str, object] = {"status": response.status_code}
        entry["headers"] = {
            name: response.headers[name] for name in _KEPT_HEADERS if name in response.headers
        }
        entry["etag"] = _etag_format(response.headers.get("etag"))
        if response.headers.get("content-type", "").startswith("application/json"):
            entry["body"] = self.normaliser(response.json())
        else:
            entry["length"] = len(response.content)
            entry["sha256"] = hashlib.sha256(response.content).hexdigest()
        return entry

    def check(self, name: str) -> None:
        path = GOLDEN / f"{name}.json"
        actual = json.loads(json.dumps(self.entries))
        if UPDATE:
            GOLDEN.mkdir(exist_ok=True)
            path.write_text(
                json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        assert path.is_file(), f"Record goldens with TIMED_UPDATE_GOLDENS=1: {path.name}"
        expected = json.loads(path.read_text(encoding="utf-8"))
        changed = sorted(
            label
            for label in actual.keys() | expected.keys()
            if actual.get(label) != expected.get(label)
        )
        assert not changed, f"{name}: responses changed for {changed}"


def _dataset_routes(
    recorder: Recorder,
    dataset_id: str,
    *,
    wells: tuple[str, ...],
    resources: tuple[str, ...],
) -> None:
    recorder.get("summary", f"/api/datasets/{dataset_id}/summary")
    for well in wells:
        recorder.get(f"wells {well}", f"/api/datasets/{dataset_id}/wells/{well}")
    recorder.get("metadata", f"/api/datasets/{dataset_id}/metadata")
    for resource in resources:
        recorder.get(f"zarr {resource}", f"/zarr/{dataset_id}/{resource}")


@pytest.fixture
def recorder(
    served_client: Callable[[Tree], TestClient], normaliser: Normaliser
) -> Iterator[Callable[[Tree], Recorder]]:
    clients: list[TestClient] = []

    def make(tree: Tree) -> Recorder:
        client = served_client(tree)
        client.__enter__()
        clients.append(client)
        return Recorder(client, normaliser)

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def test_handshake_listings_and_dataset_list(
    recorder: Callable[[Tree], Recorder], served_tree: ServedTree
) -> None:
    record = recorder("generated")
    record.get("handshake", "/api/handshake")
    record.get("fs roots", "/api/fs/list")
    record.get("fs generated root", "/api/fs/list", {"path": str(served_tree.generated)})
    record.get("fs metadata", "/api/fs/list", {"path": str(served_tree.generated / "metadata")})
    record.get("fs packed parent", "/api/fs/list", {"path": str(served_tree.index.parent)})
    record.get("fs packed index", "/api/fs/list", {"path": str(served_tree.index)})
    record.get(
        "fs packed experiment",
        "/api/fs/list",
        {"path": str(served_tree.index / "idr0080-A549")},
    )
    for name in (
        "test_plate.zarr",
        "test_plate_2.zarr",
        "test_image_v04.zarr",
        "test_image_v05.zarr",
        "native_plate.zarr",
    ):
        record.link(f"link {name}", served_tree.generated / name)
    record.link("link packed 14610", served_tree.index / "idr0080-A549" / "14610")
    record.link("link packed 14613", served_tree.index / "idr0080-ES2" / "14613")
    record.get("datasets", "/api/datasets")
    record.check("handshake_listings_datasets")


def test_timed_collection_plate(
    recorder: Callable[[Tree], Recorder], served_tree: ServedTree
) -> None:
    record = recorder("generated")
    dataset_id = record.link("link", served_tree.generated / "test_plate.zarr")
    _dataset_routes(
        record,
        dataset_id,
        wells=("A01", "A02", "B01", "B02"),
        resources=(
            "A01_s1.zarr/zarr.json",
            "A01_s1.zarr/s0/zarr.json",
            "A01_s1.zarr/s0/c/0/0/0/0",
            "B01_s2.zarr/zarr.json",
        ),
    )
    record.check("timed_collection_plate")


def test_berzelius_timed_plate(
    recorder: Callable[[Tree], Recorder], served_tree: ServedTree
) -> None:
    record = recorder("berzelius")
    record.get("fs root", "/api/fs/list", {"path": str(served_tree.berzelius)})
    dataset_id = record.link("link", served_tree.berzelius / "PB100140_P2.1.zarr")
    _dataset_routes(
        record,
        dataset_id,
        wells=("C03",),
        resources=(
            "C03_s1.zarr/zarr.json",
            "C03_s1.zarr/s0/zarr.json",
            "C03_s1.zarr/s0/c/0/0/0/0",
        ),
    )
    record.check("berzelius_timed_plate")


@pytest.mark.parametrize(
    ("store", "resources"),
    (
        (
            "test_image_v04.zarr",
            (".zgroup", ".zattrs", "s0/.zarray", "s0/.zattrs", "s0/0/0/0/0"),
        ),
        ("test_image_v05.zarr", ("zarr.json", "s0/zarr.json", "s0/c/0/0/0/0")),
    ),
)
def test_native_images(
    recorder: Callable[[Tree], Recorder],
    served_tree: ServedTree,
    store: str,
    resources: tuple[str, ...],
) -> None:
    record = recorder("generated")
    dataset_id = record.link("link", served_tree.generated / store)
    _dataset_routes(record, dataset_id, wells=("A/1",), resources=resources)
    record.check(f"native_{store.removesuffix('.zarr')}")


def test_native_plate(recorder: Callable[[Tree], Recorder], served_tree: ServedTree) -> None:
    record = recorder("generated")
    dataset_id = record.link("link", served_tree.generated / "native_plate.zarr")
    _dataset_routes(
        record,
        dataset_id,
        wells=("A/1",),
        resources=(".zattrs", "A/1/.zattrs", "A/1/0/.zattrs", "A/1/0/0/.zarray"),
    )
    record.check("native_plate")


def _packed_routes(record: Recorder, dataset_id: str) -> None:
    _dataset_routes(
        record,
        dataset_id,
        wells=("A01", "P24", "H12"),
        resources=(
            "zarr.json",
            "A01_s1.zarr/zarr.json",
            "A01_s1.zarr/s0/zarr.json",
            "A01_s1.zarr/s0/c/0/0/0",
            "A01_s2.zarr/s0/c/4/0/0",
        ),
    )
    for mode in ("well", "gene", "control"):
        record.get(
            f"matches {mode}",
            f"/api/datasets/{dataset_id}/matches",
            {"well": "C01", "by": mode},
        )


def test_packed_plate_with_pinned_profile(
    recorder: Callable[[Tree], Recorder], served_tree: ServedTree
) -> None:
    record = recorder("generated")
    dataset_id = record.link("link", served_tree.index / "idr0080-A549" / "14610")
    _packed_routes(record, dataset_id)
    record.check("packed_pinned_profile")


def test_packed_plate_with_inferred_profile(
    recorder: Callable[[Tree], Recorder], served_tree: ServedTree
) -> None:
    record = recorder("generic")
    record.get("fs root", "/api/fs/list", {"path": str(served_tree.generic)})
    record.get("fs index", "/api/fs/list", {"path": str(served_tree.generic_index)})
    record.get(
        "fs experiment",
        "/api/fs/list",
        {"path": str(served_tree.generic_index / "idr0080-A549")},
    )
    dataset_id = record.link("link", served_tree.generic_index / "idr0080-A549" / "14610")
    _packed_routes(record, dataset_id)
    record.check("packed_inferred_profile")


_QC_ROUTES = ("status", "dictionary", "fields", "triage", "display-ranges", "review-list")
# Annotation writes change only these representations.
_ANNOTATED: dict[str, tuple[str, ...] | tuple[int, ...]] = {
    "routes": ("status", "fields"),
    "sites": (1,),
}


def _qc_reads(
    record: Recorder,
    prefix: str,
    index: Path,
    *,
    blind: bool,
    routes: tuple[str, ...] = _QC_ROUTES,
    sites: tuple[int, ...] = (1, 2),
) -> None:
    params: dict[str, object] = {"path": str(index), "blind": "1" if blind else "0"}
    for name in routes:
        record.get(f"{prefix} {name}", f"/api/qc/{name}", params)
    for site in sites:
        record.get(
            f"{prefix} field A01 site {site}",
            "/api/qc/field",
            {**params, "plate": 14610, "well": "A01", "site": site},
        )
    record.get(f"{prefix} annotations", "/api/qc/annotations", params)


@pytest.mark.parametrize("blind", (False, True), ids=("core", "blind"))
def test_qc_routes_before_and_after_annotation_writes(
    recorder: Callable[[Tree], Recorder], served_tree: ServedTree, blind: bool
) -> None:
    record = recorder("generated")
    index = served_tree.index
    params: dict[str, object] = {"path": str(index), "blind": "1" if blind else "0"}
    _qc_reads(record, "before", index, blind=blind)
    export_id = record.client.get("/api/qc/status", headers=AUTH, params=params).json()[
        "export_id"
    ]
    created = record.send(
        "write field annotation",
        "POST",
        "/api/qc/annotations",
        params=params,
        json_body={
            "record": "annotation",
            "export_id": export_id,
            "scope": "field",
            "plate": 14610,
            "well": "a01",
            "site": 1,
            "channel": "DNA",
            "label": "blurred",
            "confidence": "high",
            "note": "Golden field note · µm",
        },
    )
    record.send(
        "write well annotation",
        "POST",
        "/api/qc/annotations",
        params=params,
        json_body={
            "record": "annotation",
            "export_id": export_id,
            "scope": "well",
            "plate": 14610,
            "well": "A02",
            "site": None,
            "channel": "all",
            "label": "ok",
            "confidence": "low",
            "note": "",
        },
    )
    _qc_reads(record, "after write", index, blind=blind, **_ANNOTATED)
    record.send(
        "write retraction",
        "POST",
        "/api/qc/annotations",
        params=params,
        json_body={
            "record": "retraction",
            "export_id": export_id,
            "id": created.json()["record"]["id"],
        },
    )
    _qc_reads(record, "after retraction", index, blind=blind, **_ANNOTATED)
    record.check(f"qc_{'blind' if blind else 'core'}")
