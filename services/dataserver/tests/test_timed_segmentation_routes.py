"""Production TIMED segmentation over HTTP: the pinned run model beside the export's."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from png_reader import read_png
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.mask_outlines import outline_mask
from timed_dataserver.timed_mask_config import TimedMaskMapping

from scripts import make_test_data, timed_mask_stores
from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "timed-segmentation-route-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SHAPE = (make_test_data.HEIGHT, make_test_data.WIDTH)
MODEL = timed_mask_stores.CELL_RUN
SEGMENTATION = timed_mask_stores.TIMED_SEGMENTATION_DIRECTORY
PRODUCTION = {
    "model_tag": MODEL,
    "recipe": "Cellpose-SAM cells (TiLu-corrected 505 + raw 625 nm)",
    "kinds": ["nuclei", "cells"],
    "frozen": True,
    "created_at": None,
    "available": True,
    "default": True,
}
MASKS_RAW = {
    "model_tag": "masks_raw",
    "recipe": "Cellpose-SAM cells (raw 505 + 625 nm) · classical nuclei (raw 625 nm)",
    "kinds": ["nuclei", "cells"],
    "frozen": True,
    "created_at": None,
    "available": True,
    "default": False,
}


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory, write_audit: WriteAudit) -> Path:
    root = tmp_path_factory.mktemp("timed-segmentation-routes")
    generate_test_data(root)
    write_audit.watch(root)
    return root


def _settings(
    root: Path,
    state: Path,
    *,
    export: bool = True,
    production: bool = True,
) -> ServerSettings:
    mappings = []
    if export:
        mappings.append(TimedMaskMapping(root, root / timed_mask_stores.TIMED_MASKS_DIRECTORY))
    if production:
        mappings.append(TimedMaskMapping(root, root / SEGMENTATION, layout="production"))
    return ServerSettings(
        data_roots=(root,),
        state_dir=state,
        environment="local",
        timed_masks=tuple(mappings),
    )


@pytest.fixture
def client(data_root: Path, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(data_root, tmp_path / "state"), TOKEN)) as test_client:
        yield test_client


def _link(client: TestClient, path: Path) -> str:
    response = client.post("/api/datasets", headers=AUTH, json={"path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _get(client: TestClient, dataset: str, route: str, **params: Any) -> httpx.Response:
    return client.get(f"/api/datasets/{dataset}/{route}", headers=AUTH, params=params)


def _field_index(plate: str, well: str, site: int) -> int:
    return timed_mask_stores.SEGMENTED_FIELDS.index((plate, well, site))


def test_the_production_model_comes_first_beside_the_export(
    client: TestClient,
    data_root: Path,
) -> None:
    plate = _link(client, data_root / "test_plate.zarr")
    assert _get(client, plate, "masks").json() == {"models": []}
    for well, site in (("A01", 1), ("A02", 1), ("b01", 1)):
        listing = _get(client, plate, "masks", well=well, site=site).json()
        assert listing == {"models": [PRODUCTION, MASKS_RAW]}
    # A field that only the production run covers offers it alone.
    assert _get(client, plate, "masks", well="A02", site=2).json() == {"models": [PRODUCTION]}
    for well, site in (("A01", 2), ("B01", 2), ("B02", 1)):
        assert _get(client, plate, "masks", well=well, site=site).json() == {"models": []}
    other = _link(client, data_root / "test_plate_2.zarr")
    listing = _get(client, other, "masks", well="B01", site=1).json()
    assert listing == {"models": [PRODUCTION, MASKS_RAW]}


def test_either_layout_alone_offers_its_own_model_as_the_default(
    data_root: Path,
    tmp_path: Path,
) -> None:
    for export, production, expected, absent in (
        (False, True, PRODUCTION, "masks_raw"),
        (True, False, {**MASKS_RAW, "default": True}, MODEL),
    ):
        settings = _settings(
            data_root, tmp_path / f"state-{export}", export=export, production=production
        )
        with TestClient(create_app(settings, TOKEN)) as client:
            plate = _link(client, data_root / "test_plate.zarr")
            listing = _get(client, plate, "masks", well="A01", site=1).json()
            assert listing == {"models": [expected]}
            missing = _get(client, plate, f"masks/{absent}/A01/1", t=0)
            assert missing.status_code == 404
            assert missing.json() == {"detail": f"Unknown mask model: {absent}"}


def test_production_counts_follow_the_timepoint(client: TestClient, data_root: Path) -> None:
    for plate_name, well, site in timed_mask_stores.SEGMENTED_FIELDS:
        dataset = _link(client, data_root / f"{plate_name}.zarr")
        index = _field_index(plate_name, well, site)
        for t in range(make_test_data.TIMEPOINTS):
            response = _get(client, dataset, f"masks/{MODEL}/{well}/{site}", t=t)
            assert response.status_code == 200, response.text
            assert response.json() == {
                "model_tag": MODEL,
                "well": well,
                "site": site,
                "counts": {
                    "nuclei": timed_mask_stores.production_nucleus_count(index, t),
                    "cells": timed_mask_stores.production_cell_count(index, t),
                },
            }
    # The export's counts on a shared field stay its own.
    dataset = _link(client, data_root / "test_plate.zarr")
    exported = _get(client, dataset, "masks/masks_raw/A01/1", t=1).json()["counts"]
    assert exported == {
        "nuclei": timed_mask_stores.nucleus_count(0, 1),
        "cells": timed_mask_stores.cell_count(0, 1),
    }


def test_production_outlines_draw_the_requested_frame(client: TestClient, data_root: Path) -> None:
    dataset = _link(client, data_root / "test_plate.zarr")
    index = _field_index("test_plate", "A02", 2)
    for t in range(make_test_data.TIMEPOINTS):
        for kind, frame in (
            ("cells", timed_mask_stores.production_frame_labels),
            ("nuclei", timed_mask_stores.production_frame_nuclei),
        ):
            response = _get(client, dataset, f"masks/{MODEL}/A02/2/{kind}.png", t=t)
            assert response.status_code == 200
            expected = outline_mask(frame(index, t, *SHAPE), 2)
            assert np.array_equal(read_png(response.content).pixels, expected)
    production = _get(client, dataset, f"masks/{MODEL}/A01/1/cells.png", t=0)
    exported = _get(client, dataset, "masks/masks_raw/A01/1/cells.png", t=0)
    assert production.headers["etag"] != exported.headers["etag"]
    assert not np.array_equal(
        read_png(production.content).pixels, read_png(exported.content).pixels
    )


def test_timepoints_are_required_and_bounded_for_production(
    client: TestClient,
    data_root: Path,
) -> None:
    dataset = _link(client, data_root / "test_plate.zarr")
    route = f"masks/{MODEL}/A01/1"
    missing = _get(client, dataset, route)
    assert missing.status_code == 422
    assert missing.json() == {"detail": "TIMED masks have one plane per timepoint: give t"}
    beyond = _get(client, dataset, route, t=make_test_data.TIMEPOINTS)
    assert beyond.status_code == 422
    assert beyond.json() == {"detail": "Timepoint 6 is outside this field's 6 timepoints (0 to 5)"}
    assert _get(client, dataset, f"masks/{MODEL}/A01/2", t=0).json() == {
        "detail": "No masks for A01 site 2"
    }


def _isolated_copy(data_root: Path, tmp_path: Path) -> Path:
    root = tmp_path / "copy"
    root.mkdir()
    for name in ("test_plate.zarr", timed_mask_stores.TIMED_MASKS_DIRECTORY, SEGMENTATION):
        shutil.copytree(data_root / name, root / name)
    return root


def _run_dir(root: Path, run: str = MODEL, well: str = "A01", site: int = 1) -> Path:
    return timed_mask_stores.run_array_dir(root / SEGMENTATION, "test_plate", well, site, run)


def test_a_run_that_stores_another_field_answers_409_without_absolute_paths(
    data_root: Path,
    tmp_path: Path,
) -> None:
    root = _isolated_copy(data_root, tmp_path)
    metadata_path = _run_dir(root) / "zarr.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["attributes"]["well"] = "A02"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    label = f"test_plate/sites/A01_s1/stage1/{MODEL}/labels/test_plate/A01_s1.zarr"
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        dataset = _link(client, root / "test_plate.zarr")
        for route in (f"masks/{MODEL}/A01/1", f"masks/{MODEL}/A01/1/cells.png"):
            response = _get(client, dataset, route, t=0)
            assert response.status_code == 409
            assert response.json() == {
                "detail": (
                    f"TIMED masks {label} are invalid: "
                    "the stored well 'A02' differs from its path's 'A01'"
                )
            }
            assert str(tmp_path) not in response.text
        # The nuclei run is still valid, and the export is untouched.
        assert _get(client, dataset, f"masks/{MODEL}/A01/1/nuclei.png", t=0).status_code == 200
        assert _get(client, dataset, "masks/masks_raw/A01/1", t=0).status_code == 200


def test_a_regenerated_run_is_served_without_a_restart(data_root: Path, tmp_path: Path) -> None:
    root = _isolated_copy(data_root, tmp_path)
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        dataset = _link(client, root / "test_plate.zarr")
        before = _get(client, dataset, f"masks/{MODEL}/A01/1", t=0).json()["counts"]
        # The export's discs for field 0: two cells at t 0, where the run had five.
        regenerated = timed_mask_stores.field_labels(0, make_test_data.TIMEPOINTS, *SHAPE)
        timed_mask_stores.write_run_array(
            _run_dir(root),
            regenerated,
            range(make_test_data.TIMEPOINTS),
            plate="test_plate",
            well="A01",
            site=1,
            run_id=MODEL,
        )
        after = _get(client, dataset, f"masks/{MODEL}/A01/1", t=0).json()["counts"]
        assert after["cells"] == int(regenerated[0].max()) != before["cells"]
        assert after["nuclei"] == before["nuclei"]


def test_a_field_with_only_the_cell_run_offers_cells(data_root: Path, tmp_path: Path) -> None:
    root = _isolated_copy(data_root, tmp_path)
    shutil.rmtree(_run_dir(root, timed_mask_stores.NUCLEUS_RUN, "A02", 2))
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        dataset = _link(client, root / "test_plate.zarr")
        assert _get(client, dataset, "masks", well="A02", site=2).json() == {
            "models": [PRODUCTION]
        }
        counts = _get(client, dataset, f"masks/{MODEL}/A02/2", t=1).json()["counts"]
        index = _field_index("test_plate", "A02", 2)
        assert counts == {"cells": timed_mask_stores.production_cell_count(index, 1)}
        missing = _get(client, dataset, f"masks/{MODEL}/A02/2/nuclei.png", t=1)
        assert missing.status_code == 404
        assert missing.json() == {"detail": "No nuclei mask for A02 site 2"}


def test_the_segmentation_folder_is_never_browsable_linkable_or_listed(
    client: TestClient,
    data_root: Path,
) -> None:
    seg = data_root / SEGMENTATION
    root_listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(data_root)})
    names = {entry["name"] for entry in root_listing.json()["entries"]}
    assert SEGMENTATION not in names
    assert timed_mask_stores.TIMED_MASKS_DIRECTORY not in names
    run_array = _run_dir(data_root)
    for path in (str(seg), str(seg / "test_plate" / "sites"), str(run_array), SEGMENTATION):
        browsed = client.get("/api/fs/list", headers=AUTH, params={"path": path})
        assert browsed.status_code == 403
        assert browsed.json() == {"detail": "Configured mask folders are not browsable"}
        linked = client.post("/api/datasets", headers=AUTH, json={"path": path})
        assert linked.status_code == 403
        assert linked.json() == {"detail": "Configured mask folders cannot be linked"}


@pytest.mark.needs_symlinks
def test_symlinks_never_lead_out_of_the_segmentation_folder(
    data_root: Path,
    tmp_path: Path,
) -> None:
    root = _isolated_copy(data_root, tmp_path)
    site = root / SEGMENTATION / "test_plate" / "sites" / "A02_s2"
    outside = tmp_path / "outside" / "A02_s2"
    shutil.move(site, outside)
    try:
        site.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    with TestClient(create_app(_settings(root, tmp_path / "state"), TOKEN)) as client:
        dataset = _link(client, root / "test_plate.zarr")
        assert _get(client, dataset, "masks", well="A02", site=2).json() == {"models": []}
        refused = _get(client, dataset, f"masks/{MODEL}/A02/2", t=0)
        assert refused.status_code == 404
        assert refused.json() == {"detail": "No masks for A02 site 2"}
        listing = _get(client, dataset, "masks", well="A01", site=1).json()
        assert listing == {"models": [PRODUCTION, MASKS_RAW]}
