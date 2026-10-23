"""Pinned code profiles for known packed microscopy indexes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DisplayWindow:
    """OMERO display bounds for one channel and cell line."""

    cell_line: str
    min: int
    max: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class PackedChannel:
    """One indexed image channel and its display defaults."""

    index: int
    label: str
    color: str
    active: bool
    windows: tuple[DisplayWindow, ...]

    def window_for(self, cell_line: str) -> DisplayWindow | None:
        return next((window for window in self.windows if window.cell_line == cell_line), None)


@dataclass(frozen=True, slots=True)
class PackedGeometry:
    """Image, chunk, site, and plate geometry for a packed dataset."""

    image_shape: tuple[int, int]
    chunk_shape: tuple[int, int]
    sites: tuple[int, ...]
    plate_rows: tuple[str, ...]
    plate_columns: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PackedDatasetProfile:
    """Immutable description of a packed index whose identity is pinned."""

    index_filename: str
    row_count: int
    geometry: PackedGeometry
    channels: tuple[PackedChannel, ...]
    experiment_cell_lines: tuple[tuple[str, str], ...]
    metadata_path: str
    dtype: str = "uint16"
    plate_prefix: str = "SQ000"
    member_prefix: str = "idr0080/raw/"

    def cell_line_for(self, experiment: str) -> str | None:
        return next(
            (
                cell_line
                for candidate, cell_line in self.experiment_cell_lines
                if candidate == experiment
            ),
            None,
        )

    def channel(self, index: int) -> PackedChannel | None:
        return next((channel for channel in self.channels if channel.index == index), None)

    def plate_name(self, plate: int) -> str:
        return f"{self.plate_prefix}{plate}"


def _window(cell_line: str, maximum: int, start: int, end: int) -> DisplayWindow:
    return DisplayWindow(cell_line=cell_line, min=0, max=maximum, start=start, end=end)


IDR0080_FIXTURE_PROFILE = PackedDatasetProfile(
    index_filename="idr0080-raw.index.parquet",
    row_count=150,
    geometry=PackedGeometry(
        image_shape=(64, 64),
        chunk_shape=(32, 32),
        sites=(1, 2),
        plate_rows=tuple("ABCDEFGHIJKLMNOP"),
        plate_columns=tuple(range(1, 25)),
    ),
    channels=(
        PackedChannel(
            index=1,
            label="DNA",
            color="0000FF",
            active=True,
            windows=(
                _window("A549", 27_000, 292, 11_552),
                _window("ES2", 27_000, 234, 12_971),
            ),
        ),
        PackedChannel(
            index=2,
            label="ER",
            color="00FF00",
            active=True,
            windows=(
                _window("A549", 45_000, 438, 17_554),
                _window("ES2", 45_000, 309, 18_238),
            ),
        ),
        PackedChannel(
            index=3,
            label="RNA",
            color="00FFFF",
            active=False,
            windows=(
                _window("A549", 26_000, 554, 9_283),
                _window("ES2", 26_000, 375, 10_793),
            ),
        ),
        PackedChannel(
            index=4,
            label="AGP",
            color="FF0000",
            active=True,
            windows=(
                _window("A549", 14_000, 151, 5_035),
                _window("ES2", 14_000, 108, 5_778),
            ),
        ),
        PackedChannel(
            index=5,
            label="Mito",
            color="FF00FF",
            active=False,
            windows=(
                _window("A549", 15_000, 324, 4_066),
                _window("ES2", 15_000, 334, 7_120),
            ),
        ),
    ),
    experiment_cell_lines=(
        ("idr0080-A549", "A549"),
        ("idr0080-ES2", "ES2"),
    ),
    metadata_path="metadata/metadata_idr0080.csv",
)


_KNOWN_PROFILES = (IDR0080_FIXTURE_PROFILE,)


def profile_for_index(
    filename: str | Path,
    row_count: int,
) -> PackedDatasetProfile | None:
    """Return a known profile only when both filename and row count match."""
    name = Path(filename).name
    return next(
        (
            profile
            for profile in _KNOWN_PROFILES
            if profile.index_filename == name and profile.row_count == row_count
        ),
        None,
    )
