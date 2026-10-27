from __future__ import annotations

import io
import tarfile
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest
import tifffile
from timed_dataserver.packed import (
    IndexSignature,
    PackedCatalogue,
    PackedMember,
    PackedPlate,
)
from timed_dataserver.packed_profiles import (
    IDR0080_FIXTURE_PROFILE,
    IDR0080_PROFILE,
    MAX_PACKED_MEMBER_BYTES,
    DisplayWindow,
    PackedImageSpec,
    PackedProfileError,
    generic_profile_for_catalogue,
    probe_packed_image,
    profile_for_catalogue,
    profile_for_index,
)


def _generic_catalogue(
    tmp_path: Path,
    *,
    header_name: str | None = None,
    compression: str | None = None,
    pages: int = 1,
) -> PackedCatalogue:
    relpath = "source/Plate7/Images/A01_s1_ch2.tiff"
    output = io.BytesIO()
    with tifffile.TiffWriter(output) as writer:
        writer.write(
            np.arange(48 * 80, dtype=np.uint8).reshape(48, 80),
            photometric="minisblack",
            compression=compression,
            metadata=None,
        )
        if pages > 1:
            writer.write(
                np.zeros((24, 40), dtype=np.uint8),
                photometric="minisblack",
                compression=compression,
                metadata=None,
            )
    payload = output.getvalue()
    tar_path = tmp_path / "generic.tar"
    member_name = header_name or f"generic/raw/{relpath}"
    with tarfile.open(tar_path, "w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo(member_name)
        info.size = len(payload)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(payload))
    with tarfile.open(tar_path, "r:") as archive:
        info = archive.getmember(member_name)
    member = PackedMember(
        tar=tar_path.resolve(),
        offset=info.offset_data,
        size=info.size,
        relpath=relpath,
    )
    fields = {
        ("generic-exp", 7, "A01", 1): MappingProxyType({2: member, 5: member}),
        ("generic-exp", 7, "Z12", 9): MappingProxyType({2: member, 5: member}),
        ("generic-exp", 8, "AA03", 2): MappingProxyType({2: member, 5: member}),
    }
    index = tmp_path / "generic.index.parquet"
    index.write_bytes(b"generic-index")
    metadata = index.stat()
    return PackedCatalogue(
        index_path=index.resolve(),
        signature=IndexSignature(metadata.st_mtime_ns, metadata.st_size),
        row_count=6,
        plates=MappingProxyType(
            {
                ("generic-exp", 7): PackedPlate(
                    wells=("A01", "Z12"), sites=(1, 9), channels=(2, 5)
                ),
                ("generic-exp", 8): PackedPlate(wells=("AA03",), sites=(2,), channels=(2, 5)),
            }
        ),
        fields=MappingProxyType(fields),
    )


def test_idr0080_fixture_profile_identity_and_geometry_are_pinned() -> None:
    profile = IDR0080_FIXTURE_PROFILE

    assert profile.index_filename == "idr0080-raw.index.parquet"
    assert profile.row_count == 150
    assert profile.dtype == "uint16"
    assert profile.geometry.image_shape == (64, 64)
    assert profile.geometry.chunk_shape == (32, 32)
    assert profile.geometry.sites == (1, 2)
    assert profile.geometry.plate_rows == tuple("ABCDEFGHIJKLMNOP")
    assert profile.geometry.plate_columns == tuple(range(1, 25))
    assert profile.plate_name(14610) == "SQ00014610"
    assert profile.member_prefix == "idr0080/raw/"
    assert profile.metadata_path == "metadata/metadata_idr0080.csv"
    assert profile.pixel_size_um == 0.29898804047838085
    assert profile.auto_contrast is False
    assert (
        profile.codec.cname,
        profile.codec.clevel,
        profile.codec.shuffle,
        profile.codec.blocksize,
        profile.codec.typesize,
    ) == ("zstd", 3, "shuffle", 0, 2)
    assert profile.experiment_cell_lines == (
        ("idr0080-A549", "A549"),
        ("idr0080-ES2", "ES2"),
    )


def test_idr0080_fixture_channels_and_windows_match_appendix_b() -> None:
    profile = IDR0080_FIXTURE_PROFILE
    expected = (
        (1, "DNA", "1300F9", True, (27_000, 292, 11_552), (27_000, 234, 12_971)),
        (2, "ER", "2AFF1F", True, (45_000, 438, 17_554), (45_000, 309, 18_238)),
        (3, "RNA", "2DFFFC", False, (26_000, 554, 9_283), (26_000, 375, 10_793)),
        (4, "AGP", "FF0019", True, (14_000, 151, 5_035), (14_000, 108, 5_778)),
        (5, "Mito", "FA00FD", False, (15_000, 324, 4_066), (15_000, 334, 7_120)),
    )

    assert (
        tuple(
            (
                channel.index,
                channel.label,
                channel.color,
                channel.active,
                (
                    channel.window_for("A549").max,
                    channel.window_for("A549").start,
                    channel.window_for("A549").end,
                ),
                (
                    channel.window_for("ES2").max,
                    channel.window_for("ES2").start,
                    channel.window_for("ES2").end,
                ),
            )
            for channel in profile.channels
        )
        == expected
    )
    assert all(
        channel.window_for(cell_line).min == 0
        for channel in profile.channels
        for cell_line in ("A549", "ES2")
    )
    assert profile.cell_line_for("idr0080-A549") == "A549"
    assert profile.experiment_display_name("idr0080-A549") == "A549"
    assert profile.cell_line_for("idr0080-ES2") == "ES2"
    assert profile.cell_line_for("idr0080-HCC44") is None
    assert profile.channel(6) is None


def test_real_idr0080_profile_identity_geometry_and_codec_are_exactly_pinned() -> None:
    profile = IDR0080_PROFILE

    assert profile.index_filename == "idr0080-raw.index.parquet"
    assert profile.row_count == 155_520
    assert profile.dtype == "uint16"
    assert profile.geometry.image_shape == (2160, 2160)
    assert profile.geometry.chunk_shape == (1080, 1080)
    assert profile.geometry.sites == tuple(range(1, 10))
    assert profile.geometry.plate_rows == tuple("ABCDEFGHIJKLMNOP")
    assert profile.geometry.plate_columns == tuple(range(1, 25))
    assert profile.experiment_cell_lines == (
        ("idr0080-A549", "A549"),
        ("idr0080-ES2", "ES2"),
        ("idr0080-HCC44", "HCC44"),
    )
    assert profile.metadata_path == "metadata/metadata_idr0080.csv"
    assert profile.display_name == "Cell Health"
    assert profile.pixel_size_um == 0.29898804047838085
    assert profile.member_prefix == "idr0080/raw/"
    assert profile.plate_name(14618) == "SQ00014618"
    assert (
        profile.plate_display_name("idr0080-HCC44", 14618)
        == "Cell Health \u00b7 HCC44 \u00b7 SQ00014618"
    )
    assert profile.auto_contrast is False
    assert (
        profile.codec.cname,
        profile.codec.clevel,
        profile.codec.shuffle,
        profile.codec.blocksize,
        profile.codec.typesize,
    ) == ("zstd", 3, "shuffle", 0, 2)


def test_real_idr0080_channels_and_all_appendix_b_windows_are_exactly_pinned() -> None:
    expected = (
        (
            1,
            "DNA",
            "1300F9",
            True,
            (
                ("A549", 0, 27_000, 292, 11_552),
                ("ES2", 0, 27_000, 234, 12_971),
                ("HCC44", 0, 27_000, 254, 13_104),
            ),
        ),
        (
            2,
            "ER",
            "2AFF1F",
            True,
            (
                ("A549", 0, 45_000, 438, 17_554),
                ("ES2", 0, 45_000, 309, 18_238),
                ("HCC44", 0, 45_000, 318, 22_196),
            ),
        ),
        (
            3,
            "RNA",
            "2DFFFC",
            False,
            (
                ("A549", 0, 26_000, 554, 9_283),
                ("ES2", 0, 26_000, 375, 10_793),
                ("HCC44", 0, 26_000, 497, 12_756),
            ),
        ),
        (
            4,
            "AGP",
            "FF0019",
            True,
            (
                ("A549", 0, 14_000, 151, 5_035),
                ("ES2", 0, 14_000, 108, 5_778),
                ("HCC44", 0, 14_000, 117, 6_968),
            ),
        ),
        (
            5,
            "Mito",
            "FA00FD",
            False,
            (
                ("A549", 0, 15_000, 324, 4_066),
                ("ES2", 0, 15_000, 334, 7_120),
                ("HCC44", 0, 15_000, 394, 5_711),
            ),
        ),
    )
    actual = tuple(
        (
            channel.index,
            channel.label,
            channel.color,
            channel.active,
            tuple(
                (window.cell_line, window.min, window.max, window.start, window.end)
                for window in channel.windows
            ),
        )
        for channel in IDR0080_PROFILE.channels
    )

    assert actual == expected
    assert IDR0080_PROFILE.cell_line_for("idr0080-HCC44") == "HCC44"


def test_profile_lookup_requires_exact_filename_and_row_count() -> None:
    assert profile_for_index("idr0080-raw.index.parquet", 150) is IDR0080_FIXTURE_PROFILE
    assert (
        profile_for_index(Path("fixture") / "idr0080-raw.index.parquet", 150)
        is IDR0080_FIXTURE_PROFILE
    )
    assert profile_for_index("idr0080-raw.index.parquet", 149) is None
    assert profile_for_index("idr0080-raw.index.parquet", 155_520) is IDR0080_PROFILE
    assert profile_for_index("IDR0080-raw.index.parquet", 150) is None
    assert profile_for_index("other.index.parquet", 150) is None


def test_profile_value_objects_are_immutable() -> None:
    profile = IDR0080_FIXTURE_PROFILE
    with pytest.raises(FrozenInstanceError):
        profile.row_count = 151  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        profile.geometry.image_shape = (2160, 2160)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        profile.channels[0].active = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        profile.channels[0].windows[0].end = 1  # type: ignore[misc]


def test_display_window_lookup_returns_the_exact_value_object() -> None:
    dna = IDR0080_FIXTURE_PROFILE.channel(1)
    assert dna is not None
    assert dna.window_for("A549") == DisplayWindow(
        cell_line="A549",
        min=0,
        max=27_000,
        start=292,
        end=11_552,
    )
    assert dna.window_for("unknown") is None


def test_generic_profile_probe_and_catalogue_inference_are_exact(tmp_path: Path) -> None:
    catalogue = _generic_catalogue(tmp_path)
    image = probe_packed_image(catalogue)
    profile = profile_for_catalogue(catalogue)

    assert image == PackedImageSpec(
        image_shape=(48, 80),
        dtype="uint8",
        member_prefix="generic/raw/",
    )
    assert profile == generic_profile_for_catalogue(catalogue, image)
    assert profile.index_filename == "generic.index.parquet"
    assert profile.row_count == 6
    assert profile.geometry.image_shape == (48, 80)
    assert profile.geometry.chunk_shape == (48, 80)
    assert profile.geometry.sites == (1, 2, 9)
    assert profile.geometry.plate_rows == tuple(
        chr(ord("A") + index) for index in range(26)
    ) + ("AA",)
    assert profile.geometry.plate_columns == tuple(range(1, 13))
    assert tuple(
        (channel.index, channel.label, channel.color, channel.active, channel.windows)
        for channel in profile.channels
    ) == ((2, "2", None, True, ()), (5, "5", None, True, ()))
    assert profile.experiment_cell_lines == (("generic-exp", "generic-exp"),)
    assert profile.cell_line_for("generic-exp") == "generic-exp"
    assert profile.metadata_path is None
    assert profile.auto_contrast is True
    assert profile.plate_name(7) == "7"
    assert profile.member_prefix == "generic/raw/"
    assert profile.dtype == "uint8"
    assert profile.codec.typesize == 1


def test_generic_profile_rejects_unbounded_or_inconsistent_geometry(tmp_path: Path) -> None:
    catalogue = _generic_catalogue(tmp_path)
    oversized = PackedImageSpec(
        image_shape=(MAX_PACKED_MEMBER_BYTES, 2),
        dtype="uint8",
        member_prefix="generic/raw/",
    )
    with pytest.raises(PackedProfileError, match="decoded plane exceeds"):
        generic_profile_for_catalogue(catalogue, oversized)

    expanded_fields = {
        key: MappingProxyType(
            {channel: next(iter(members.values())) for channel in range(1, 6)}
        )
        for key, members in catalogue.fields.items()
    }
    expanded = replace(catalogue, fields=MappingProxyType(expanded_fields))
    with pytest.raises(PackedProfileError, match="field exceeds"):
        generic_profile_for_catalogue(
            expanded,
            PackedImageSpec(
                image_shape=(MAX_PACKED_MEMBER_BYTES, 1),
                dtype="uint8",
                member_prefix="generic/raw/",
            ),
        )

    fields = dict(catalogue.fields)
    inconsistent_key = ("generic-exp", 7, "Z12", 9)
    fields[inconsistent_key] = MappingProxyType({2: fields[inconsistent_key][2]})
    inconsistent = replace(catalogue, fields=MappingProxyType(fields))
    with pytest.raises(PackedProfileError, match="consistent channel set"):
        generic_profile_for_catalogue(
            inconsistent,
            PackedImageSpec(
                image_shape=(48, 80),
                dtype="uint8",
                member_prefix="generic/raw/",
            ),
        )


def test_generic_profile_rejects_aliases_for_one_well_coordinate(tmp_path: Path) -> None:
    catalogue = _generic_catalogue(tmp_path)
    fields = dict(catalogue.fields)
    fields[("generic-exp", 7, "A1", 2)] = fields[("generic-exp", 7, "A01", 1)]
    aliased = replace(catalogue, fields=MappingProxyType(fields))

    with pytest.raises(PackedProfileError, match="use the same coordinates"):
        generic_profile_for_catalogue(
            aliased,
            PackedImageSpec(
                image_shape=(48, 80),
                dtype="uint8",
                member_prefix="generic/raw/",
            ),
        )


def test_catalogue_resolver_returns_known_pin_without_probing(tmp_path: Path) -> None:
    catalogue = _generic_catalogue(tmp_path)
    known_path = tmp_path / "idr0080-raw.index.parquet"
    known_path.write_bytes(b"known-index")
    known = replace(catalogue, index_path=known_path, row_count=155_520)

    assert profile_for_catalogue(known) is IDR0080_PROFILE


@pytest.mark.parametrize(
    ("header_name", "message"),
    [
        ("generic/raw/not-the-indexed-member.tiff", "does not end with relpath"),
        ("prefixsource/Plate7/Images/A01_s1_ch2.tiff", "not path-delimited"),
    ],
)
def test_generic_probe_rejects_malformed_header_name_or_prefix(
    tmp_path: Path,
    header_name: str,
    message: str,
) -> None:
    catalogue = _generic_catalogue(tmp_path, header_name=header_name)

    with pytest.raises(PackedProfileError, match=message):
        probe_packed_image(catalogue)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"compression": "deflate"}, "must be uncompressed"),
        ({"pages": 2}, "must have one page"),
    ],
)
def test_generic_probe_rejects_non_contract_tiffs(
    tmp_path: Path,
    options: dict[str, object],
    message: str,
) -> None:
    catalogue = _generic_catalogue(tmp_path, **options)  # type: ignore[arg-type]

    with pytest.raises(PackedProfileError, match=message):
        probe_packed_image(catalogue)


def test_generic_probe_checks_indexed_size_against_ustar_header(tmp_path: Path) -> None:
    catalogue = _generic_catalogue(tmp_path)
    field_key = min(catalogue.fields)
    members = dict(catalogue.fields[field_key])
    channel = min(members)
    members[channel] = replace(members[channel], size=members[channel].size - 1)
    fields = dict(catalogue.fields)
    fields[field_key] = MappingProxyType(members)
    malformed = replace(catalogue, fields=MappingProxyType(fields))

    with pytest.raises(PackedProfileError, match="member size mismatch"):
        probe_packed_image(malformed)
