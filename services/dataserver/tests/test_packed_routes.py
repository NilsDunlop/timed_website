from __future__ import annotations

import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import timed_dataserver.app as dataserver_app
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app

from scripts import make_test_data

TOKEN = "packed-route-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def packed_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate packed data separately from the frozen TIMED server fixture."""
    output = tmp_path_factory.mktemp("packed-route-data")
    return make_test_data._write_packed_fixture(output)


def _settings(root: Path, state_dir: Path) -> ServerSettings:
    return ServerSettings(
        data_roots=(root,),
        state_dir=state_dir,
        environment="local",
    )


def _entry(
    name: str,
    path: Path,
    kind: str,
    *,
    detail: str | None = None,
    ome_zarr: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "name": name,
        "path": str(path),
        "kind": kind,
        "ome_zarr": ome_zarr,
    }
    if detail is not None:
        result["detail"] = detail
    return result


def test_parent_index_and_experiment_listings_are_exact(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    root = packed_root.resolve()
    index = (root / make_test_data.PACKED_INDEX_NAME).resolve()
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        parent = client.get("/api/fs/list", headers=AUTH, params={"path": str(root)})
        assert parent.status_code == 200, parent.text
        assert parent.json() == {
            "path": str(root),
            "entries": [
                _entry(
                    f"idr0080-raw-SQ000{plate}.tar",
                    root / f"idr0080-raw-SQ000{plate}.tar",
                    "file",
                )
                for plate in (14610, 14611, 14613)
            ]
            + [
                _entry(
                    make_test_data.PACKED_INDEX_NAME,
                    index,
                    "dir",
                    detail="2 cell lines \u00b7 3 plates",
                ),
                _entry("index_fragments", root / "index_fragments", "dir"),
                _entry("masks", root / "masks", "dir"),
                _entry("metadata", root / "metadata", "dir"),
            ],
        }

        index_listing = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(index)},
        )
        assert index_listing.status_code == 200, index_listing.text
        assert index_listing.json() == {
            "path": str(index),
            "entries": [
                _entry(
                    "A549",
                    index / "idr0080-A549",
                    "dir",
                    detail="2 plates",
                ),
                _entry(
                    "ES2",
                    index / "idr0080-ES2",
                    "dir",
                    detail="1 plate",
                ),
            ],
        }

        experiment = index / "idr0080-A549"
        experiment_listing = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(experiment)},
        )
        assert experiment_listing.status_code == 200, experiment_listing.text
        plate_info = {"kind": "plate", "version": "0.5", "zarr_format": 3}
        assert experiment_listing.json() == {
            "path": str(experiment),
            "entries": [
                _entry(
                    f"Cell Health \u00b7 A549 \u00b7 SQ000{plate}",
                    experiment / str(plate),
                    "dir",
                    detail="5 wells \u00b7 2 sites \u00b7 5 channels",
                    ome_zarr=plate_info,
                )
                for plate in (14610, 14611)
            ],
        }


def test_plate_link_is_idempotent_and_persists_across_apps(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    plate = index / "idr0080-A549" / "14610"
    state_dir = tmp_path / "persistent-state"
    settings = _settings(packed_root, state_dir)

    with TestClient(create_app(settings, TOKEN)) as first_client:
        first = first_client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(plate)},
        )
        assert first.status_code == 201, first.text
        record = first.json()
        assert {key: record[key] for key in ("path", "name", "ome_zarr")} == {
            "path": str(plate),
            "name": "Cell Health \u00b7 A549 \u00b7 SQ00014610",
            "ome_zarr": {"kind": "plate", "version": "0.5", "zarr_format": 3},
        }
        assert datetime.fromisoformat(record["linked_at"]).tzinfo is not None

        repeated = first_client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(plate)},
        )
        assert repeated.status_code == 201, repeated.text
        assert repeated.json() == record
        assert first_client.get("/api/datasets", headers=AUTH).json() == {"datasets": [record]}

    registry_path = state_dir / "datasets.json"
    persisted = json.loads(registry_path.read_text(encoding="utf-8"))
    persisted["datasets"][0]["name"] = "SQ00014610"
    registry_path.write_text(json.dumps(persisted), encoding="utf-8")

    with TestClient(create_app(settings, TOKEN)) as restarted_client:
        assert restarted_client.get("/api/datasets", headers=AUTH).json() == {"datasets": [record]}
        reopened = restarted_client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(plate)},
        )
        assert reopened.status_code == 201, reopened.text
        assert reopened.json() == record


def test_packed_metadata_route_maps_idr0080_fixture(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    plate = (
        packed_root
        / make_test_data.PACKED_INDEX_NAME
        / "idr0080-A549"
        / "14610"
    )
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        linked = client.post("/api/datasets", headers=AUTH, json={"path": str(plate)})
        assert linked.status_code == 201, linked.text
        response = client.get(
            f"/api/datasets/{linked.json()['id']}/metadata",
            headers=AUTH,
        )

    assert response.status_code == 200, response.text
    metadata = response.json()
    assert metadata["labels"] == {
        "compound": "Gene",
        "moa": "Well type",
        "control": "EMPTY control",
    }
    assert metadata["category_colors"] == {
        "Query guides": "#3b82a0",
        "Cutting controls": "#b86b35",
        "Non-cutting controls": "#9367a8",
    }
    assert metadata["plate"]["plate_id"] == "SQ00014610"
    assert metadata["plate"]["project"] == "Cell Health"
    assert metadata["plate"]["acquisition"]["sites_per_well"] == 2
    wells = {well["well"]: well for well in metadata["wells"]}
    assert wells["A01"]["compound"] == "EMPTY control"
    assert wells["A01"]["is_control"] is True
    assert wells["A02"]["moa"] == "Cutting controls"
    assert wells["B01"]["moa"] == "Non-cutting controls"
    assert wells["C01"]["details"] == [
        {"label": "Guide", "value": "KRAS-1"},
        {"label": "Ensembl ID", "value": "ENSG00000133703"},
        {"label": "Control category", "value": "targeting"},
    ]


def test_packed_matches_route_supports_well_gene_and_control_modes(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    plate = index / "idr0080-A549" / "14610"
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        linked = client.post("/api/datasets", headers=AUTH, json={"path": str(plate)})
        assert linked.status_code == 201, linked.text
        dataset_id = linked.json()["id"]
        same_well = client.get(
            f"/api/datasets/{dataset_id}/matches",
            headers=AUTH,
            params={"well": "c01", "by": "well"},
        )
        same_gene = client.get(
            f"/api/datasets/{dataset_id}/matches",
            headers=AUTH,
            params={"well": "C01", "by": "gene"},
        )
        controls = client.get(
            f"/api/datasets/{dataset_id}/matches",
            headers=AUTH,
            params={"well": "C01", "by": "control"},
        )

    assert same_well.status_code == 200, same_well.text
    assert same_well.json() == {
        "matches": [
            {
                "path": str(index / "idr0080-A549" / "14611"),
                "plate_label": "SQ00014611",
                "cell_line": "A549",
                "well": "C01",
                "guide": "KRAS-1",
                "is_control": False,
                "well_type": "Query guides",
            },
            {
                "path": str(index / "idr0080-ES2" / "14613"),
                "plate_label": "SQ00014613",
                "cell_line": "ES2",
                "well": "C01",
                "guide": "KRAS-1",
                "is_control": False,
                "well_type": "Query guides",
            },
        ]
    }

    assert same_gene.status_code == 200, same_gene.text
    assert [
        (match["cell_line"], match["plate_label"], match["well"], match["guide"])
        for match in same_gene.json()["matches"]
    ] == [
        ("A549", "SQ00014610", "P24", "KRAS-2"),
        ("A549", "SQ00014611", "C01", "KRAS-1"),
        ("A549", "SQ00014611", "P24", "KRAS-2"),
        ("ES2", "SQ00014613", "C01", "KRAS-1"),
        ("ES2", "SQ00014613", "P24", "KRAS-2"),
    ]
    assert all(match["well_type"] == "Query guides" for match in same_gene.json()["matches"])

    assert controls.status_code == 200, controls.text
    assert controls.json() == {
        "matches": [
            {
                "path": str(plate),
                "plate_label": "SQ00014610",
                "cell_line": "A549",
                "well": "A01",
                "guide": "EMPTY",
                "is_control": True,
                "well_type": None,
            }
        ]
    }


@pytest.mark.parametrize(
    "params",
    [
        {"well": "C01"},
        {"well": "C01", "by": "plate"},
        {"well": "../C01", "by": "well"},
        {"well": "A" * 17 + "1", "by": "well"},
    ],
)
def test_packed_matches_route_rejects_invalid_queries(
    packed_root: Path,
    tmp_path: Path,
    params: dict[str, str],
) -> None:
    plate = (
        packed_root
        / make_test_data.PACKED_INDEX_NAME
        / "idr0080-A549"
        / "14610"
    )
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        linked = client.post("/api/datasets", headers=AUTH, json={"path": str(plate)})
        assert linked.status_code == 201, linked.text
        response = client.get(
            f"/api/datasets/{linked.json()['id']}/matches",
            headers=AUTH,
            params=params,
        )

    assert response.status_code == 422


def test_packed_matches_route_returns_not_found_for_unknown_well(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    plate = (
        packed_root
        / make_test_data.PACKED_INDEX_NAME
        / "idr0080-A549"
        / "14610"
    )
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        linked = client.post("/api/datasets", headers=AUTH, json={"path": str(plate)})
        assert linked.status_code == 201, linked.text
        response = client.get(
            f"/api/datasets/{linked.json()['id']}/matches",
            headers=AUTH,
            params={"well": "Z99", "by": "well"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown packed well: Z99"}


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("unknown_plate", "plate absent from the image index"),
        ("unknown_well", "well absent from the image index"),
        ("mixed_cell_line", "inconsistent 'cell_type'"),
        ("wrong_barcode", "expected 'SQ00014611'"),
        ("missing_guide", "empty 'idr_guide'"),
    ],
)
def test_packed_matches_route_rejects_invalid_metadata_targets(
    tmp_path: Path,
    corruption: str,
    message: str,
) -> None:
    root = make_test_data._write_packed_fixture(tmp_path / "generated")
    metadata_path = root / "metadata" / "metadata_idr0080.csv"
    with metadata_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or ())
        rows = [dict(row) for row in reader]

    if corruption == "unknown_plate":
        extra = dict(rows[0])
        extra.update(
            well_id="unknown_plate_A01",
            experiment_name="idr0080-A549",
            plate="99999",
            idr_plate_barcode="SQ00099999",
        )
        rows.append(extra)
    elif corruption == "unknown_well":
        extra = dict(rows[0])
        extra.update(well_id="unknown_well_Z99", address="Z99")
        rows.append(extra)
    elif corruption == "mixed_cell_line":
        next(
            row
            for row in rows
            if row["plate"] == "14611" and row["address"] == "C01"
        )["cell_type"] = "unexpected"
    elif corruption == "wrong_barcode":
        for row in rows:
            if row["plate"] == "14611":
                row["idr_plate_barcode"] = "WRONG"
    else:
        next(
            row
            for row in rows
            if row["plate"] == "14610" and row["address"] == "P24"
        )["idr_guide"] = ""

    with metadata_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    plate = root / make_test_data.PACKED_INDEX_NAME / "idr0080-A549" / "14610"
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        linked = client.post("/api/datasets", headers=AUTH, json={"path": str(plate)})
        assert linked.status_code == 201, linked.text
        response = client.get(
            f"/api/datasets/{linked.json()['id']}/matches",
            headers=AUTH,
            params={"well": "C01", "by": "gene"},
        )

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_missing_packed_metadata_does_not_block_images(tmp_path: Path) -> None:
    root = make_test_data._write_packed_fixture(tmp_path / "generated")
    (root / "metadata" / "metadata_idr0080.csv").unlink()
    plate = root / make_test_data.PACKED_INDEX_NAME / "idr0080-A549" / "14610"
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        linked = client.post("/api/datasets", headers=AUTH, json={"path": str(plate)})
        assert linked.status_code == 201, linked.text
        dataset_id = linked.json()["id"]
        metadata = client.get(f"/api/datasets/{dataset_id}/metadata", headers=AUTH)
        matches = client.get(
            f"/api/datasets/{dataset_id}/matches",
            headers=AUTH,
            params={"well": "A01", "by": "control"},
        )
        summary = client.get(f"/api/datasets/{dataset_id}/summary", headers=AUTH)
        chunk = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0",
            headers=AUTH,
        )

    assert metadata.status_code == 200
    assert metadata.json() == {"available": False, "plate": None, "wells": []}
    assert matches.status_code == 404
    assert matches.json() == {"detail": "Packed metadata is unavailable"}
    assert summary.status_code == 200, summary.text
    assert chunk.status_code == 200, chunk.text


def test_unknown_packed_index_uses_cached_generic_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_test_data._write_packed_fixture(tmp_path / "generated")
    source = root / make_test_data.PACKED_INDEX_NAME
    index = source.with_name("generic.index.parquet")
    source.rename(index)
    experiment = index / "idr0080-A549"
    plate = experiment / "14610"
    calls: list[int] = []
    original = dataserver_app.profile_for_catalogue

    def counted_profile(catalogue):
        calls.append(threading.get_ident())
        return original(catalogue)

    monkeypatch.setattr(dataserver_app, "profile_for_catalogue", counted_profile)
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(experiment)})
        linked = client.post("/api/datasets", headers=AUTH, json={"path": str(plate)})
        assert linked.status_code == 201, linked.text
        dataset_id = linked.json()["id"]
        summary = client.get(f"/api/datasets/{dataset_id}/summary", headers=AUTH)
        metadata = client.get(f"/api/datasets/{dataset_id}/metadata", headers=AUTH)
        group = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/zarr.json",
            headers=AUTH,
        )
        probe_tar = root / "idr0080-raw-SQ00014610.tar"
        probe_stat = probe_tar.stat()
        os.utime(
            probe_tar,
            ns=(probe_stat.st_atime_ns, probe_stat.st_mtime_ns + 2_000_000_000),
        )
        refreshed_summary = client.get(
            f"/api/datasets/{dataset_id}/summary",
            headers=AUTH,
        )
        chunk = client.get(
            f"/zarr/{dataset_id}/A01_s1.zarr/s0/c/0/0/0",
            headers=AUTH,
        )

    assert listing.status_code == 200, listing.text
    assert [entry["name"] for entry in listing.json()["entries"]] == ["14610", "14611"]
    assert linked.json()["name"] == "14610"
    assert summary.status_code == 200, summary.text
    channels = summary.json()["first_image"]["channels"]
    assert [(channel["name"], channel["color"], channel["window"]) for channel in channels] == [
        (str(index), None, None) for index in range(1, 6)
    ]
    assert all(channel["active"] is True for channel in channels)
    assert metadata.json() == {"available": False, "plate": None, "wells": []}
    assert group.status_code == 200, group.text
    assert group.json()["attributes"]["ome"]["omero"]["channels"] == [
        {"label": str(index), "active": True} for index in range(1, 6)
    ]
    assert refreshed_summary.status_code == 200, refreshed_summary.text
    assert chunk.status_code == 200, chunk.text
    assert len(calls) == 2
    assert all(call != threading.get_ident() for call in calls)


def test_concurrent_unknown_profile_resolution_is_single_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_test_data._write_packed_fixture(tmp_path / "generated")
    source = root / make_test_data.PACKED_INDEX_NAME
    index = source.with_name("generic.index.parquet")
    source.rename(index)
    experiment = index / "idr0080-A549"
    original = dataserver_app.profile_for_catalogue
    calls = 0
    calls_lock = threading.Lock()

    def counted_profile(catalogue):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.1)
        return original(catalogue)

    monkeypatch.setattr(dataserver_app, "profile_for_catalogue", counted_profile)
    barrier = threading.Barrier(8)

    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:

        def list_experiment() -> int:
            barrier.wait()
            return client.get(
                "/api/fs/list",
                headers=AUTH,
                params={"path": str(experiment)},
            ).status_code

        with ThreadPoolExecutor(max_workers=8) as executor:
            statuses = tuple(executor.map(lambda _index: list_experiment(), range(8)))

    assert statuses == (200,) * 8
    assert calls == 1


@pytest.mark.parametrize("suffix", [(), ("idr0080-A549",)])
def test_index_and_experiment_cannot_be_linked(
    packed_root: Path,
    tmp_path: Path,
    suffix: tuple[str, ...],
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    path = index.joinpath(*suffix)
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        response = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(path)},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "Link a packed plate, not an index or experiment"}


def test_parent_segments_are_forbidden_for_listing_and_linking(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    unsafe = f"{index}{os.sep}..{os.sep}idr0080-A549"
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        listing = client.get("/api/fs/list", headers=AUTH, params={"path": unsafe})
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": unsafe},
        )
    for response in (listing, linking):
        assert response.status_code == 403
        assert response.json() == {"detail": "Parent path segments are not allowed"}


@pytest.mark.parametrize(
    ("suffix", "message"),
    [
        (("unknown",), "Unknown packed experiment"),
        (("idr0080-A549", "99999"), "Unknown packed plate"),
        (("idr0080-A549", "14610", "extra"), "Unknown packed dataset path"),
    ],
)
def test_unknown_virtual_paths_return_not_found_for_listing_and_linking(
    packed_root: Path,
    tmp_path: Path,
    suffix: tuple[str, ...],
    message: str,
) -> None:
    index = (packed_root / make_test_data.PACKED_INDEX_NAME).resolve()
    unknown = index.joinpath(*suffix)
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(unknown)})
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(unknown)},
        )
    for response in (listing, linking):
        assert response.status_code == 404
        assert message in response.json()["detail"]


def test_fragment_and_mask_indexes_are_not_packed_routes(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    decoys = (
        packed_root / "index_fragments" / "SQ00014610.index.parquet",
        packed_root / "masks" / "fixture" / "Plate14610.index.parquet",
    )
    with TestClient(create_app(_settings(packed_root, tmp_path / "state"), TOKEN)) as client:
        for decoy in decoys:
            parent = client.get(
                "/api/fs/list",
                headers=AUTH,
                params={"path": str(decoy.parent)},
            )
            assert parent.status_code == 200, parent.text
            entry = next(item for item in parent.json()["entries"] if item["name"] == decoy.name)
            assert entry == _entry(decoy.name, decoy.resolve(), "file")

            listing = client.get(
                "/api/fs/list",
                headers=AUTH,
                params={"path": str(decoy)},
            )
            linking = client.post(
                "/api/datasets",
                headers=AUTH,
                json={"path": str(decoy)},
            )
            assert listing.status_code == 404
            assert linking.status_code == 404


def test_malformed_detected_catalogue_returns_unprocessable(tmp_path: Path) -> None:
    root = make_test_data._write_packed_fixture(tmp_path / "generated")
    source = pq.read_table(root / make_test_data.PACKED_INDEX_NAME)
    malformed = root / "malformed.index.parquet"
    pq.write_table(pa.concat_tables([source.slice(0, 1), source.slice(0, 1)]), malformed)

    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        listing = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(malformed)},
        )
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(malformed / "idr0080-A549" / "14610")},
        )
    for response in (listing, linking):
        assert response.status_code == 422
        assert "duplicate field/channel key" in response.json()["detail"]


def test_symlinked_index_outside_root_is_forbidden(tmp_path: Path) -> None:
    outside = make_test_data._write_packed_fixture(tmp_path / "outside-generated")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / make_test_data.PACKED_INDEX_NAME
    try:
        link.symlink_to(outside / make_test_data.PACKED_INDEX_NAME)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Creating a file symlink is unavailable: {exc}")

    virtual_plate = link / "idr0080-A549" / "14610"
    with TestClient(create_app(_settings(allowed, tmp_path / "state"), TOKEN)) as client:
        listing = client.get(
            "/api/fs/list",
            headers=AUTH,
            params={"path": str(link)},
        )
        linking = client.post(
            "/api/datasets",
            headers=AUTH,
            json={"path": str(virtual_plate)},
        )
    for response in (listing, linking):
        assert response.status_code == 403
        assert response.json() == {"detail": "Path is outside the configured data roots"}
