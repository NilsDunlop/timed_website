"""Load and map optional well metadata for packed image indexes."""

from __future__ import annotations

import csv
import os
import stat
import threading
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import TextIO

from .models import (
    AcquisitionChannelSummary,
    DatasetMatchesResponse,
    DatasetMatchSummary,
    MatchMode,
    MetadataDetailSummary,
    PlateAcquisitionSummary,
    PlateExperimentSummary,
    PlateMetadataLabels,
    PlateMetadataResponse,
    WellMetadataSummary,
)
from .packed import PackedCatalogue, PackedVirtualPath
from .packed_profiles import PackedDatasetProfile
from .tar_io import open_flags, same_file_revision

MAX_DATASET_MATCHES = 200

IDR0080_METADATA_COLUMNS = frozenset(
    {
        "well_id",
        "experiment_name",
        "plate",
        "address",
        "gene",
        "treatment",
        "SMILES",
        "concentration",
        "perturbation_type",
        "cell_type",
        "well_type_label",
        "idr_plate_barcode",
        "idr_cell_line",
        "idr_guide",
        "idr_gene_original",
        "idr_gene_identifier",
        "idr_profile_id",
        "idr_control_category",
        "idr_reagent_id",
    }
)

IDR0080_CATEGORY_COLORS: Mapping[str, str] = {
    "Query guides": "#3b82a0",
    "Cutting controls": "#b86b35",
    "Non-cutting controls": "#9367a8",
}

_PROJECT = "Cell Health"
_LABELS = PlateMetadataLabels(
    compound="Gene",
    moa="Well type",
    control="EMPTY control",
)
_PlateKey = tuple[str, int]
_RowsByPlate = dict[_PlateKey, tuple[dict[str, str], ...]]


class PackedMetadataError(RuntimeError):
    """Raised when a configured packed metadata CSV violates its contract."""


class PackedMetadataNotFoundError(PackedMetadataError):
    """Raised when metadata-backed matching is unavailable or has no source well."""


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required(row: Mapping[str, str], column: str, *, context: str) -> str:
    value = _text(row.get(column))
    if value is None:
        raise PackedMetadataError(f"Packed metadata {context} has an empty {column!r}")
    return value


def _plate_number(value: str, *, context: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise PackedMetadataError(
            f"Packed metadata {context} has an invalid plate number: {value!r}"
        ) from exc


def _uniform(rows: tuple[dict[str, str], ...], column: str, *, context: str) -> str:
    values = {_required(row, column, context=context) for row in rows}
    if len(values) != 1:
        raise PackedMetadataError(
            f"Packed metadata {context} has inconsistent {column!r}: {sorted(values)}"
        )
    return next(iter(values))


def _details(row: Mapping[str, str]) -> list[MetadataDetailSummary]:
    result = []
    for label, column in (
        ("Guide", "idr_guide"),
        ("Ensembl ID", "idr_gene_identifier"),
        ("Control category", "idr_control_category"),
    ):
        value = _text(row.get(column))
        if value is not None:
            result.append(MetadataDetailSummary(label=label, value=value))
    return result


def _well(row: Mapping[str, str], *, context: str) -> WellMetadataSummary:
    gene = _required(row, "gene", context=context)
    treatment = _required(row, "treatment", context=context)
    return WellMetadataSummary(
        well=_required(row, "address", context=context),
        well_type=_text(row.get("perturbation_type")),
        compound="EMPTY control" if gene == "EMPTY_control" else gene,
        is_control=treatment == "EMPTY_control",
        moa=_text(row.get("well_type_label")),
        details=_details(row),
    )


def _plate_identity(
    profile: PackedDatasetProfile,
    experiment: str,
    plate: int,
    rows: tuple[dict[str, str], ...],
) -> tuple[str, str]:
    context = f"for {experiment}/{plate}"
    barcode = _uniform(rows, "idr_plate_barcode", context=context)
    expected_barcode = profile.plate_name(plate)
    if barcode != expected_barcode:
        raise PackedMetadataError(
            f"Packed metadata {context} has barcode {barcode!r}, expected {expected_barcode!r}"
        )
    return expected_barcode, _uniform(rows, "cell_type", context=context)


def _plate_response(
    profile: PackedDatasetProfile,
    experiment: str,
    plate: int,
    rows: tuple[dict[str, str], ...],
) -> PlateMetadataResponse:
    context = f"for {experiment}/{plate}"
    expected_barcode, cell_line = _plate_identity(profile, experiment, plate, rows)
    return PlateMetadataResponse(
        available=True,
        plate=PlateExperimentSummary(
            plate_id=expected_barcode,
            plate_barcode=expected_barcode,
            project=_PROJECT,
            cell_line=cell_line,
            acquisition=PlateAcquisitionSummary(
                sites_per_well=len(profile.geometry.sites),
                timepoint_count=1,
                channels=[
                    AcquisitionChannelSummary(name=channel.label)
                    for channel in profile.channels
                ],
            ),
        ),
        wells=[_well(row, context=context) for row in rows],
        labels=_LABELS.model_copy(deep=True),
        category_colors=dict(IDR0080_CATEGORY_COLORS),
    )


class PackedMetadataCatalog:
    """Lazy, thread-safe metadata catalogue for one packed image index."""

    def __init__(self, index_path: Path, profile: PackedDatasetProfile) -> None:
        try:
            self.index_path = index_path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise PackedMetadataError(f"Packed index does not exist: {index_path}") from exc
        self.profile = profile
        self._lock = threading.Lock()
        self._loaded = False
        self._missing = False
        self._groups: _RowsByPlate = {}
        self._responses: dict[_PlateKey, PlateMetadataResponse] = {}

    def _metadata_candidate(self) -> Path | None:
        relative = self.profile.metadata_path
        if relative is None:
            return None
        root = self.index_path.parent
        logical = PurePosixPath(relative)
        if (
            not relative
            or "\x00" in relative
            or logical.is_absolute()
            or "\\" in relative
            or ".." in logical.parts
        ):
            raise PackedMetadataError(
                f"Packed metadata path escapes its index directory: {relative}"
            )
        return root.joinpath(*(part for part in logical.parts if part not in ("", ".")))

    def _open_metadata(self) -> tuple[Path, TextIO] | None:
        candidate = self._metadata_candidate()
        if candidate is None:
            return None
        root = self.index_path.parent
        fd: int | None = None
        try:
            fd = os.open(candidate, open_flags())
            descriptor_stat = os.fstat(fd)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            path_stat = os.stat(resolved, follow_symlinks=False)
            if not stat.S_ISREG(descriptor_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
                raise PackedMetadataError(
                    f"Packed metadata is not a regular file: {candidate}"
                )
            if not same_file_revision(descriptor_stat, path_stat):
                raise PackedMetadataError(
                    f"Packed metadata changed while opening: {candidate}"
                )
            handle = os.fdopen(fd, "r", encoding="utf-8-sig", newline="")
            fd = None
            return resolved, handle
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise PackedMetadataError(
                f"Packed metadata path escapes its index directory: {self.profile.metadata_path}"
            ) from exc
        except PackedMetadataError:
            raise
        except OSError as exc:
            try:
                candidate.resolve(strict=False).relative_to(root)
            except (OSError, ValueError) as path_exc:
                raise PackedMetadataError(
                    "Packed metadata path escapes its index directory: "
                    f"{self.profile.metadata_path}"
                ) from path_exc
            raise PackedMetadataError(f"Could not open packed metadata {candidate}: {exc}") from exc
        finally:
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)

    def _read_groups(self) -> _RowsByPlate | None:
        opened = self._open_metadata()
        if opened is None:
            return None
        path, handle = opened
        try:
            with handle:
                reader = csv.DictReader(handle)
                fieldnames = set(reader.fieldnames or ())
                missing_columns = sorted(IDR0080_METADATA_COLUMNS - fieldnames)
                if missing_columns:
                    raise PackedMetadataError(
                        f"Packed metadata CSV is missing columns: {', '.join(missing_columns)}"
                    )
                mutable: dict[_PlateKey, list[dict[str, str]]] = {}
                seen_wells: dict[_PlateKey, set[str]] = {}
                for line, row in enumerate(reader, start=2):
                    context = f"row {line} of {path}"
                    experiment = _required(row, "experiment_name", context=context)
                    plate = _plate_number(
                        _required(row, "plate", context=context),
                        context=context,
                    )
                    well = _required(row, "address", context=context)
                    key = (experiment, plate)
                    if well in seen_wells.setdefault(key, set()):
                        raise PackedMetadataError(
                            f"Packed metadata has duplicate well {well!r} for {experiment}/{plate}"
                        )
                    seen_wells[key].add(well)
                    mutable.setdefault(key, []).append(dict(row))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, csv.Error) as exc:
            raise PackedMetadataError(f"Could not read packed metadata {path}: {exc}") from exc
        if not mutable:
            raise PackedMetadataError(f"Packed metadata CSV is empty: {path}")
        return {key: tuple(rows) for key, rows in mutable.items()}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        groups = self._read_groups()
        self._missing = groups is None
        self._groups = groups or {}
        self._loaded = True

    def for_plate(self, experiment: str, plate: int) -> PlateMetadataResponse:
        """Return one cached mapped plate response, loading the CSV at most once."""
        key = (experiment, plate)
        with self._lock:
            cached = self._responses.get(key)
            if cached is not None:
                return cached
            self._ensure_loaded()
            if self._missing:
                response = PlateMetadataResponse(available=False)
            else:
                rows = self._groups.get(key)
                if rows is None:
                    raise PackedMetadataError(
                        f"Packed metadata has no rows for {experiment}/{plate}"
                    )
                response = _plate_response(self.profile, experiment, plate, rows)
            self._responses[key] = response
            return response

    def matches(
        self,
        catalogue: PackedCatalogue,
        experiment: str,
        plate: int,
        well: str,
        by: MatchMode,
    ) -> DatasetMatchesResponse:
        """Return deterministic metadata matches, capped for bounded API responses."""
        if by not in {"well", "gene", "control"}:
            raise PackedMetadataError(f"Unknown packed metadata match mode: {by}")
        if catalogue.index_path != self.index_path:
            raise PackedMetadataError("Packed metadata catalogue does not match its image index")

        source_key = (experiment, plate)
        plate_info = catalogue.plate(experiment, plate)
        if plate_info is None or well not in plate_info.wells:
            raise PackedMetadataNotFoundError(f"Unknown packed well: {well}")

        with self._lock:
            self._ensure_loaded()
            if self._missing:
                raise PackedMetadataNotFoundError("Packed metadata is unavailable")
            source_rows = self._groups.get(source_key)
            if source_rows is None:
                raise PackedMetadataError(
                    f"Packed metadata has no rows for {experiment}/{plate}"
                )
            source = next(
                (row for row in source_rows if _text(row.get("address")) == well),
                None,
            )
            if source is None:
                raise PackedMetadataNotFoundError(f"Packed metadata has no well {well!r}")
            source_gene = _required(
                source,
                "gene",
                context=f"for {experiment}/{plate}/{well}",
            )

            ordered: list[tuple[tuple[str, int, str], DatasetMatchSummary]] = []
            for (candidate_experiment, candidate_plate), rows in self._groups.items():
                candidate_info = catalogue.plate(candidate_experiment, candidate_plate)
                if candidate_info is None:
                    raise PackedMetadataError(
                        "Packed metadata has a plate absent from the image index: "
                        f"{candidate_experiment}/{candidate_plate}"
                    )
                plate_label, cell_line = _plate_identity(
                    self.profile,
                    candidate_experiment,
                    candidate_plate,
                    rows,
                )
                for row in rows:
                    context = f"for {candidate_experiment}/{candidate_plate}"
                    candidate_well = _required(row, "address", context=context)
                    if candidate_well not in candidate_info.wells:
                        raise PackedMetadataError(
                            "Packed metadata has a well absent from the image index: "
                            f"{candidate_experiment}/{candidate_plate}/{candidate_well}"
                        )
                    candidate_gene = _required(row, "gene", context=context)
                    treatment = _required(row, "treatment", context=context)
                    same_source = (
                        candidate_experiment == experiment
                        and candidate_plate == plate
                        and candidate_well == well
                    )
                    include = (
                        by == "well"
                        and candidate_well == well
                        and (candidate_experiment, candidate_plate) != source_key
                    ) or (
                        by == "gene"
                        and candidate_gene == source_gene
                        and not same_source
                    ) or (
                        by == "control"
                        and (candidate_experiment, candidate_plate) == source_key
                        and treatment == "EMPTY_control"
                    )
                    if not include:
                        continue
                    virtual_plate = PackedVirtualPath(
                        index_path=self.index_path,
                        catalogue=catalogue,
                        experiment=candidate_experiment,
                        plate=candidate_plate,
                    )
                    match = DatasetMatchSummary(
                        path=virtual_plate.registry_key,
                        plate_label=plate_label,
                        cell_line=cell_line,
                        well=candidate_well,
                        guide=_required(row, "idr_guide", context=context),
                        is_control=treatment == "EMPTY_control",
                        well_type=_text(row.get("well_type_label")),
                    )
                    ordered.append(
                        ((cell_line.casefold(), candidate_plate, candidate_well), match)
                    )

            ordered.sort(key=lambda item: item[0])
            return DatasetMatchesResponse(
                matches=[match for _key, match in ordered[:MAX_DATASET_MATCHES]]
            )


class PackedMetadataCache:
    """Own one lazy metadata catalogue per packed index and code profile."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._catalogues: dict[
            tuple[Path, int, int, int, int, PackedDatasetProfile],
            PackedMetadataCatalog,
        ] = {}

    def _catalogue(
        self,
        index_path: Path,
        profile: PackedDatasetProfile,
    ) -> PackedMetadataCatalog:
        try:
            resolved = index_path.expanduser().resolve(strict=True)
            index_stat = resolved.stat()
        except OSError as exc:
            raise PackedMetadataError(f"Packed index does not exist: {index_path}") from exc
        key = (
            resolved,
            index_stat.st_dev,
            index_stat.st_ino,
            index_stat.st_mtime_ns,
            index_stat.st_size,
            profile,
        )
        with self._lock:
            catalogue = self._catalogues.get(key)
            if catalogue is None:
                catalogue = PackedMetadataCatalog(resolved, profile)
                if len(self._catalogues) >= 256:
                    self._catalogues.pop(next(iter(self._catalogues)))
                self._catalogues[key] = catalogue
        return catalogue

    def for_plate(
        self,
        index_path: Path,
        profile: PackedDatasetProfile,
        experiment: str,
        plate: int,
    ) -> PlateMetadataResponse:
        """Map one plate without touching metadata storage for profile-less datasets."""
        relative = profile.metadata_path
        if relative is None:
            return PlateMetadataResponse(available=False)
        return self._catalogue(index_path, profile).for_plate(experiment, plate)

    def matches(
        self,
        catalogue: PackedCatalogue,
        profile: PackedDatasetProfile,
        experiment: str,
        plate: int,
        well: str,
        by: MatchMode,
    ) -> DatasetMatchesResponse:
        """Query the cached metadata rows for one packed source well."""
        if profile.metadata_path is None:
            raise PackedMetadataNotFoundError("Packed metadata is unavailable")
        return self._catalogue(catalogue.index_path, profile).matches(
            catalogue,
            experiment,
            plate,
            well,
            by,
        )

    def clear(self) -> None:
        """Forget loaded metadata catalogues (primarily for isolated tests)."""
        with self._lock:
            self._catalogues.clear()
