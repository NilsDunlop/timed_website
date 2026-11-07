"""The fixture's tar+wal/v1 mask stores follow the contract in docs/data/IDR0080.md."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
import tifffile

from scripts import make_test_data, mask_stores

SHAPE = (make_test_data.PACKED_HEIGHT, make_test_data.PACKED_WIDTH)
WELLS = [well.address for well in make_test_data.PACKED_WELLS]


@pytest.fixture(scope="module")
def fixture_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_test_data._write_packed_fixture(tmp_path_factory.mktemp("mask-fixture"))


def _complete_records(log: Path) -> tuple[list[dict[str, Any]], bytes]:
    data = log.read_bytes()
    *lines, partial = data.split(b"\n")
    return [json.loads(line) for line in lines], partial


def _expected_counts(model: str, plate: int, well: str, site: int) -> dict[str, int]:
    return mask_stores.field_masks(model, plate, WELLS.index(well), site, SHAPE).counts


def _store_paths(root: Path) -> list[tuple[str, str, int]]:
    return [
        (model, experiment, plate)
        for experiment, plate, _cell_line in make_test_data.PACKED_PLATES
        for model in (mask_stores.DEFAULT_MODEL, mask_stores.STOCK_MODEL)
        if model == mask_stores.DEFAULT_MODEL or plate == 14610
    ]


def test_models_cover_the_expected_plates(fixture_root: Path) -> None:
    masks = fixture_root / "masks"
    assert sorted(path.name for path in masks.iterdir()) == [
        mask_stores.STOCK_MODEL,
        "fixture",
        mask_stores.OTHER_FORMAT_MODEL,
        mask_stores.DEFAULT_MODEL,
    ]
    assert not (masks / "fixture" / "manifest.json").exists()
    for model, store_format in (
        (mask_stores.DEFAULT_MODEL, "tar+wal/v1"),
        (mask_stores.STOCK_MODEL, "tar+wal/v1"),
        (mask_stores.OTHER_FORMAT_MODEL, "zarr/v0"),
    ):
        manifest = json.loads((masks / model / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["model_tag"] == model
        assert manifest["frozen"] is False
        assert manifest["schema"] == {
            "mask_dtype": "uint16",
            "kinds": ["nuclei", "cells", "cytoplasm"],
            "store_format": store_format,
            "qc_schema_version": 1,
        }
    plate_tars = sorted(
        path.relative_to(masks).as_posix() for path in masks.rglob("*.masks.tar")
    )
    assert plate_tars == [
        "cpsam_stock_R2_top999/idr0080-A549/Plate14610.masks.tar",
        "idr0080_cpsam_B90/idr0080-A549/Plate14610.masks.tar",
        "idr0080_cpsam_B90/idr0080-A549/Plate14611.masks.tar",
        "idr0080_cpsam_B90/idr0080-ES2/Plate14613.masks.tar",
    ]


def test_every_committed_member_matches_the_contract(fixture_root: Path) -> None:
    for model, experiment, plate in _store_paths(fixture_root):
        directory = fixture_root / "masks" / model / experiment
        tar = (directory / f"Plate{plate}.masks.tar").read_bytes()
        records, _partial = _complete_records(directory / f"Plate{plate}.wal.jsonl")
        assert records, (model, plate)
        previous_end = 0
        for record in records:
            assert set(record) == {"well", "site", "tar_end", "masks", "qc", "git_sha"}
            tar_end = int(record["tar_end"])
            assert tar_end % 512 == 0
            assert tar_end >= previous_end
            previous_end = tar_end
            assert [member["kind"] for member in record["masks"]] == list(mask_stores.KINDS)
            for member in record["masks"]:
                offset, size = int(member["offset"]), int(member["size"])
                assert offset >= 512 and offset + size <= tar_end
                header = tarfile.TarInfo.frombuf(tar[offset - 512 : offset], "utf-8", "strict")
                assert tar[offset - 512 + 257 : offset - 512 + 265] == tarfile.POSIX_MAGIC
                assert header.name == (
                    f"{experiment}/Plate{plate}/"
                    f"{record['well']}_s{record['site']}_{member['kind']}.tiff"
                )
                assert header.size == size
                with tifffile.TiffFile(io.BytesIO(tar[offset : offset + size])) as image:
                    assert len(image.pages) == 1
                    assert int(image.pages[0].compression) == 8
                    labels = image.pages[0].asarray()
                assert labels.shape == SHAPE
                assert labels.dtype == np.dtype("uint16")
                count = int(labels.max())
                assert np.array_equal(np.unique(labels), np.arange(count + 1))
                qc_key = {"nuclei": "nuclei_final", "cells": "cell_final"}.get(
                    str(member["kind"]), "cytoplasm_final"
                )
                assert record["qc"][qc_key] == count
        assert len(tar) >= previous_end


def test_last_records_hold_the_known_counts(fixture_root: Path) -> None:
    for model, experiment, plate in _store_paths(fixture_root):
        log = fixture_root / "masks" / model / experiment / f"Plate{plate}.wal.jsonl"
        records, _partial = _complete_records(log)
        last = {(record["well"], record["site"]): record for record in records}
        assert set(last) == {(well, site) for well in WELLS for site in make_test_data.PACKED_SITES}
        for (well, site), record in last.items():
            counts = {
                "nuclei": record["qc"]["nuclei_final"],
                "cells": record["qc"]["cell_final"],
                "cytoplasm": record["qc"]["cytoplasm_final"],
            }
            assert counts == _expected_counts(model, plate, str(well), int(site))
    # The reference field differs between the two models, as on the real plate.
    assert _expected_counts(mask_stores.DEFAULT_MODEL, 14610, "A01", 1) == {
        "nuclei": 4,
        "cells": 2,
        "cytoplasm": 2,
    }
    assert _expected_counts(mask_stores.STOCK_MODEL, 14610, "A01", 1) == {
        "nuclei": 4,
        "cells": 6,
        "cytoplasm": 6,
    }


def test_rewritten_field_keeps_its_superseded_record(fixture_root: Path) -> None:
    plate, well, site = mask_stores.REWRITTEN_FIELD
    directory = fixture_root / "masks" / mask_stores.DEFAULT_MODEL / "idr0080-A549"
    log = directory / f"Plate{plate}.wal.jsonl"
    records, partial = _complete_records(log)
    assert partial == b""
    rewrites = [record for record in records if (record["well"], record["site"]) == (well, site)]
    assert len(rewrites) == 2
    assert records[-1] is rewrites[1]
    assert rewrites[0]["qc"]["nuclei_final"] == 1
    assert rewrites[1]["qc"]["nuclei_final"] == _expected_counts(
        mask_stores.DEFAULT_MODEL, plate, well, site
    )["nuclei"]


def test_write_in_progress_leaves_a_half_written_line_and_uncommitted_members(
    fixture_root: Path,
) -> None:
    plate, well, site = mask_stores.IN_PROGRESS_FIELD
    directory = fixture_root / "masks" / mask_stores.DEFAULT_MODEL / "idr0080-A549"
    records, partial = _complete_records(directory / f"Plate{plate}.wal.jsonl")
    assert partial and not partial.endswith(b"\n")
    with pytest.raises(json.JSONDecodeError):
        json.loads(partial)
    tar = (directory / f"Plate{plate}.masks.tar").read_bytes()
    committed = max(int(record["tar_end"]) for record in records)
    assert len(tar) > committed
    assert tar.endswith(b"\0" * 8)
    with tarfile.open(fileobj=io.BytesIO(tar[committed:]), mode="r:") as archive:
        assert [member.name for member in archive.getmembers()] == [
            f"idr0080-A549/Plate{plate}/{well}_s{site}_{kind}.tiff" for kind in mask_stores.KINDS
        ]
    assert not (directory / f"Plate{plate}.index.parquet").exists()
    assert not (directory / f"Plate{plate}.qc.parquet").exists()


def test_finalised_snapshots_equal_the_deduplicated_log(fixture_root: Path) -> None:
    for model, experiment, plate in _store_paths(fixture_root):
        if (model, plate) == (mask_stores.DEFAULT_MODEL, mask_stores.IN_PROGRESS_PLATE):
            continue
        directory = fixture_root / "masks" / model / experiment
        records, _partial = _complete_records(directory / f"Plate{plate}.wal.jsonl")
        last = {(record["well"], record["site"]): record for record in records}
        expected = {
            (well, site, member["kind"]): (member["offset"], member["size"])
            for (well, site), record in last.items()
            for member in record["masks"]
        }
        index = pq.read_table(directory / f"Plate{plate}.index.parquet").to_pylist()
        assert {
            (row["well"], row["site"], row["kind"]): (row["offset"], row["size"]) for row in index
        } == expected
        assert {(row["experiment"], row["plate"]) for row in index} == {(experiment, plate)}
        qc = pq.read_table(directory / f"Plate{plate}.qc.parquet").to_pylist()
        assert len(qc) == len(last)
        tar = (directory / f"Plate{plate}.masks.tar").read_bytes()
        committed = max(int(record["tar_end"]) for record in records)
        assert len(tar) % (20 * 512) == 0
        assert tar[committed:] == b"\0" * (len(tar) - committed)


def test_leases_and_reader_decoys_are_present(fixture_root: Path) -> None:
    for model, experiment, plate in _store_paths(fixture_root):
        lease = fixture_root / "masks" / model / experiment / f"Plate{plate}.lease"
        assert set(json.loads(lease.read_text(encoding="utf-8"))) == {
            "pid",
            "host",
            "slurm_job_id",
            "slurm_array_task_id",
            "acquired_at",
        }
    assert (fixture_root / "run_ledger.jsonl").is_file()
    assert (
        fixture_root
        / "profiles"
        / mask_stores.DEFAULT_MODEL
        / "per_experiment"
        / "idr0080-A549_plate14610_mad_exclusions.json"
    ).is_file()


def test_corrupt_store_variants(tmp_path: Path) -> None:
    fields = [
        (well, site, mask_stores.field_masks(mask_stores.DEFAULT_MODEL, 14610, index, site, SHAPE))
        for index, well in enumerate(WELLS)
        for site in make_test_data.PACKED_SITES
    ]
    short = mask_stores.write_plate_store(
        tmp_path / "short",
        mask_stores.DEFAULT_MODEL,
        "idr0080-A549",
        14610,
        fields,
        tar_shortfall=512,
    )
    committed = max(int(record["tar_end"]) for record in short.records)
    assert short.tar.stat().st_size == committed - 512

    broken = mask_stores.write_plate_store(
        tmp_path / "broken",
        mask_stores.DEFAULT_MODEL,
        "idr0080-A549",
        14610,
        fields,
        broken_line=3,
    )
    lines = broken.log.read_bytes().split(b"\n")
    assert lines[-1] == b""
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[3])
    for position in (0, 1, 2, 4):
        json.loads(lines[position])


def test_mask_fixture_is_deterministic(tmp_path: Path) -> None:
    first = make_test_data._write_packed_fixture(tmp_path / "first")
    second = make_test_data._write_packed_fixture(tmp_path / "second")
    for path in sorted((first / "masks").rglob("*")):
        if path.is_file():
            twin = second / path.relative_to(first)
            assert path.read_bytes() == twin.read_bytes(), path.relative_to(first)
