"""Typed request and response models for the data-server API."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base model with a closed schema so API drift is visible."""

    model_config = ConfigDict(extra="forbid")


def _omit_excluded_default(schema: dict[str, object]) -> None:
    schema.pop("default", None)


class Capabilities(ApiModel):
    gpu: bool = False
    plate_metadata: bool = False
    packed_datasets: bool = False
    qc_review: bool = False
    qc_annotations_writable: bool = False


class HandshakeResponse(ApiModel):
    api_version: str
    server_version: str
    environment: Literal["local", "hpc"]
    hostname: str
    data_roots: list[str]
    python_version: str
    capabilities: Capabilities


class QcStatusResponse(ApiModel):
    """Operational state for one configured QC export."""

    available: bool
    core_ready: bool
    export_readable: bool
    annotations_writable: bool
    export_id: str | None = None
    registry_hash: str | None = None
    row_count: int | None = None
    hash_check: Literal["unavailable", "ok", "error"]
    missing_contract: list[str] = Field(default_factory=list)
    unknown_statuses: list[str] = Field(default_factory=list)
    error: str | None = None


class OmeZarrInfo(ApiModel):
    kind: Literal["plate", "well", "image"]
    version: str
    zarr_format: Literal[2, 3]


class FilesystemEntry(ApiModel):
    name: str
    path: str
    kind: Literal["dir", "file"]
    ome_zarr: OmeZarrInfo | None = None
    detail: str | None = Field(default=None, exclude_if=lambda value: value is None)


class FilesystemListResponse(ApiModel):
    path: str | None
    entries: list[FilesystemEntry]


class DatasetLinkRequest(ApiModel):
    path: str
    name: str | None = Field(default=None, min_length=1, max_length=200)


class DatasetRecord(ApiModel):
    id: str
    path: str
    name: str
    linked_at: str
    ome_zarr: OmeZarrInfo


class DatasetListResponse(ApiModel):
    datasets: list[DatasetRecord]


class AxisSummary(ApiModel):
    name: str
    type: str | None = None
    unit: str | None = None


class WindowSummary(ApiModel):
    min: float | None = None
    max: float | None = None
    start: float | None = None
    end: float | None = None


class ChannelSummary(ApiModel):
    name: str
    color: str | None = None
    window: WindowSummary | None = None
    active: bool | None = Field(default=None, exclude_if=lambda value: value is None)


class ResolutionLevel(ApiModel):
    path: str
    shape: list[int]
    chunks: list[int]
    shard_shape: list[int] | None = None
    dtype: str
    coordinate_transformations: list[dict[str, object]] = Field(default_factory=list)


class ImageSummary(ApiModel):
    kind: Literal["image"] = "image"
    name: str
    path: str
    axes: list[AxisSummary]
    levels: list[ResolutionLevel]
    channels: list[ChannelSummary]
    grayscale_available: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
        json_schema_extra=_omit_excluded_default,
    )
    ome_zarr_version: str
    zarr_format: Literal[2, 3]


class AcquisitionSummary(ApiModel):
    id: int | str | None = None
    name: str | None = None


class WellSummary(ApiModel):
    row: str
    column: str
    path: str


class PlateSummary(ApiModel):
    kind: Literal["plate"] = "plate"
    name: str
    path: str
    rows: list[str]
    columns: list[str]
    acquisitions: list[AcquisitionSummary]
    wells: list[WellSummary]
    first_image: ImageSummary | None = None
    ome_zarr_version: str
    zarr_format: Literal[2, 3]


DatasetSummary = Annotated[ImageSummary | PlateSummary, Field(discriminator="kind")]


class FieldSummary(ApiModel):
    path: str
    acquisition: int | str | None = None


class WellFieldsResponse(ApiModel):
    well_path: str
    fields: list[FieldSummary]


class AcquisitionChannelSummary(ApiModel):
    name: str


class PlateAcquisitionSummary(ApiModel):
    project_name: str | None = None
    configuration_timestamp: str | None = None
    autofocus_enabled: bool | None = None
    plate_manufacturer: str | None = None
    plate_model: str | None = None
    plate_bottom_offset_mm: float | None = None
    sites_per_well: int | None = None
    site_spacing_x_mm: float | None = None
    site_spacing_y_mm: float | None = None
    timepoint_count: int | None = None
    interval_minutes: float | None = None
    channels: list[AcquisitionChannelSummary] = Field(default_factory=list)


class PlateExperimentSummary(ApiModel):
    plate_id: str
    plate_series: str | None = None
    plate_replicate: int | None = None
    plate_barcode: str | None = None
    project: str | None = None
    experiment_date: str | None = None
    cell_line: str | None = None
    cell_modification: str | None = None
    passage: str | None = None
    seeding_density_cells_per_well: int | None = None
    dye_program: str | None = None
    acquisition: PlateAcquisitionSummary | None = None


class MetadataDetailSummary(ApiModel):
    label: str
    value: str


class PlateMetadataLabels(ApiModel):
    compound: str
    moa: str
    control: str


class WellMetadataSummary(ApiModel):
    well: str
    well_type: str | None = None
    compound: str | None = None
    dose_um: float | None = None
    is_control: bool = False
    moa: str | None = None
    target: str | None = None
    pathway: str | None = None
    mechanism_of_action: str | None = None
    details: list[MetadataDetailSummary] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class PlateMetadataResponse(ApiModel):
    available: bool
    plate: PlateExperimentSummary | None = None
    wells: list[WellMetadataSummary] = Field(default_factory=list)
    labels: PlateMetadataLabels | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    category_colors: dict[str, str] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


MatchMode = Literal["well", "gene", "control"]


class DatasetMatchSummary(ApiModel):
    path: str
    plate_label: str
    cell_line: str
    well: str
    guide: str
    is_control: bool
    well_type: str | None


class DatasetMatchesResponse(ApiModel):
    matches: list[DatasetMatchSummary]
