from __future__ import annotations

import json
from dataclasses import replace
from types import MappingProxyType

import pytest
from ome_zarr_models.v05.image import Image
from timed_dataserver.packed import PackedCatalogue, PackedCatalogueCache, PackedVirtualPath
from timed_dataserver.packed_facade import (
    PackedChunkRequest,
    PackedFacadeNotFoundError,
    PackedMetadataRequest,
    packed_array_metadata_bytes,
    packed_group_metadata_bytes,
    packed_plate_summary,
    packed_well_fields,
    parse_packed_resource,
)
from timed_dataserver.packed_profiles import IDR0080_FIXTURE_PROFILE, PackedCodec

from scripts import make_test_data


@pytest.fixture(scope="module")
def catalogue(tmp_path_factory: pytest.TempPathFactory) -> PackedCatalogue:
    output = tmp_path_factory.mktemp("packed-facade")
    root = make_test_data._write_packed_fixture(output)
    return PackedCatalogueCache().get(root / make_test_data.PACKED_INDEX_NAME)


def _plate(
    catalogue: PackedCatalogue,
    experiment: str = "idr0080-A549",
    plate: int = 14610,
) -> PackedVirtualPath:
    return PackedVirtualPath(
        index_path=catalogue.index_path,
        catalogue=catalogue,
        experiment=experiment,
        plate=plate,
    )


def _json_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def test_plate_summary_uses_profile_geometry_and_sparse_catalogue_wells(
    catalogue: PackedCatalogue,
) -> None:
    summary = packed_plate_summary(_plate(catalogue), IDR0080_FIXTURE_PROFILE)

    assert summary.name == "SQ00014610"
    assert summary.path == ""
    assert summary.rows == list("ABCDEFGHIJKLMNOP")
    assert summary.columns == [str(value) for value in range(1, 25)]
    assert summary.acquisitions == []
    assert [well.model_dump() for well in summary.wells] == [
        {"row": "A", "column": "1", "path": "A01"},
        {"row": "A", "column": "2", "path": "A02"},
        {"row": "B", "column": "1", "path": "B01"},
        {"row": "C", "column": "1", "path": "C01"},
        {"row": "P", "column": "24", "path": "P24"},
    ]
    assert summary.ome_zarr_version == "0.5"
    assert summary.zarr_format == 3

    image = summary.first_image
    assert image is not None
    assert image.name == "A01_s1"
    assert image.path == "A01_s1.zarr"
    assert [axis.model_dump() for axis in image.axes] == [
        {"name": "c", "type": "channel", "unit": None},
        {"name": "y", "type": "space", "unit": None},
        {"name": "x", "type": "space", "unit": None},
    ]
    assert image.levels[0].model_dump() == {
        "path": "s0",
        "shape": [5, 64, 64],
        "chunks": [1, 32, 32],
        "shard_shape": None,
        "dtype": "uint16",
        "coordinate_transformations": [{"type": "scale", "scale": [1, 1, 1]}],
    }
    assert [(channel.name, channel.color) for channel in image.channels] == [
        ("DNA", "0000FF"),
        ("ER", "00FF00"),
        ("RNA", "00FFFF"),
        ("AGP", "FF0000"),
        ("Mito", "FF00FF"),
    ]
    assert [channel.active for channel in image.channels] == [True, True, False, True, False]
    assert image.channels[0].window is not None
    assert image.channels[0].window.model_dump() == {
        "min": 0.0,
        "max": 27_000.0,
        "start": 292.0,
        "end": 11_552.0,
    }


def test_plate_summary_selects_cell_line_specific_windows(
    catalogue: PackedCatalogue,
) -> None:
    summary = packed_plate_summary(
        _plate(catalogue, experiment="idr0080-ES2", plate=14613),
        IDR0080_FIXTURE_PROFILE,
    )

    assert summary.first_image is not None
    dna = summary.first_image.channels[0]
    assert dna.window is not None
    assert dna.window.model_dump() == {
        "min": 0.0,
        "max": 27_000.0,
        "start": 234.0,
        "end": 12_971.0,
    }


def test_well_fields_come_from_actual_catalogue_keys(catalogue: PackedCatalogue) -> None:
    fields = {
        key: members
        for key, members in catalogue.fields.items()
        if key != ("idr0080-A549", 14610, "A01", 2)
    }
    sparse = replace(catalogue, fields=MappingProxyType(fields))
    response = packed_well_fields(_plate(sparse), "A01")

    assert response.model_dump() == {
        "well_path": "A01",
        "fields": [{"path": "A01_s1.zarr", "acquisition": None}],
    }
    with pytest.raises(PackedFacadeNotFoundError, match="Unknown packed well"):
        packed_well_fields(_plate(catalogue), "D01")


def test_group_metadata_bytes_are_exact_and_include_appendix_b_omero() -> None:
    content = packed_group_metadata_bytes(
        IDR0080_FIXTURE_PROFILE,
        "idr0080-A549",
        "A01_s1",
    )
    expected = {
        "attributes": {
            "ome": {
                "version": "0.5",
                "multiscales": [
                    {
                        "name": "A01_s1",
                        "axes": [
                            {"name": "c", "type": "channel"},
                            {"name": "y", "type": "space"},
                            {"name": "x", "type": "space"},
                        ],
                        "datasets": [
                            {
                                "path": "s0",
                                "coordinateTransformations": [
                                    {"type": "scale", "scale": [1, 1, 1]}
                                ],
                            }
                        ],
                    }
                ],
                "omero": {
                    "channels": [
                        {
                            "label": "DNA",
                            "color": "0000FF",
                            "active": True,
                            "window": {
                                "min": 0,
                                "max": 27_000,
                                "start": 292,
                                "end": 11_552,
                            },
                        },
                        {
                            "label": "ER",
                            "color": "00FF00",
                            "active": True,
                            "window": {
                                "min": 0,
                                "max": 45_000,
                                "start": 438,
                                "end": 17_554,
                            },
                        },
                        {
                            "label": "RNA",
                            "color": "00FFFF",
                            "active": False,
                            "window": {
                                "min": 0,
                                "max": 26_000,
                                "start": 554,
                                "end": 9_283,
                            },
                        },
                        {
                            "label": "AGP",
                            "color": "FF0000",
                            "active": True,
                            "window": {
                                "min": 0,
                                "max": 14_000,
                                "start": 151,
                                "end": 5_035,
                            },
                        },
                        {
                            "label": "Mito",
                            "color": "FF00FF",
                            "active": False,
                            "window": {
                                "min": 0,
                                "max": 15_000,
                                "start": 324,
                                "end": 4_066,
                            },
                        },
                    ]
                },
            }
        },
        "zarr_format": 3,
        "node_type": "group",
    }
    assert content == _json_bytes(expected)


def test_array_metadata_bytes_are_exact() -> None:
    expected = {
        "zarr_format": 3,
        "node_type": "array",
        "shape": [5, 64, 64],
        "data_type": "uint16",
        "chunk_grid": {
            "name": "regular",
            "configuration": {"chunk_shape": [1, 32, 32]},
        },
        "chunk_key_encoding": {
            "name": "default",
            "configuration": {"separator": "/"},
        },
        "fill_value": 0,
        "codecs": [
            {"name": "bytes", "configuration": {"endian": "little"}},
            {
                "name": "blosc",
                "configuration": {
                    "typesize": 2,
                    "cname": "zstd",
                    "clevel": 3,
                    "shuffle": "shuffle",
                    "blocksize": 0,
                },
            },
        ],
        "dimension_names": ["c", "y", "x"],
        "attributes": {},
    }
    assert packed_array_metadata_bytes(IDR0080_FIXTURE_PROFILE) == _json_bytes(expected)


def test_array_metadata_uses_every_profile_codec_value() -> None:
    profile = replace(
        IDR0080_FIXTURE_PROFILE,
        codec=PackedCodec(
            cname="lz4",
            clevel=7,
            shuffle="bitshuffle",
            blocksize=256,
            typesize=4,
        ),
    )

    metadata = json.loads(packed_array_metadata_bytes(profile))

    assert metadata["codecs"][1]["configuration"] == {
        "typesize": 4,
        "cname": "lz4",
        "clevel": 7,
        "shuffle": "bitshuffle",
        "blocksize": 256,
    }


def test_combined_metadata_validates_with_pinned_ome_zarr_models() -> None:
    group = json.loads(
        packed_group_metadata_bytes(
            IDR0080_FIXTURE_PROFILE,
            "idr0080-A549",
            "A01_s1",
        )
    )
    array = json.loads(packed_array_metadata_bytes(IDR0080_FIXTURE_PROFILE))

    image = Image.model_validate({**group, "members": {"s0": array}})

    assert image.ome_zarr_version == "0.5"
    assert image.datasets[0][0].path == "s0"


def test_parser_returns_typed_metadata_and_chunk_requests(
    catalogue: PackedCatalogue,
) -> None:
    plate = _plate(catalogue)
    group = parse_packed_resource(
        plate,
        IDR0080_FIXTURE_PROFILE,
        "A01_s1.zarr/zarr.json",
    )
    assert isinstance(group, PackedMetadataRequest)
    assert group.kind == "group"
    assert group.field.path == "A01_s1.zarr"
    assert group.content == packed_group_metadata_bytes(
        IDR0080_FIXTURE_PROFILE,
        "idr0080-A549",
        "A01_s1",
    )

    array = parse_packed_resource(
        plate,
        IDR0080_FIXTURE_PROFILE,
        "A01_s1.zarr/s0/zarr.json",
    )
    assert isinstance(array, PackedMetadataRequest)
    assert array.kind == "array"
    assert array.content == packed_array_metadata_bytes(IDR0080_FIXTURE_PROFILE)

    chunk = parse_packed_resource(
        plate,
        IDR0080_FIXTURE_PROFILE,
        "A01_s1.zarr/s0/c/4/1/1",
    )
    assert isinstance(chunk, PackedChunkRequest)
    assert chunk.field == group.field
    assert (chunk.channel_index, chunk.channel) == (4, 5)
    assert (chunk.chunk_y, chunk.chunk_x) == (1, 1)
    assert chunk.y_slice == slice(32, 64)
    assert chunk.x_slice == slice(32, 64)
    members = catalogue.field("idr0080-A549", 14610, "A01", 1)
    assert members is not None
    assert chunk.member == members[5]


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "/A01_s1.zarr/zarr.json",
        "A01_s1.zarr/zarr.json/",
        "A01_s1.zarr//zarr.json",
        "A01_s1.zarr\\zarr.json",
        "A01_s1.zarr/./zarr.json",
        "A01_s1.zarr/../zarr.json",
        "A01_s1.zarr",
        "A01_s1.zarr/zarr.json/extra",
        "D01_s1.zarr/zarr.json",
        "A01_s3.zarr/zarr.json",
        "A01_s01.zarr/zarr.json",
        "A01_s1.zarr/s1/zarr.json",
        "A01_s1.zarr/s0/c/-1/0/0",
        "A01_s1.zarr/s0/c/00/0/0",
        "A01_s1.zarr/s0/c/5/0/0",
        "A01_s1.zarr/s0/c/0/2/0",
        "A01_s1.zarr/s0/c/0/0/2",
        "A01_s1.zarr/s0/c/0/0/0/extra",
    ],
)
def test_parser_rejects_every_bad_extra_out_of_range_or_unknown_key(
    catalogue: PackedCatalogue,
    relative: str,
) -> None:
    with pytest.raises(PackedFacadeNotFoundError):
        parse_packed_resource(_plate(catalogue), IDR0080_FIXTURE_PROFILE, relative)


def test_facade_rejects_non_plate_context_and_wrong_profile(
    catalogue: PackedCatalogue,
) -> None:
    experiment = PackedVirtualPath(
        index_path=catalogue.index_path,
        catalogue=catalogue,
        experiment="idr0080-A549",
    )
    with pytest.raises(PackedFacadeNotFoundError, match="require a plate"):
        packed_plate_summary(experiment, IDR0080_FIXTURE_PROFILE)

    wrong_profile = replace(IDR0080_FIXTURE_PROFILE, row_count=149)
    with pytest.raises(PackedFacadeNotFoundError, match="does not match"):
        parse_packed_resource(
            _plate(catalogue),
            wrong_profile,
            "A01_s1.zarr/zarr.json",
        )
