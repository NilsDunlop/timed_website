from __future__ import annotations

import csv
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
import timed_dataserver.packed_metadata as packed_metadata
from timed_dataserver.models import PlateMetadataResponse, WellMetadataSummary
from timed_dataserver.packed import PackedCatalogueCache, PackedPlate
from timed_dataserver.packed_metadata import (
    PackedMetadataCache,
    PackedMetadataCatalog,
    PackedMetadataError,
)
from timed_dataserver.packed_profiles import IDR0080_FIXTURE_PROFILE

from scripts import make_test_data


@pytest.fixture
def packed_root(tmp_path: Path) -> Path:
    return make_test_data._write_packed_fixture(tmp_path / "generated")


def _catalogue(root: Path) -> PackedMetadataCatalog:
    return PackedMetadataCatalog(
        root / make_test_data.PACKED_INDEX_NAME,
        IDR0080_FIXTURE_PROFILE,
    )


def test_idr0080_metadata_maps_plate_acquisition_labels_colors_and_wells(
    packed_root: Path,
) -> None:
    response = _catalogue(packed_root).for_plate("idr0080-A549", 14610)

    assert response.available is True
    assert response.plate is not None
    assert response.plate.plate_id == "SQ00014610"
    assert response.plate.plate_barcode == "SQ00014610"
    assert response.plate.cell_line == "A549"
    assert response.plate.project == "Cell Health"
    assert response.plate.acquisition is not None
    assert response.plate.acquisition.timepoint_count == 1
    assert response.plate.acquisition.sites_per_well == 2
    assert [channel.name for channel in response.plate.acquisition.channels] == [
        "DNA",
        "ER",
        "AGP",
        "RNA",
        "Mito",
    ]
    assert response.labels is not None
    assert response.labels.model_dump() == {
        "compound": "Gene",
        "moa": "Well type",
        "control": "EMPTY control",
    }
    assert response.category_colors == {
        "Query guides": "#3b82a0",
        "Cutting controls": "#b86b35",
        "Non-cutting controls": "#9367a8",
    }

    wells = {well.well: well for well in response.wells}
    assert list(wells) == ["A01", "A02", "B01", "C01", "P24"]
    assert wells["A01"].model_dump(mode="json") == {
        "well": "A01",
        "well_type": "CRISPR",
        "compound": "EMPTY control",
        "dose_um": None,
        "is_control": True,
        "moa": None,
        "target": None,
        "pathway": None,
        "mechanism_of_action": None,
        "details": [
            {"label": "Guide", "value": "EMPTY"},
            {"label": "Control category", "value": "EMPTY"},
        ],
    }
    assert wells["A02"].is_control is False
    assert wells["A02"].moa == "Cutting controls"
    assert wells["B01"].moa == "Non-cutting controls"
    assert wells["C01"].model_dump(mode="json")["details"] == [
        {"label": "Guide", "value": "KRAS-1"},
        {"label": "Ensembl ID", "value": "ENSG00000133703"},
        {"label": "Control category", "value": "targeting"},
    ]
    assert wells["P24"].compound == "KRAS"
    assert wells["P24"].well_type == "CRISPR"


def test_metadata_csv_is_loaded_once_and_responses_are_cached_per_plate(
    packed_root: Path,
) -> None:
    catalogue = _catalogue(packed_root)
    first = catalogue.for_plate("idr0080-A549", 14610)
    assert catalogue.for_plate("idr0080-A549", 14610) is first

    (packed_root / IDR0080_FIXTURE_PROFILE.metadata_path).unlink()
    second = catalogue.for_plate("idr0080-A549", 14611)
    assert second.available is True
    assert second.plate is not None
    assert second.plate.plate_id == "SQ00014611"
    packed = PackedCatalogueCache().get(packed_root / make_test_data.PACKED_INDEX_NAME)
    matches = catalogue.matches(packed, "idr0080-A549", 14610, "C01", "well")
    assert len(matches.matches) == 2


def test_metadata_matches_are_sorted_before_the_200_result_limit(
    packed_root: Path,
) -> None:
    metadata_path = packed_root / IDR0080_FIXTURE_PROFILE.metadata_path
    with metadata_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or ())
        rows = [dict(row) for row in reader]

    added_plates = range(20000, 20205)
    template = next(
        row
        for row in rows
        if row["plate"] == "14610" and row["address"] == "C01"
    )
    for plate in added_plates:
        row = dict(template)
        row.update(
            well_id=f"extra_{plate}_C01",
            experiment_name="idr0080-ZZZ",
            plate=str(plate),
            cell_type="ZZZ",
            idr_cell_line="ZZZ",
            idr_plate_barcode=f"SQ000{plate}",
        )
        rows.append(row)
    with metadata_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    packed = PackedCatalogueCache().get(packed_root / make_test_data.PACKED_INDEX_NAME)
    expanded_plates = dict(packed.plates)
    for plate in added_plates:
        expanded_plates[("idr0080-ZZZ", plate)] = PackedPlate(
            wells=("C01",),
            sites=(1,),
            channels=(1,),
        )
    expanded = replace(packed, plates=expanded_plates)

    response = _catalogue(packed_root).matches(
        expanded,
        "idr0080-A549",
        14610,
        "C01",
        "gene",
    )

    assert len(response.matches) == 200
    keys = [(match.cell_line, match.plate_label, match.well) for match in response.matches]
    assert keys == sorted(keys)
    assert response.matches[-1].plate_label == "SQ00020194"


def test_metadata_load_is_single_flight_across_worker_threads(
    packed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = _catalogue(packed_root)
    original = catalogue._read_groups
    barrier = threading.Barrier(16)
    count_lock = threading.Lock()
    load_count = 0

    def counted_read():
        nonlocal load_count
        with count_lock:
            load_count += 1
        time.sleep(0.02)
        return original()

    monkeypatch.setattr(catalogue, "_read_groups", counted_read)

    def request(index: int) -> PlateMetadataResponse:
        barrier.wait()
        plate = 14610 if index % 2 == 0 else 14611
        return catalogue.for_plate("idr0080-A549", plate)

    with ThreadPoolExecutor(max_workers=16) as executor:
        responses = list(executor.map(request, range(16)))

    assert load_count == 1
    assert all(response.available for response in responses)
    assert len({id(response) for response in responses}) == 2


def test_missing_or_profileless_metadata_is_unavailable_and_omits_new_keys(
    packed_root: Path,
    tmp_path: Path,
) -> None:
    (packed_root / IDR0080_FIXTURE_PROFILE.metadata_path).unlink()
    missing = _catalogue(packed_root).for_plate("idr0080-A549", 14610)
    assert missing.model_dump(mode="json") == {
        "available": False,
        "plate": None,
        "wells": [],
    }

    profileless = replace(IDR0080_FIXTURE_PROFILE, metadata_path=None)
    unavailable = PackedMetadataCache().for_plate(
        tmp_path / "does-not-exist.index.parquet",
        profileless,
        "generic",
        1,
    )
    assert unavailable.model_dump(mode="json") == {
        "available": False,
        "plate": None,
        "wells": [],
    }

    legacy_well = WellMetadataSummary(well="A01")
    assert "details" not in legacy_well.model_dump(mode="json")


def test_metadata_rejects_unknown_plate_and_paths_outside_index_root(
    packed_root: Path,
) -> None:
    catalogue = _catalogue(packed_root)
    with pytest.raises(PackedMetadataError, match="no rows"):
        catalogue.for_plate("idr0080-HCC44", 14616)

    escaping = replace(IDR0080_FIXTURE_PROFILE, metadata_path="../outside.csv")
    with pytest.raises(PackedMetadataError, match="escapes"):
        PackedMetadataCatalog(
            packed_root / make_test_data.PACKED_INDEX_NAME,
            escaping,
        ).for_plate("idr0080-A549", 14610)


@pytest.mark.needs_symlinks
def test_metadata_rejects_symlink_escape(packed_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.csv"
    shutil.copyfile(
        packed_root / IDR0080_FIXTURE_PROFILE.metadata_path,
        outside,
    )
    link = packed_root / "metadata" / "outside-link.csv"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("This platform cannot create test symlinks")

    escaping = replace(
        IDR0080_FIXTURE_PROFILE,
        metadata_path="metadata/outside-link.csv",
    )
    with pytest.raises(PackedMetadataError, match="escapes"):
        PackedMetadataCatalog(
            packed_root / make_test_data.PACKED_INDEX_NAME,
            escaping,
        ).for_plate("idr0080-A549", 14610)


def test_metadata_rejects_a_descriptor_redirected_after_path_validation(
    packed_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("untrusted\n", encoding="utf-8")
    original_open = packed_metadata.os.open

    def redirected_open(_path: object, flags: int) -> int:
        return original_open(outside, flags)

    monkeypatch.setattr(packed_metadata.os, "open", redirected_open)
    with pytest.raises(PackedMetadataError, match="changed while opening"):
        _catalogue(packed_root).for_plate("idr0080-A549", 14610)
