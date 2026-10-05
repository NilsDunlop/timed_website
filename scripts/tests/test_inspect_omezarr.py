import json
import subprocess
import sys
from pathlib import Path

from scripts import inspect_omezarr


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def inspect_folder(path: Path) -> dict:
    items, skipped, errors = inspect_omezarr.plan([path])
    return {
        "schema_version": inspect_omezarr.SCHEMA_VERSION,
        "items": list(inspect_omezarr.iter_records(items, all_fields=False, progress=False)),
        "skipped": skipped,
        "errors": errors,
    }


def make_v2_plate(folder: Path) -> Path:
    store = folder / "plate-without-ome-suffix.data"
    write_json(store / ".zgroup", {"zarr_format": 2})
    write_json(
        store / ".zattrs",
        {
            "bioformats2raw.layout": 3,
            "plate": {
                "name": "PB test plate",
                "rows": [{"name": "A"}, {"name": "B"}],
                "columns": [{"name": "1"}, {"name": "2"}],
                "acquisitions": [{"id": 0, "name": "time lapse"}],
                "wells": [{"path": "A/1"}, {"path": "B/2"}],
            },
        },
    )
    write_json(store / "OME" / ".zgroup", {"zarr_format": 2})
    write_json(store / "A" / "1" / ".zgroup", {"zarr_format": 2})
    write_json(
        store / "A" / "1" / ".zattrs",
        {"well": {"images": [{"path": "0", "acquisition": 0}, {"path": "1"}]}},
    )
    image = store / "A" / "1" / "0"
    write_json(image / ".zgroup", {"zarr_format": 2})
    write_json(
        image / ".zattrs",
        {
            "multiscales": [
                {
                    "version": "0.4",
                    "name": "field 0",
                    "axes": [
                        {"name": "t", "type": "time"},
                        {"name": "c", "type": "channel"},
                        {"name": "z", "type": "space"},
                        {"name": "y", "type": "space"},
                        {"name": "x", "type": "space"},
                    ],
                    "datasets": [
                        {
                            "path": "0",
                            "coordinateTransformations": [
                                {"type": "scale", "scale": [1, 1, 1, 0.5, 0.5]}
                            ],
                        },
                        {
                            "path": "1",
                            "coordinateTransformations": [
                                {"type": "scale", "scale": [1, 1, 1, 1, 1]}
                            ],
                        },
                    ],
                }
            ],
            "omero": {
                "channels": [
                    {
                        "label": "DAPI",
                        "color": "0000FF",
                        "window": {"min": 0, "max": 65535, "start": 100, "end": 5000},
                    },
                    {"label": "GFP", "color": "00FF00", "window": {"start": 50, "end": 4000}},
                ],
                "rdefs": {"model": "color"},
            },
        },
    )
    write_json(
        image / "0" / ".zarray",
        {
            "zarr_format": 2,
            "shape": [6, 3, 1, 512, 512],
            "chunks": [1, 1, 1, 256, 256],
            "dtype": "<u2",
            "compressor": {"id": "blosc", "cname": "zstd"},
            "filters": None,
        },
    )
    write_json(
        image / "1" / ".zarray",
        {
            "zarr_format": 2,
            "shape": [6, 3, 1, 256, 256],
            "chunks": [1, 1, 1, 256, 256],
            "dtype": "<u2",
            "compressor": None,
            "filters": [],
        },
    )
    write_json(image / "labels" / ".zgroup", {"zarr_format": 2})
    (image / "0" / "0.0.0.0.0").write_bytes(b"not metadata and not JSON")
    return store


def make_v3_image(folder: Path) -> Path:
    store = folder / "single-v3.data"
    axes = [
        {"name": "t", "type": "time"},
        {"name": "c", "type": "channel"},
        {"name": "z", "type": "space"},
        {"name": "y", "type": "space"},
        {"name": "x", "type": "space"},
    ]
    write_json(
        store / "zarr.json",
        {
            "zarr_format": 3,
            "node_type": "group",
            "attributes": {
                "multiscales": [
                    {
                        "version": "0.5",
                        "axes": axes,
                        "datasets": [{"path": "0"}, {"path": "1"}],
                    }
                ],
                "omero": {"channels": [{"label": "phase", "color": "FFFFFF"}]},
            },
        },
    )
    write_json(
        store / "0" / "zarr.json",
        {
            "zarr_format": 3,
            "node_type": "array",
            "shape": [6, 1, 1, 256, 256],
            "data_type": "uint16",
            "chunk_grid": {
                "name": "regular",
                "configuration": {"chunk_shape": [1, 1, 1, 128, 128]},
            },
            "codecs": [
                {
                    "name": "sharding_indexed",
                    "configuration": {
                        "chunk_shape": [1, 1, 1, 64, 64],
                        "codecs": [{"name": "bytes"}, {"name": "zstd"}],
                    },
                }
            ],
        },
    )
    write_json(
        store / "1" / "zarr.json",
        {
            "zarr_format": 3,
            "node_type": "array",
            "shape": [6, 1, 1, 128, 128],
            "data_type": "uint16",
            "chunk_grid": {
                "name": "regular",
                "configuration": {"chunk_shape": [1, 1, 1, 64, 64]},
            },
            "codecs": [{"name": "bytes"}, {"name": "zstd"}],
        },
    )
    return store


def make_bare_v3_array(path: Path, well: str, site: int) -> None:
    write_json(
        path / "zarr.json",
        {
            "zarr_format": 3,
            "node_type": "array",
            "shape": [48, 3, 2500, 2500],
            "data_type": "uint16",
            "chunk_grid": {
                "name": "regular",
                "configuration": {"chunk_shape": [48, 3, 2500, 2500]},
            },
            "codecs": [
                {
                    "name": "sharding_indexed",
                    "configuration": {
                        "chunk_shape": [1, 1, 625, 625],
                        "codecs": [{"name": "bytes"}, {"name": "blosc"}],
                    },
                }
            ],
            "fill_value": 0,
            "attributes": {
                "timed": {
                    "well": well,
                    "site": site,
                    "axes": ["t", "c", "y", "x"],
                    "channels_nm": [405, 505, 625],
                }
            },
        },
    )


def test_inventory_reads_metadata_only_and_summarizes_v2_and_v3(
    tmp_path: Path, monkeypatch
) -> None:
    make_v2_plate(tmp_path)
    make_v3_image(tmp_path)
    write_json(tmp_path / "plain-zarr" / ".zgroup", {"zarr_format": 2})
    write_json(tmp_path / "plain-zarr" / ".zattrs", {"unrelated": True})
    (tmp_path / "CONVERSION.md").write_text("notes", encoding="utf-8")

    metadata_reads = []
    original_read_json = inspect_omezarr._read_json

    def tracked_read_json(path: Path):
        metadata_reads.append(path)
        return original_read_json(path)

    monkeypatch.setattr(inspect_omezarr, "_read_json", tracked_read_json)
    inventory = inspect_folder(tmp_path)

    assert [store["name"] for store in inventory["items"]] == [
        "plate-without-ome-suffix.data",
        "single-v3.data",
    ]
    assert {path.name for path in metadata_reads} <= {".zgroup", ".zattrs", ".zarray", "zarr.json"}

    plate = inventory["items"][0]
    assert plate["errors"] == []
    assert plate["kind"] == "plate"
    assert plate["zarr_format"] == 2
    assert plate["ome_zarr_version"] == "0.4"
    assert plate["bioformats2raw"] == {"present": True, "layout": 3, "ome_folder": True}
    assert plate["plate"]["rows"] == ["A", "B"]
    assert plate["plate"]["columns"] == ["1", "2"]
    assert plate["plate"]["well_count"] == 2
    assert plate["plate"]["sample_well_paths"] == ["A/1", "B/2"]
    assert plate["plate"]["first_well"]["fields"] == [
        {"path": "0", "acquisition": 0},
        {"path": "1", "acquisition": None},
    ]
    assert plate["image"]["axes"] == ["t", "c", "z", "y", "x"]
    assert plate["image"]["shape"] == [6, 3, 1, 512, 512]
    assert plate["image"]["levels"][0]["chunks"] == [1, 1, 1, 256, 256]
    assert plate["image"]["levels"][0]["compressor"]["cname"] == "zstd"
    assert plate["image"]["omero"]["channels"][0]["label"] == "DAPI"
    assert plate["image"]["labels_group"] is True

    image = inventory["items"][1]
    assert image["errors"] == []
    assert image["kind"] == "image"
    assert image["zarr_format"] == 3
    assert image["ome_zarr_version"] == "0.5"
    assert image["image"]["levels"][0]["chunks"] == [1, 1, 1, 64, 64]
    assert image["image"]["levels"][0]["shard_shape"] == [1, 1, 1, 128, 128]
    assert image["image"]["levels"][1]["shard_shape"] is None


def test_inventory_records_store_errors_and_rejects_metadata_path_escape(tmp_path: Path) -> None:
    make_v3_image(tmp_path)
    broken = tmp_path / "broken.zarr"
    broken.mkdir()
    (broken / ".zgroup").write_text("not json", encoding="utf-8")
    escaping = tmp_path / "escaping.zarr"
    write_json(
        escaping / "zarr.json",
        {
            "zarr_format": 3,
            "node_type": "group",
            "attributes": {
                "multiscales": [
                    {"version": "0.5", "axes": ["y", "x"], "datasets": [{"path": "../outside"}]}
                ]
            },
        },
    )
    write_json(tmp_path / "outside" / "zarr.json", {"zarr_format": 3, "node_type": "array"})

    inventory = inspect_folder(tmp_path)
    stores = {store["name"]: store for store in inventory["items"]}

    assert stores["single-v3.data"]["errors"] == []
    assert stores["broken.zarr"]["errors"]
    assert "Could not read" in stores["broken.zarr"]["errors"][0]
    assert "escapes the store" in stores["escaping.zarr"]["errors"][0]


def test_cli_prints_one_json_document(tmp_path: Path) -> None:
    make_v2_plate(tmp_path)
    script = Path(inspect_omezarr.__file__)
    completed = subprocess.run(
        [sys.executable, str(script), "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    inventory = json.loads(completed.stdout)
    assert completed.stderr == ""
    assert inventory["schema_version"] == inspect_omezarr.SCHEMA_VERSION
    assert inventory["items"][0]["name"] == "plate-without-ome-suffix.data"


def test_inventory_summarizes_real_timed_collection_layout(tmp_path: Path) -> None:
    collection = tmp_path / "PB100087_P1.1.zarr"
    make_bare_v3_array(collection / "B02_s1.zarr", "B02", 1)
    make_bare_v3_array(collection / "B02_s2.zarr", "B02", 2)
    make_bare_v3_array(collection / "C03_s1.zarr", "C03", 1)
    write_json(collection / "acquisition_config.json", {"plate_name": "PB100087_P1.1"})
    (collection / "CONVERSION.md").write_text("metadata only", encoding="utf-8")

    inventory = inspect_folder(tmp_path)
    record = inventory["items"][0]

    assert record["kind"] == "collection"
    assert record["field_store_count"] == 3
    assert record["well_count"] == 2
    assert record["rows"] == ["B", "C"]
    assert record["columns"] == [2, 3]
    assert record["fields_per_well"] == {"1": 1, "2": 1}
    assert record["field_error_count"] == 0
    assert record["first_field"]["kind"] == "array"
    assert record["first_field"]["array"]["dimension_names"] is None
    assert record["signatures"][0]["count"] == 3
    assert record["signatures"][0]["signature"]["chunks"] == [1, 1, 625, 625]
    assert record["signatures"][0]["signature"]["shard_shape"] == [48, 3, 2500, 2500]
