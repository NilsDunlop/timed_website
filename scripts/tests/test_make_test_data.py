import csv
import io
import json
import tarfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import tifffile
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
    packed = tmp_path / "packed" / "idr0080-like"
    assert (packed / "idr0080-raw.index.parquet").is_file()
    plate = tmp_path / "test_plate.zarr"
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
    for plate_name, *_metadata in make_test_data.COMPARISON_PLATES:
        comparison_plate = tmp_path / f"{plate_name}.zarr"
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
    for plate_name, plate_id, *_metadata in make_test_data.COMPARISON_PLATES:
        comparison_acquisition = json.loads(
            (metadata / "acquisition" / f"{plate_name}.json").read_text("utf-8")
        )
        assert comparison_acquisition["plate_name"] == plate_name
        comparison_metadata_csv = (metadata / "csv" / f"{plate_name}.csv").read_text("utf-8")
        assert plate_id in comparison_metadata_csv
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
    skip_field = next(iter(set(skipped[0]) - {"path"}))
    assert skipped == [
        {
            "path": str(metadata),
            "reason": "no Zarr metadata and no *.zarr inside",
        },
        {
            "path": str(tmp_path / "packed"),
            "reason": "no Zarr metadata and no *.zarr inside",
        },
        {
            "path": str(tmp_path / "qc-export"),
            skip_field: "no Zarr metadata and no *.zarr inside",
        },
        {
            "path": str(tmp_path / "qc-export-next"),
            skip_field: "no Zarr metadata and no *.zarr inside",
        },
        {
            # Production runs sit below plain plate folders, not *.zarr stores.
            "path": str(tmp_path / "timed-seg"),
            skip_field: "no Zarr metadata and no *.zarr inside",
        },
    ]
    assert errors == []
    assert set(by_name) == {
        "test_plate.zarr",
        "test_plate_2.zarr",
        "test_plate_3.zarr",
        "test_plate_4.zarr",
        "test_image_v04.zarr",
        "test_image_v05.zarr",
        "timed-masks",
    }
    # The mask folder mirrors plates but holds no image fields.
    masks = by_name["timed-masks"]
    assert (masks["well_count"], masks["signatures"]) == (0, [])
    assert masks["unparsed_store_names"] == ["test_plate.zarr", "test_plate_2.zarr"]
    collection = by_name["test_plate.zarr"]
    assert collection["well_count"] == 3
    assert collection["field_store_count"] == 6
    assert collection["fields_per_well"] == {"2": 3}
    assert collection["field_error_count"] == 0
    assert collection["signatures"][0]["signature"]["chunks"] == [1, 1, 128, 128]
    assert collection["signatures"][0]["signature"]["shard_shape"] == [6, 3, 512, 512]
    for plate_name, *_metadata in make_test_data.COMPARISON_PLATES:
        comparison_collection = by_name[f"{plate_name}.zarr"]
        assert comparison_collection["well_count"] == 1
        assert comparison_collection["field_store_count"] == 2
        assert comparison_collection["fields_per_well"] == {"2": 1}
    assert by_name["test_image_v04.zarr"]["ome_zarr_version"] == "0.4"
    assert by_name["test_image_v05.zarr"]["ome_zarr_version"] == "0.5"


def test_packed_fixture_matches_idr0080_contract(tmp_path: Path) -> None:
    root = make_test_data._write_packed_fixture(tmp_path)
    index_path = root / make_test_data.PACKED_INDEX_NAME
    index = pq.read_table(index_path)

    assert index.column_names == [
        "relpath",
        "experiment",
        "plate",
        "well",
        "site",
        "channel",
        "offset",
        "size",
        "shard",
    ]
    for name in ("plate", "site", "channel", "offset", "size"):
        assert pa.types.is_int64(index.schema.field(name).type)
    assert index.num_rows == 150

    rows = index.to_pylist()
    assert {(row["experiment"], row["plate"]) for row in rows} == {
        ("idr0080-A549", 14610),
        ("idr0080-A549", 14611),
        ("idr0080-ES2", 14613),
    }
    assert {row["well"] for row in rows} == {"A01", "A02", "B01", "C01", "P24"}
    assert {row["site"] for row in rows} == {1, 2}
    assert {row["channel"] for row in rows} == {1, 2, 3, 4, 5}

    fields: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        fields[(row["experiment"], row["plate"], row["well"], row["site"])].append(row)
    assert len(fields) == 30
    for field_rows in fields.values():
        assert [row["channel"] for row in field_rows] == [1, 2, 3, 4, 5]
        assert [row["offset"] for row in field_rows] == sorted(
            int(row["offset"]) for row in field_rows
        )

    rows_by_shard: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        rows_by_shard[str(row["shard"])].append(row)
    assert set(rows_by_shard) == {
        "idr0080-raw-SQ00014610.tar",
        "idr0080-raw-SQ00014611.tar",
        "idr0080-raw-SQ00014613.tar",
    }

    well_indices = {well.address: index for index, well in enumerate(make_test_data.PACKED_WELLS)}
    for shard, shard_rows in rows_by_shard.items():
        shard_path = root / shard
        with tarfile.open(shard_path, mode="r:") as archive:
            members = archive.getmembers()
            assert [member.name for member in members] == [
                f"idr0080/raw/{row['relpath']}" for row in shard_rows
            ]
            for row, member in zip(shard_rows, members, strict=True):
                assert member.offset_data == row["offset"]
                assert member.size == row["size"]
                extracted = archive.extractfile(member)
                assert extracted is not None
                payload = extracted.read()
                with tifffile.TiffFile(io.BytesIO(payload)) as image:
                    assert len(image.pages) == 1
                    assert image.pages[0].compression == 1
                    plane = image.asarray()
                assert plane.shape == (64, 64)
                assert plane.dtype == np.dtype("uint16")
                assert np.array_equal(
                    plane,
                    make_test_data.make_packed_plane(
                        int(row["plate"]),
                        well_indices[str(row["well"])],
                        int(row["site"]),
                        int(row["channel"]),
                    ),
                )
        with shard_path.open("rb") as handle:
            for row in shard_rows:
                handle.seek(int(row["offset"]) - 512 + 257)
                assert handle.read(6) == b"ustar\x00"

    metadata_path = root / "metadata" / "metadata_idr0080.csv"
    with metadata_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        metadata_rows = list(reader)
        assert reader.fieldnames == list(make_test_data.IDR0080_METADATA_COLUMNS)
    assert len(metadata_rows) == 15
    assert {
        (row["experiment_name"], int(row["plate"]), row["address"]) for row in metadata_rows
    } == {(row["experiment"], row["plate"], row["well"]) for row in rows}
    empty = next(row for row in metadata_rows if row["address"] == "A01")
    assert empty["treatment"] == "EMPTY_control"
    assert empty["well_type_label"] == ""
    cutting = next(row for row in metadata_rows if row["address"] == "A02")
    assert cutting["well_type_label"] == "Cutting controls"
    assert cutting["idr_control_category"] == "Chr2"
    kras_rows = [row for row in metadata_rows if row["address"] in {"C01", "P24"}]
    assert {row["gene"] for row in kras_rows} == {"KRAS"}
    assert {row["idr_guide"] for row in kras_rows} == {"KRAS-1", "KRAS-2"}

    fragment_path = root / "index_fragments" / "SQ00014610.index.parquet"
    fragment = pq.read_table(fragment_path)
    assert fragment.schema == index.schema
    assert fragment.num_rows == 50
    assert all(
        not (fragment_path.parent / shard).exists() for shard in fragment["shard"].to_pylist()
    )

    mask_index = pq.read_table(root / "masks" / "fixture" / "Plate14610.index.parquet")
    assert "kind" in mask_index.column_names
    assert "channel" not in mask_index.column_names
    assert mask_index["kind"].to_pylist() == ["nuclei"]
