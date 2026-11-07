"""Opt-in, read-only check of the real TIMED masks for four PB100140_P2.1 fields.

Set TIMED_TEST_REAL_TIMED_MASKS=1 (and optionally TIMED_REAL_TIMED_COPY_ROOT,
default D:/TIMED) to run it against a copy holding zarr/ and the presentation
export's corrected/ folder. It checks every frame's nucleus and cell counts
against the export's own counts tables, compares outlines with zarr's decoding
of the same frames, and proves that no file or folder in the mask tree changed.
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


def _export_counts(masks_root: Path) -> dict[tuple[str, int, int], dict[str, int]]:
    counts: dict[tuple[str, int, int], dict[str, int]] = {}
    for name in COUNT_TABLES:
        with (masks_root / name).open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = (row["well"], int(row["site"]), int(row["time_index"]))
                counts[key] = {"nuclei": int(row["n_nuclei"]), "cells": int(row["n_cells_raw"])}
    return counts


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
