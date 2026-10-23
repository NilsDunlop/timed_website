from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from timed_dataserver.packed_profiles import (
    IDR0080_FIXTURE_PROFILE,
    DisplayWindow,
    profile_for_index,
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
        (1, "DNA", "0000FF", True, (27_000, 292, 11_552), (27_000, 234, 12_971)),
        (2, "ER", "00FF00", True, (45_000, 438, 17_554), (45_000, 309, 18_238)),
        (3, "RNA", "00FFFF", False, (26_000, 554, 9_283), (26_000, 375, 10_793)),
        (4, "AGP", "FF0000", True, (14_000, 151, 5_035), (14_000, 108, 5_778)),
        (5, "Mito", "FF00FF", False, (15_000, 324, 4_066), (15_000, 334, 7_120)),
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
    assert profile.cell_line_for("idr0080-ES2") == "ES2"
    assert profile.cell_line_for("idr0080-HCC44") is None
    assert profile.channel(6) is None


def test_profile_lookup_requires_exact_filename_and_row_count() -> None:
    assert profile_for_index("idr0080-raw.index.parquet", 150) is IDR0080_FIXTURE_PROFILE
    assert (
        profile_for_index(Path("fixture") / "idr0080-raw.index.parquet", 150)
        is IDR0080_FIXTURE_PROFILE
    )
    assert profile_for_index("idr0080-raw.index.parquet", 149) is None
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
