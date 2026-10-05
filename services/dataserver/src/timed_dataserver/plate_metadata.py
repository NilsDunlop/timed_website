"""Read optional TIMED plate and acquisition metadata from a configured root."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .models import (
    AcquisitionChannelSummary,
    PlateAcquisitionSummary,
    PlateExperimentSummary,
    PlateMetadataResponse,
    WellMetadataSummary,
)

DATASET_NAME = re.compile(
    r"^(?P<barcode>[A-Za-z]+\d+)_(?P<plate>P(?P<series>\d+)\.(?P<replicate>\d+))$"
)


class PlateMetadataError(RuntimeError):
    """Raised when configured metadata is malformed or ambiguous."""


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: object) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise PlateMetadataError(f"Expected a number, got {text!r}") from exc


def _int(value: object) -> int | None:
    number = _float(value)
    if number is None:
        return None
    if not number.is_integer():
        raise PlateMetadataError(f"Expected an integer, got {number}")
    return int(number)


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _interval_minutes(grid: dict[str, Any]) -> float | None:
    hours = _float(grid.get("delta_t_hours"))
    if hours is not None:
        return hours * 60
    value = _mapping(grid.get("delta_t"))
    if not value:
        return None
    return (
        (_float(value.get("h")) or 0) * 60
        + (_float(value.get("m")) or 0)
        + (_float(value.get("s")) or 0) / 60
    )


def _acquisition(path: Path) -> PlateAcquisitionSummary:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlateMetadataError(f"Could not read acquisition metadata {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlateMetadataError(f"Acquisition metadata is not a JSON object: {path}")

    grid = _mapping(value.get("grid"))
    plate_type = _mapping(value.get("wellplate_type"))
    channels = []
    raw_channels = value.get("channels")
    if isinstance(raw_channels, list):
        for raw_channel in raw_channels:
            channel = _mapping(raw_channel)
            if not channel or channel.get("enabled") is False:
                continue
            name = _text(channel.get("name"))
            if name is None:
                continue
            channels.append(
                AcquisitionChannelSummary(
                    name=name,
                )
            )

    num_x = _int(grid.get("num_x"))
    num_y = _int(grid.get("num_y"))
    return PlateAcquisitionSummary(
        project_name=_text(value.get("project_name")),
        configuration_timestamp=_text(value.get("timestamp")),
        autofocus_enabled=(
            value.get("autofocus_enabled")
            if isinstance(value.get("autofocus_enabled"), bool)
            else None
        ),
        plate_manufacturer=_text(plate_type.get("Manufacturer")),
        plate_model=_text(plate_type.get("Model_name")),
        plate_bottom_offset_mm=_float(plate_type.get("Offset_bottom_mm")),
        sites_per_well=num_x * num_y if num_x is not None and num_y is not None else None,
        site_spacing_x_mm=_float(grid.get("delta_x_mm")),
        site_spacing_y_mm=_float(grid.get("delta_y_mm")),
        timepoint_count=_int(grid.get("num_t")),
        interval_minutes=_interval_minutes(grid),
        channels=channels,
    )


def _well_metadata(row: dict[str, str]) -> WellMetadataSummary:
    well_type = _text(row.get("well_type"))
    compound = _text(row.get("cmpdname"))
    is_control = (well_type or "").casefold() != "compound" or (compound or "").casefold() in {
        "dmso",
        "vehicle",
    }
    return WellMetadataSummary(
        well=_text(row.get("well")) or "",
        well_type=well_type,
        compound=compound,
        dose_um=_float(row.get("target_compound_concentration_uM")),
        is_control=is_control,
        moa=_text(row.get("moa")),
        target=_text(row.get("target")),
        pathway=_text(row.get("pathway")),
        mechanism_of_action=_text(row.get("mechanism_of_action")),
    )


class PlateMetadataCatalog:
    """Resolve a linked plate name to its bounded CSV and acquisition JSON."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        if not self.root.is_dir():
            raise PlateMetadataError(f"Metadata root does not exist: {self.root}")
        self._cache: dict[str, PlateMetadataResponse] = {}

    def _csv_path(self, stem: str, plate_id: str | None) -> Path | None:
        csv_root = self.root / "csv"
        if not csv_root.is_dir():
            return None
        exact = csv_root / f"{stem}.csv"
        if exact.is_file():
            return exact
        if plate_id is None:
            return None
        matches = sorted(
            path
            for path in csv_root.iterdir()
            if path.is_file() and path.suffix.casefold() == ".csv" and f"_{plate_id}_" in path.name
        )
        if len(matches) > 1:
            raise PlateMetadataError(
                f"Multiple CSV metadata files match plate {plate_id}: "
                + ", ".join(path.name for path in matches)
            )
        return matches[0] if matches else None

    def for_dataset(self, dataset_name: str) -> PlateMetadataResponse:
        if dataset_name in self._cache:
            return self._cache[dataset_name]
        stem = dataset_name.removesuffix(".zarr")
        identity = DATASET_NAME.fullmatch(stem)
        plate_id = identity.group("plate") if identity else None
        barcode = identity.group("barcode") if identity else None
        csv_path = self._csv_path(stem, plate_id)
        acquisition_path = self.root / "acquisition" / f"{stem}.json"
        if csv_path is None and not acquisition_path.is_file():
            response = PlateMetadataResponse(available=False)
            self._cache[dataset_name] = response
            return response
        if csv_path is None:
            raise PlateMetadataError(f"No well metadata CSV found for {stem}")

        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, UnicodeError, csv.Error) as exc:
            raise PlateMetadataError(f"Could not read well metadata {csv_path}: {exc}") from exc
        if not rows:
            raise PlateMetadataError(f"Well metadata CSV is empty: {csv_path}")

        first = rows[0]
        csv_barcode = _text(first.get("plate_barcode"))
        if barcode is not None and csv_barcode != barcode:
            raise PlateMetadataError(
                f"Metadata barcode {csv_barcode!r} does not match dataset {barcode!r}"
            )
        wells = [_well_metadata(row) for row in rows]
        if any(not well.well for well in wells):
            raise PlateMetadataError(f"Well metadata contains an empty well: {csv_path}")
        well_names = [well.well for well in wells]
        if len(well_names) != len(set(well_names)):
            raise PlateMetadataError(f"Well metadata contains duplicate wells: {csv_path}")

        explicit_plate_id = _text(first.get("plate_id"))
        resolved_plate_id = explicit_plate_id or plate_id or stem
        plate_match = re.fullmatch(
            r"P(?P<series>\d+)\.(?P<replicate>\d+)",
            resolved_plate_id,
        )
        acquisition = _acquisition(acquisition_path) if acquisition_path.is_file() else None
        response = PlateMetadataResponse(
            available=True,
            plate=PlateExperimentSummary(
                plate_id=resolved_plate_id,
                plate_series=(
                    _text(first.get("plate_series"))
                    or (f"P{plate_match.group('series')}" if plate_match else None)
                ),
                plate_replicate=(
                    _int(first.get("plate_replicate"))
                    or (int(plate_match.group("replicate")) if plate_match else None)
                ),
                plate_barcode=csv_barcode,
                project=_text(first.get("project")),
                experiment_date=_text(first.get("experiment_date")),
                cell_line=_text(first.get("cell_line")),
                cell_modification=_text(first.get("cell_modification")),
                passage=_text(first.get("passage")),
                seeding_density_cells_per_well=_int(first.get("seeding_density_cells_per_well")),
                dye_program=_text(first.get("dye_program")),
                acquisition=acquisition,
            ),
            wells=wells,
        )
        if len(self._cache) >= 32:
            self._cache.pop(next(iter(self._cache)))
        self._cache[dataset_name] = response
        return response
