"""Opt-in, read-only check of the real TIMED masks for PB100140_P2.1.

Set TIMED_TEST_REAL_TIMED_MASKS=1 (and optionally TIMED_REAL_TIMED_COPY_ROOT,
default D:/TIMED) to run it against a copy holding zarr/, the presentation
export's corrected/ folder and the production segmentation's seg/ folder. For
the export's four fields it checks every frame's nucleus and cell counts against
the export's own counts tables; the production run must match the same tables'
nuclei and TiLu-corrected cells, apart from one known frame. Outlines are
compared with zarr's decoding of the same frames, and no file or folder in
either mask tree may change.
"""

from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path

import httpx
import numpy as np
import pytest
import zarr
from png_reader import read_png
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.mask_outlines import outline_mask
from timed_dataserver.timed_mask_config import TimedMaskMapping

TOKEN = "real-timed-masks-read-only-test"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PLATE_NAME = "PB100140_P2.1"
FIELDS = (("C05", 1), ("C15", 1), ("F13", 1), ("G07", 1))
TIMEPOINTS = 48
COUNT_TABLES = ("counts.csv", "counts_C05_F13.csv")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _export_counts(
    masks_root: Path, cells_column: str = "n_cells_raw"
) -> dict[tuple[str, int, int], dict[str, int]]:
    counts: dict[tuple[str, int, int], dict[str, int]] = {}
    for name in COUNT_TABLES:
        with (masks_root / name).open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = (row["well"], int(row["site"]), int(row["time_index"]))
                counts[key] = {"nuclei": int(row["n_nuclei"]), "cells": int(row[cells_column])}
    return counts


PRODUCTION_MODEL = "R4_cpsam_dS_corrected"
PRODUCTION_RUNS = (("cells", PRODUCTION_MODEL), ("nuclei", f"{PRODUCTION_MODEL}_nuclei"))
# The one frame where the production cells differ from the export's TiLu-corrected
# cells: one of the export's 40 cells is part of a neighbour in the production run.
KNOWN_DIFFERENCE = {("C15", 1, 31): {"nuclei": 37, "cells": 39}}


def _run_array(seg_plate: Path, well: str, site: int, run: str) -> Path:
    field = f"{well}_s{site}"
    return seg_plate / "sites" / field / "stage1" / run / "labels" / PLATE_NAME / f"{field}.zarr"


@pytest.mark.asyncio
async def test_real_production_segmentation_matches_the_corrected_export_read_only(
    tmp_path: Path,
) -> None:
    if os.environ.get("TIMED_TEST_REAL_TIMED_MASKS") != "1":
        pytest.skip("set TIMED_TEST_REAL_TIMED_MASKS=1 to test the real TIMED masks")

    copy_root = Path(os.environ.get("TIMED_REAL_TIMED_COPY_ROOT", "D:/TIMED"))
    data_root = copy_root / "zarr"
    masks_root = copy_root / "corrected"
    seg_root = copy_root / "seg"
    seg_plate = seg_root / PLATE_NAME
    assert (data_root / f"{PLATE_NAME}.zarr").is_dir() and (seg_plate / "sites").is_dir()
    protected = [
        _run_array(seg_plate, well, site, run) / name
        for well, site in (*FIELDS, ("C03", 1))
        for _kind, run in PRODUCTION_RUNS
        for name in ("zarr.json", "0.0.0")
    ]

    def entries() -> list[str]:
        return sorted(path.relative_to(seg_root).as_posix() for path in seg_root.rglob("*"))

    original_entries = entries()
    original_digests = {path: _digest(path) for path in protected}
    corrected = _export_counts(masks_root, "n_cells_corr")
    assert len(corrected) == len(FIELDS) * TIMEPOINTS
    for key, counts in KNOWN_DIFFERENCE.items():
        assert corrected[key] == {**counts, "cells": counts["cells"] + 1}

    settings = ServerSettings(
        data_roots=(data_root,),
        state_dir=tmp_path / "state",
        environment="local",
        timed_masks=(
            TimedMaskMapping(data_root, masks_root),
            TimedMaskMapping(data_root, seg_root, layout="production"),
        ),
    )
    transport = httpx.ASGITransport(app=create_app(settings, TOKEN))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=120
    ) as client:
        linked = await client.post(
            "/api/datasets", headers=AUTH, json={"path": str(data_root / f"{PLATE_NAME}.zarr")}
        )
        assert linked.status_code == 201, linked.text
        masks = f"/api/datasets/{linked.json()['id']}/masks"

        for well, site in FIELDS:
            listing = (
                await client.get(masks, headers=AUTH, params={"well": well, "site": site})
            ).json()["models"]
            assert [(model["model_tag"], model["default"]) for model in listing] == [
                (PRODUCTION_MODEL, True),
                ("masks_raw", False),
            ]
            for t in range(TIMEPOINTS):
                counted = await client.get(
                    f"{masks}/{PRODUCTION_MODEL}/{well}/{site}", headers=AUTH, params={"t": t}
                )
                assert counted.status_code == 200, counted.text
                expected = KNOWN_DIFFERENCE.get((well, site, t), corrected[(well, site, t)])
                assert counted.json()["counts"] == expected, (well, site, t)
        unexported = await client.get(masks, headers=AUTH, params={"well": "C03", "site": 1})
        assert [model["model_tag"] for model in unexported.json()["models"]] == [
            PRODUCTION_MODEL
        ]

        frames = (
            (FIELDS[0], 0),
            (FIELDS[3], TIMEPOINTS - 1),
            (("C15", 1), 31),
            (("C03", 1), 0),
        )
        for (well, site), t in frames:
            for kind, run in PRODUCTION_RUNS:
                outline = await client.get(
                    f"{masks}/{PRODUCTION_MODEL}/{well}/{site}/{kind}.png",
                    headers=AUTH,
                    params={"t": t},
                )
                assert outline.status_code == 200, outline.text
                stored = zarr.open_array(_run_array(seg_plate, well, site, run), mode="r")
                labels = np.asarray(stored[t])
                assert np.array_equal(read_png(outline.content).pixels, outline_mask(labels, 2))

    assert entries() == original_entries
    assert {path: _digest(path) for path in protected} == original_digests


@pytest.mark.asyncio
async def test_real_timed_masks_follow_the_exports_counts_read_only(tmp_path: Path) -> None:
    if os.environ.get("TIMED_TEST_REAL_TIMED_MASKS") != "1":
        pytest.skip("set TIMED_TEST_REAL_TIMED_MASKS=1 to test the real TIMED masks")

    copy_root = Path(os.environ.get("TIMED_REAL_TIMED_COPY_ROOT", "D:/TIMED"))
    data_root = copy_root / "zarr"
    masks_root = copy_root / "corrected"
    plate = data_root / f"{PLATE_NAME}.zarr"
    masks_plate = masks_root / f"{PLATE_NAME}.zarr"
    assert plate.is_dir() and masks_plate.is_dir()
    protected = [
        masks_plate / f"{well}_s{site}.zarr" / array / name
        for well, site in FIELDS
        for array in ("masks_raw", "nuclei")
        for name in ("zarr.json", "0.0.0")
    ]
    def entries() -> list[str]:
        return sorted(path.relative_to(masks_root).as_posix() for path in masks_root.rglob("*"))

    original_entries = entries()
    original_digests = {path: _digest(path) for path in protected}
    expected = _export_counts(masks_root)
    assert len(expected) == len(FIELDS) * TIMEPOINTS

    settings = ServerSettings(
        data_roots=(data_root,),
        state_dir=tmp_path / "state",
        environment="local",
        timed_masks=(TimedMaskMapping(data_root, masks_root),),
    )
    transport = httpx.ASGITransport(app=create_app(settings, TOKEN))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=120
    ) as client:
        linked = await client.post("/api/datasets", headers=AUTH, json={"path": str(plate)})
        assert linked.status_code == 201, linked.text
        masks = f"/api/datasets/{linked.json()['id']}/masks"

        for well, site in FIELDS:
            listing = await client.get(masks, headers=AUTH, params={"well": well, "site": site})
            assert [model["model_tag"] for model in listing.json()["models"]] == ["masks_raw"]
            for t in range(TIMEPOINTS):
                counted = await client.get(
                    f"{masks}/masks_raw/{well}/{site}", headers=AUTH, params={"t": t}
                )
                assert counted.status_code == 200, counted.text
                assert counted.json()["counts"] == expected[(well, site, t)], (
                    well,
                    site,
                    t,
                )
        unmasked = await client.get(masks, headers=AUTH, params={"well": "C03", "site": 1})
        assert unmasked.json() == {"models": []}
        missing_t = await client.get(f"{masks}/masks_raw/C05/1", headers=AUTH)
        assert missing_t.status_code == 422

        for (well, site), t in zip(FIELDS[::3], (0, TIMEPOINTS - 1), strict=True):
            for kind, array in (("cells", "masks_raw"), ("nuclei", "nuclei")):
                outline = await client.get(
                    f"{masks}/masks_raw/{well}/{site}/{kind}.png", headers=AUTH, params={"t": t}
                )
                assert outline.status_code == 200, outline.text
                stored = zarr.open_array(masks_plate / f"{well}_s{site}.zarr" / array, mode="r")
                labels = np.asarray(stored[t])
                assert np.array_equal(read_png(outline.content).pixels, outline_mask(labels, 2))

    assert entries() == original_entries
    assert {path: _digest(path) for path in protected} == original_digests
