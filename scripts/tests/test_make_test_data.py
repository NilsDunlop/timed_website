import json
from pathlib import Path

import numpy as np
import zarr

from scripts import inspect_omezarr, make_test_data


def test_field_pixels_are_deterministic_and_time_varying() -> None:
    first = make_test_data.make_field_data(42, height=64, width=64)
    second = make_test_data.make_field_data(42, height=64, width=64)
    different_seed = make_test_data.make_field_data(43, height=64, width=64)

    assert np.array_equal(first, second)
    assert not np.array_equal(first, different_seed)
    assert not np.array_equal(first[0], first[-1])
    assert not np.array_equal(first[:, 0], first[:, 1])
    assert float(first.std()) > 100


def test_generator_mirrors_inventory_and_writes_native_ome_formats(tmp_path: Path) -> None:
    make_test_data.generate_test_data(tmp_path)
    plate = tmp_path / "test_plate.zarr"
    comparison_plate = tmp_path / "test_plate_2.zarr"
    field_names = sorted(path.name for path in plate.glob("*.zarr"))

    assert field_names == [
        "A01_s1.zarr",
        "A01_s2.zarr",
        "A02_s1.zarr",
        "A02_s2.zarr",
        "B01_s1.zarr",
        "B01_s2.zarr",
    ]
    assert not (plate / "B02_s1.zarr").exists()
    assert sorted(path.name for path in comparison_plate.glob("*.zarr")) == [
        "B01_s1.zarr",
        "B01_s2.zarr",
    ]
    metadata = tmp_path / "metadata"
    acquisition = json.loads((metadata / "acquisition" / "test_plate.json").read_text("utf-8"))
    assert acquisition["plate_name"] == "test_plate"
    assert [channel["name"] for channel in acquisition["channels"]] == [
        "Fluorescence 405 nm Ex",
        "Fluorescence 505 nm Ex",
        "Fluorescence 625 nm Ex",
    ]
    metadata_csv = (metadata / "csv" / "test_plate.csv").read_text("utf-8")
    assert "Compound Alpha" in metadata_csv
    assert "solvent_control" in metadata_csv
    comparison_acquisition = json.loads(
        (metadata / "acquisition" / "test_plate_2.json").read_text("utf-8")
    )
    assert comparison_acquisition["plate_name"] == "test_plate_2"
    comparison_metadata_csv = (metadata / "csv" / "test_plate_2.csv").read_text("utf-8")
    assert "S1.2" in comparison_metadata_csv
    assert "DMSO" in comparison_metadata_csv

    field = zarr.open_array(plate / "A01_s1.zarr", mode="r")
    assert field.metadata.zarr_format == 3
    assert field.shape == (6, 3, 512, 512)
    assert field.dtype == np.dtype("uint16")
    assert field.chunks == (1, 1, 128, 128)
    assert field.shards == (6, 3, 512, 512)
    assert field.metadata.dimension_names is None
    assert dict(field.attrs)["timed"] == {
        "well": "A01",
        "site": 1,
        "axes": ["t", "c", "y", "x"],
        "channels_nm": [405, 505, 625],
        "t_indices": [0, 1, 2, 3, 4, 5],
    }
    assert not np.array_equal(field[0], field[5])
    assert not np.array_equal(field[:, 0], field[:, 1])

    array_metadata = json.loads((plate / "A01_s1.zarr" / "zarr.json").read_text("utf-8"))
    sharding = array_metadata["codecs"][0]
    assert sharding["name"] == "sharding_indexed"
    assert sharding["configuration"]["chunk_shape"] == [1, 1, 128, 128]
    assert sharding["configuration"]["index_location"] == "end"
    blosc = sharding["configuration"]["codecs"][1]
    assert blosc == {
        "name": "blosc",
        "configuration": {
            "typesize": 2,
            "cname": "zstd",
            "clevel": 5,
            "shuffle": "shuffle",
            "blocksize": 0,
        },
    }

    v04 = zarr.open_group(tmp_path / "test_image_v04.zarr", mode="r")
    v04_multiscales = v04.attrs["multiscales"]
    assert v04.metadata.zarr_format == 2
    assert v04_multiscales[0]["version"] == "0.4"
    assert len(v04_multiscales[0]["datasets"]) == 1
    assert [axis["name"] for axis in v04_multiscales[0]["axes"]] == ["t", "c", "y", "x"]
    assert [channel["label"] for channel in v04.attrs["omero"]["channels"]] == [
        "405 nm",
        "505 nm",
        "625 nm",
    ]

    v05 = zarr.open_group(tmp_path / "test_image_v05.zarr", mode="r")
    ome = v05.attrs["ome"]
    v05_multiscales = ome["multiscales"]
    assert v05.metadata.zarr_format == 3
    assert ome["version"] == "0.5"
    assert len(v05_multiscales[0]["datasets"]) == 1
    v05_array = v05[v05_multiscales[0]["datasets"][0]["path"]]
    assert v05_array.metadata.dimension_names == ("t", "c", "y", "x")
    assert v05_array.shape == (6, 3, 512, 512)

    items, skipped, errors = inspect_omezarr.plan([tmp_path])
    records = list(inspect_omezarr.iter_records(items, all_fields=False, progress=False))
    by_name = {record["name"]: record for record in records}
    assert skipped == [
        {
            "path": str(metadata),
            "reason": "no Zarr metadata and no *.zarr inside",
        }
    ]
    assert errors == []
    assert set(by_name) == {
        "test_plate.zarr",
        "test_plate_2.zarr",
        "test_image_v04.zarr",
        "test_image_v05.zarr",
    }
    collection = by_name["test_plate.zarr"]
    assert collection["well_count"] == 3
    assert collection["field_store_count"] == 6
    assert collection["fields_per_well"] == {"2": 3}
    assert collection["field_error_count"] == 0
    assert collection["signatures"][0]["signature"]["chunks"] == [1, 1, 128, 128]
    assert collection["signatures"][0]["signature"]["shard_shape"] == [6, 3, 512, 512]
    comparison_collection = by_name["test_plate_2.zarr"]
    assert comparison_collection["well_count"] == 1
    assert comparison_collection["field_store_count"] == 2
    assert comparison_collection["fields_per_well"] == {"2": 1}
    assert by_name["test_image_v04.zarr"]["ome_zarr_version"] == "0.4"
    assert by_name["test_image_v05.zarr"]["ome_zarr_version"] == "0.5"
