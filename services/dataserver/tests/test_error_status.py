"""Status and detail of every HTTP error the data server raises (report DS-10).

The per-route differences are deliberate and frozen until a separate API
decision: OmeZarrError is 422 on summary and /zarr but 404 on wells, and
PackedMetadataError is 503 under /api/qc but 422 under /api/datasets.

Sites without a row here cannot be reached through the API today:
``linked`` "Packed plate is no longer available" (linking refuses indexes and
experiments), the packed summary's PackedFacadeNotFoundError (linking already
validated the plate), the summary's PathForbiddenError (store readers raise
OmeZarrError for escapes), the unknown packed facade resource after parsing (the
parser returns only metadata or chunk requests), the QC payload's
PackedMetadataError and QcPayloadError (metadata and the core contract are
checked before payloads are built), and the listing OSError and symlinked
private-path check (both need platform-specific setup). PackedReadError's 422
on /zarr is pinned by test_packed_resources.py.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from served_stores import write_json, write_native_plate
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.qc_config import QcPathConfig

from scripts import make_test_data
from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    import httpx
    from conftest import WriteAudit

TOKEN = "error-status-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
type Expected = tuple[int, str]


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory, write_audit: WriteAudit) -> Path:
    root = tmp_path_factory.mktemp("error-status-pristine")
    generate_test_data(root)
    write_audit.watch(root)
    return root.resolve()


def _copy(pristine: Path, destination: Path, *names: str) -> Path:
    destination.mkdir(parents=True)
    for name in names:
        source = pristine / name
        if source.is_dir():
            shutil.copytree(source, destination / name)
        else:
            shutil.copy2(source, destination / name)
    return destination.resolve()


def _client(
    roots: tuple[Path, ...],
    state: Path,
    *,
    metadata_root: Path | None = None,
    qc_paths: tuple[QcPathConfig, ...] = (),
) -> TestClient:
    settings = ServerSettings(
        data_roots=roots,
        state_dir=state,
        environment="local",
        metadata_root=metadata_root,
        qc_paths=qc_paths,
    )
    return TestClient(create_app(settings, TOKEN))


def _link(client: TestClient, path: Path) -> str:
    response = client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _status(response: httpx.Response) -> Expected:
    return response.status_code, response.json()["detail"]


def _assert_table(table: list[tuple[str, httpx.Response, Expected]]) -> None:
    observed = {label: _status(response) for label, response, _expected in table}
    expected = {label: status for label, _response, status in table}
    assert observed == expected


def test_link_and_unlink_error_statuses(pristine: Path, tmp_path: Path) -> None:
    root = _copy(pristine, tmp_path / "root", "test_image_v05.zarr", "packed")
    write_native_plate(root / "native_plate.zarr")
    (root / "ordinary").mkdir()
    index = root / "packed" / make_test_data.PACKED_FIXTURE_DIRECTORY / (
        make_test_data.PACKED_INDEX_NAME
    )
    outside = tmp_path / "outside" / "elsewhere.index.parquet"

    with _client((root,), tmp_path / "state") as client:

        def link(raw: str) -> httpx.Response:
            return client.post("/api/datasets", headers=AUTH, json={"path": raw})

        table = [
            ("not a store", link(str(root / "ordinary")), (422, (
                "Path is not a recognized OME-Zarr store"
            ))),
            ("native well", link(str(root / "native_plate.zarr" / "A" / "1")), (
                422,
                "Link a plate or image, not an individual well",
            )),
            ("packed index", link(str(index)), (
                422,
                "Link a packed plate, not an index or experiment",
            )),
            ("packed experiment", link(str(index / "idr0080-A549")), (
                422,
                "Link a packed plate, not an index or experiment",
            )),
            ("unknown experiment", link(str(index / "unknown" / "14610")), (
                404,
                "Unknown packed experiment: unknown",
            )),
            ("unknown plate", link(str(index / "idr0080-A549" / "99999")), (
                404,
                "Unknown packed plate: 99999",
            )),
            ("non-numeric plate", link(str(index / "idr0080-A549" / "plate")), (
                404,
                "Unknown packed plate: plate",
            )),
            ("too deep", link(str(index / "idr0080-A549" / "14610" / "A01")), (
                404,
                f"Unknown packed dataset path: {index / 'idr0080-A549' / '14610' / 'A01'}",
            )),
            ("index outside roots", link(str(outside / "idr0080-A549" / "14610")), (
                403,
                "Path is outside the configured data roots",
            )),
            ("unlink unknown", client.delete("/api/datasets/" + "0" * 32, headers=AUTH), (
                404,
                "Dataset is not linked",
            )),
        ]

    _assert_table(table)


def test_store_route_error_statuses(pristine: Path, tmp_path: Path) -> None:
    root = _copy(pristine, tmp_path / "root", "test_plate.zarr", "test_image_v04.zarr", "metadata")
    metadata_csv = root / "metadata" / "csv" / "test_plate.csv"
    metadata_csv.write_text("plate_id,well\n", encoding="utf-8")

    with _client((root,), tmp_path / "state", metadata_root=root / "metadata") as client:
        plate = _link(client, root / "test_plate.zarr")
        image = _link(client, root / "test_image_v04.zarr")

        def get(path: str, **headers: str) -> httpx.Response:
            return client.get(path, headers={**AUTH, **headers})

        table = [
            ("unknown well", get(f"/api/datasets/{plate}/wells/Z99"), (404, "Unknown well: Z99")),
            ("nested TIMED well", get(f"/api/datasets/{plate}/wells/A01/s1"), (
                403,
                "Invalid TIMED well path",
            )),
            ("wells on an image", get(f"/api/datasets/{image}/wells/A/1"), (
                422,
                "Dataset is not a plate",
            )),
            ("matches on TIMED", get(f"/api/datasets/{plate}/matches?well=A01&by=gene"), (
                404,
                "Dataset matches are available only for packed plates",
            )),
            ("header-only plate metadata", get(f"/api/datasets/{plate}/metadata"), (
                422,
                f"Well metadata CSV is empty: {metadata_csv}",
            )),
            ("zarr directory", get(f"/zarr/{image}/s0"), (
                404,
                "Zarr resource does not exist: s0",
            )),
            ("zarr missing chunk", get(f"/zarr/{plate}/A01_s1.zarr/s0/c/9/9/9/9"), (
                404,
                "Path does not exist: A01_s1.zarr/c/9/9/9/9",
            )),
            ("memory range past the end", get(
                f"/zarr/{plate}/A01_s1.zarr/zarr.json", Range="bytes=999999-"
            ), (416, "Unsatisfiable byte range")),
            ("memory range malformed", get(
                f"/zarr/{plate}/A01_s1.zarr/zarr.json", Range="items=0-1"
            ), (416, "Invalid byte range")),
            ("file range inverted", get(f"/zarr/{image}/.zattrs", Range="bytes=10-5"), (
                416,
                "Unsatisfiable byte range",
            )),
            ("file range empty suffix", get(f"/zarr/{image}/.zattrs", Range="bytes=-0"), (
                416,
                "Invalid byte range",
            )),
        ]

    _assert_table(table)


def _break_field(field: Path) -> None:
    metadata = json.loads((field / "zarr.json").read_text(encoding="utf-8"))
    metadata["data_type"] = "float32"
    write_json(field / "zarr.json", metadata)


def test_broken_timed_fields_keep_their_per_route_statuses(
    pristine: Path, tmp_path: Path
) -> None:
    root = _copy(pristine, tmp_path / "root", "test_plate.zarr", "test_plate_2.zarr")

    with _client((root,), tmp_path / "state") as client:
        first = _link(client, root / "test_plate.zarr")
        later = _link(client, root / "test_plate_2.zarr")
        _break_field(root / "test_plate.zarr" / "A01_s1.zarr")
        _break_field(root / "test_plate_2.zarr" / "B01_s2.zarr")
        gone = "Linked path is no longer a recognized OME-Zarr dataset"

        def get(path: str) -> httpx.Response:
            return client.get(path, headers=AUTH)

        first_field = [
            (f"first field {route}", get(path), (404, gone))
            for route, path in (
                ("summary", f"/api/datasets/{first}/summary"),
                ("wells", f"/api/datasets/{first}/wells/A01"),
                ("metadata", f"/api/datasets/{first}/metadata"),
                ("zarr", f"/zarr/{first}/A01_s1.zarr/zarr.json"),
                ("other zarr", f"/zarr/{first}/A02_s1.zarr/zarr.json"),
            )
        ]
        summary = get(f"/api/datasets/{later}/summary")
        well = get(f"/api/datasets/{later}/wells/B01")
        resource = get(f"/zarr/{later}/B01_s2.zarr/zarr.json")
        untouched = get(f"/zarr/{later}/B01_s1.zarr/zarr.json")

    _assert_table(first_field)
    assert summary.status_code == 200
    assert untouched.status_code == 200
    assert well.status_code == 404
    assert resource.status_code == 422
    assert well.json()["detail"] == resource.json()["detail"]
    assert "B01_s2.zarr" in well.json()["detail"]


def test_listing_error_statuses(pristine: Path, tmp_path: Path) -> None:
    root = _copy(pristine, tmp_path / "root", "test_plate.zarr", "packed", "qc-export")
    packed = root / "packed" / make_test_data.PACKED_FIXTURE_DIRECTORY
    index = packed / make_test_data.PACKED_INDEX_NAME
    source = pq.read_table(index)
    # A packed index is detected only beside its shards.
    pq.write_table(
        pa.concat_tables([source.slice(0, 1), source.slice(0, 1)]),
        packed / "malformed.index.parquet",
    )
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    qc = (QcPathConfig(index, root / "qc-export", annotations),)

    with _client((root,), tmp_path / "state", qc_paths=qc) as client:

        def listing(raw: str) -> httpx.Response:
            return client.get("/api/fs/list", headers=AUTH, params={"path": raw})

        table = [
            ("private QC folder", listing(str(root / "qc-export")), (
                403,
                "Configured QC folders are not browsable",
            )),
            ("packed plate", listing(str(index / "idr0080-A549" / "14610")), (
                400,
                "Packed plates are not listable",
            )),
            ("outside", listing(str(tmp_path)), (403, "Path is outside the configured data roots")),
            ("missing", listing(str(root / "missing")), (
                404,
                f"Path does not exist: {root / 'missing'}",
            )),
            ("file", listing(str(root / "test_plate.zarr" / "acquisition_config.json")), (
                400,
                "Only directories can be listed",
            )),
            ("inside a store", listing(str(root / "test_plate.zarr" / "A01_s1.zarr")), (
                400,
                "The inside of a Zarr store is not listable",
            )),
            ("parent segments", listing(str(root) + "/../outside"), (
                403,
                "Parent path segments are not allowed",
            )),
        ]
        malformed = listing(str(packed))

    _assert_table(table)
    assert malformed.status_code == 422
    assert "duplicate field/channel key" in malformed.json()["detail"]


def _rewrite_metadata_gene(packed: Path, gene: str) -> None:
    path = packed / "metadata" / "metadata_idr0080.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)
    rows[0]["cell_type"] = gene
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_packed_route_error_statuses(pristine: Path, tmp_path: Path) -> None:
    root = _copy(pristine, tmp_path / "root", "packed")
    packed = root / "packed" / make_test_data.PACKED_FIXTURE_DIRECTORY
    index = packed / make_test_data.PACKED_INDEX_NAME

    with _client((root,), tmp_path / "state") as client:
        plate = _link(client, index / "idr0080-A549" / "14610")

        def get(path: str) -> httpx.Response:
            return client.get(path, headers=AUTH)

        table = [
            ("unknown well", get(f"/api/datasets/{plate}/wells/H12"), (
                404,
                "Unknown packed well: H12",
            )),
            ("unknown field", get(f"/zarr/{plate}/H12_s1.zarr/zarr.json"), (
                404,
                "Unknown packed field: H12_s1.zarr",
            )),
            ("unknown resource", get(f"/zarr/{plate}/A01_s1.zarr/s0/.zarray"), (
                404,
                "Unknown packed facade resource: A01_s1.zarr/s0/.zarray",
            )),
            ("channel out of range", get(f"/zarr/{plate}/A01_s1.zarr/s0/c/9/0/0"), (
                404,
                "Packed channel index is out of range: 9",
            )),
            ("chunk out of range", get(f"/zarr/{plate}/A01_s1.zarr/s0/c/0/9/9"), (
                404,
                "Packed chunk is out of range: 9/9",
            )),
            ("matches unknown well", get(f"/api/datasets/{plate}/matches?well=H12&by=gene"), (
                404,
                "",
            )),
        ]
        _rewrite_metadata_gene(packed, "inconsistent")
        metadata = get(f"/api/datasets/{plate}/metadata")
        matches = get(f"/api/datasets/{plate}/matches?well=C01&by=gene")

    observed = {label: _status(response) for label, response, _expected in table}
    assert observed.pop("matches unknown well")[0] == 404
    _assert_table([row for row in table if row[0] != "matches unknown well"])
    for response in (metadata, matches):
        assert response.status_code == 422
        assert "inconsistent 'cell_type'" in response.json()["detail"]


def _qc_tree(pristine: Path, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = _copy(pristine, tmp_path / "root", "packed", "qc-export")
    index = root / "packed" / make_test_data.PACKED_FIXTURE_DIRECTORY / (
        make_test_data.PACKED_INDEX_NAME
    )
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    return root, index, root / "qc-export", annotations


def test_qc_error_statuses(pristine: Path, tmp_path: Path) -> None:
    root, index, export, annotations = _qc_tree(pristine, tmp_path)
    qc = (QcPathConfig(index, export, annotations),)
    other_index = root / "packed" / "unconfigured.index.parquet"
    shutil.copy2(index, other_index)

    with _client((root,), tmp_path / "state", qc_paths=qc) as client:
        status = client.get("/api/qc/status", headers=AUTH, params={"path": str(index)})
        export_id = status.json()["export_id"]

        def get(name: str, raw: str, **params: object) -> httpx.Response:
            return client.get(f"/api/qc/{name}", headers=AUTH, params={"path": raw, **params})

        def write(body: dict[str, object]) -> httpx.Response:
            return client.post(
                "/api/qc/annotations",
                headers=AUTH,
                params={"path": str(index)},
                json=body,
            )

        command = {
            "record": "annotation",
            "export_id": export_id,
            "scope": "field",
            "plate": 14610,
            "well": "A01",
            "site": 1,
            "channel": "DNA",
            "label": "blurred",
            "confidence": "high",
            "note": "",
        }
        table = [
            ("outside", get("dictionary", str(tmp_path)), (
                403,
                "Path is outside the configured data roots",
            )),
            ("missing", get("dictionary", str(root / "missing.index.parquet")), (
                404,
                f"Path does not exist: {root / 'missing.index.parquet'}",
            )),
            ("unconfigured", get("dictionary", str(other_index)), (
                404,
                "No QC export is configured for this index",
            )),
            ("unknown field", get("field", str(index), plate=14610, well="H12", site=1), (
                404,
                "",
            )),
            ("stale export", write({**command, "export_id": "stale"}), (
                409,
                "The QC export changed; reload the review before saving",
            )),
            ("unknown target", write({**command, "well": "H12"}), (
                422,
                "Annotation field does not exist",
            )),
            ("unknown channel", write({**command, "channel": "Nope"}), (
                422,
                "Annotation channel is invalid",
            )),
        ]
        writer = annotations / f"annotations_{_reviewer(client, index)}.jsonl"
        writer.mkdir()
        unavailable = write(command)
        writer.rmdir()
        shutil.rmtree(annotations)
        not_writable = write(command)

    observed = {label: _status(response) for label, response, _expected in table}
    assert observed.pop("unknown field")[0] == 404
    _assert_table([row for row in table if row[0] != "unknown field"])
    assert _status(unavailable) == (503, "QC annotations are not writable")
    assert _status(not_writable) == (503, "QC annotations are not writable")


def _reviewer(client: TestClient, index: Path) -> str:
    stores = client.app.state.qc_annotation_stores  # type: ignore[attr-defined]
    return next(iter(stores.values())).reviewer


def test_qc_unavailable_export_and_index_statuses(pristine: Path, tmp_path: Path) -> None:
    root, index, export, annotations = _qc_tree(pristine, tmp_path)
    missing_export = tmp_path / "missing-export"

    with _client(
        (root,),
        tmp_path / "missing-state",
        qc_paths=(QcPathConfig(index, missing_export, annotations),),
    ) as client:
        unavailable = client.get("/api/qc/dictionary", headers=AUTH, params={"path": str(index)})

    with _client(
        (root,),
        tmp_path / "corrupt-state",
        qc_paths=(QcPathConfig(index, export, annotations),),
    ) as client:
        index.write_bytes(b"not a parquet index")
        unloadable = client.get("/api/qc/dictionary", headers=AUTH, params={"path": str(index)})
        status = client.get("/api/qc/status", headers=AUTH, params={"path": str(index)})

    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]
    assert _status(unloadable) == (503, "Configured QC index could not be loaded")
    assert status.status_code == 200
    assert status.json()["available"] is False
    assert status.json()["error"] == "Configured QC index could not be loaded"


def test_qc_phase_one_metadata_errors_are_unavailable(pristine: Path, tmp_path: Path) -> None:
    root, index, export, annotations = _qc_tree(pristine, tmp_path)
    _rewrite_metadata_gene(index.parent, "inconsistent")

    with _client(
        (root,),
        tmp_path / "state",
        qc_paths=(QcPathConfig(index, export, annotations),),
    ) as client:
        fields = client.get("/api/qc/fields", headers=AUTH, params={"path": str(index)})
        blind = client.get(
            "/api/qc/fields",
            headers=AUTH,
            params={"path": str(index), "blind": "1"},
        )

    assert _status(fields) == (503, "Phase 1 metadata is unavailable or invalid")
    assert blind.status_code == 200


def test_qc_non_core_export_and_hash_guard_statuses(pristine: Path, tmp_path: Path) -> None:
    root, index, export, annotations = _qc_tree(pristine, tmp_path)
    version_path = export / "version.json"
    version = json.loads(version_path.read_text(encoding="utf-8"))
    columns = export / "columns.csv"
    rows = list(csv.DictReader(columns.open(encoding="utf-8", newline="")))
    for row in rows:
        if row["layer"] == "triage":
            row["layer"] = "context"
    with columns.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    version["files"]["columns.csv"] = hashlib.sha256(columns.read_bytes()).hexdigest()
    version_path.write_text(json.dumps(version, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with _client(
        (root,),
        tmp_path / "state",
        qc_paths=(QcPathConfig(index, export, annotations),),
    ) as client:
        response = client.get("/api/qc/dictionary", headers=AUTH, params={"path": str(index)})

    assert _status(response) == (
        503,
        "QC export does not satisfy the core response contract; see QC status",
    )


def test_escaping_native_well_path_makes_the_summary_unprocessable(tmp_path: Path) -> None:
    root = tmp_path / "root"
    plate = write_native_plate(root / "escaping_plate.zarr")
    attributes = json.loads((plate / ".zattrs").read_text(encoding="utf-8"))
    attributes["plate"]["wells"][0]["path"] = "A/../1"
    write_json(plate / ".zattrs", attributes)

    with _client((root.resolve(),), tmp_path / "state") as client:
        dataset_id = _link(client, plate)
        summary = client.get(f"/api/datasets/{dataset_id}/summary", headers=AUTH)
        well = client.get(f"/api/datasets/{dataset_id}/wells/A/1", headers=AUTH)

    assert _status(summary) == (422, "Metadata path escapes its store: A/../1")
    assert well.status_code == 200


def test_failed_generic_profile_probe_is_unprocessable(pristine: Path, tmp_path: Path) -> None:
    root = _copy(pristine, tmp_path / "root", "packed")
    packed = root / "packed" / make_test_data.PACKED_FIXTURE_DIRECTORY
    generic = (packed / make_test_data.PACKED_INDEX_NAME).rename(packed / "generic.index.parquet")
    for shard in packed.glob("*.tar"):
        shard.write_bytes(shard.read_bytes().replace(b"ustar\x0000", b"xxxxx\x0000"))

    with _client((root,), tmp_path / "state") as client:
        listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(generic)})
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(generic / "idr0080-A549" / "14610")},
        )

    for response in (listing, linking):
        assert response.status_code == 422
        assert response.json()["detail"].startswith("Invalid generic packed USTAR header")
