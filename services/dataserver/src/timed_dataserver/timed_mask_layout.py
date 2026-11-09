"""Where each TIMED mask layout keeps one field's arrays inside its mask folder.

An export folder mirrors the plate: ``<plate>.zarr/<well>_s<site>.zarr/<array>``.
A production folder holds one array per run and field:
``<plate>/sites/<well>_s<site>/stage1/<run>/labels/<plate>/<well>_s<site>.zarr``.
Labels name an array by its path inside the mask folder, never absolutely.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .timed_mask_config import MaskLayout, TimedMaskMapping, mirrored_plate, production_plate
from .timed_masks import RunIdentity


@dataclass(frozen=True, slots=True)
class TimedMaskFolder:
    """One plate's masks in one layout: the mask folder and the plate's place in it."""

    layout: MaskLayout
    root: Path
    plate: Path
    plate_name: str

    def array_dir(self, well: str, site: int, array: str) -> Path:
        """The directory of one array (export) or of one run's array (production)."""
        name = f"{well}_s{site}"
        if self.layout == "export":
            return self.plate / f"{name}.zarr" / array
        return (
            self.plate / "sites" / name / "stage1" / array / "labels" / self.plate_name
        ) / f"{name}.zarr"

    def label(self, well: str, site: int, array: str) -> str:
        """The array's path relative to the mask folder, as errors name it."""
        return self.array_dir(well, site, array).relative_to(self.root).as_posix()

    def run(self, well: str, site: int, array: str) -> RunIdentity | None:
        """What a production array must store about itself; nothing for an export."""
        if self.layout == "export":
            return None
        return RunIdentity(plate=self.plate_name, well=well, site=site, run_id=array)


def plate_folders(
    mappings: Sequence[TimedMaskMapping],
    plate: Path,
) -> dict[MaskLayout, TimedMaskFolder]:
    """Each configured layout's folder for one resolved TIMED plate."""
    folders: dict[MaskLayout, TimedMaskFolder] = {}
    mirrored = mirrored_plate(mappings, plate)
    if mirrored is not None:
        mapping, masks_plate = mirrored
        folders["export"] = TimedMaskFolder(
            layout="export",
            root=mapping.masks_root,
            plate=masks_plate,
            plate_name=plate.name,
        )
    production = production_plate(mappings, plate)
    if production is not None:
        mapping, runs_plate, plate_name = production
        folders["production"] = TimedMaskFolder(
            layout="production",
            root=mapping.masks_root,
            plate=runs_plate,
            plate_name=plate_name,
        )
    return folders
