"""Typed request and response models for the data-server API."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base model with a closed schema so API drift is visible."""

    model_config = ConfigDict(extra="forbid")


class Capabilities(ApiModel):
    gpu: bool = False


class HandshakeResponse(ApiModel):
    api_version: str
    server_version: str
    environment: Literal["local", "hpc"]
    hostname: str
    data_roots: list[str]
    python_version: str
    capabilities: Capabilities


class OmeZarrInfo(ApiModel):
    kind: Literal["plate", "well", "image"]
    version: str
    zarr_format: Literal[2, 3]


class FilesystemEntry(ApiModel):
    name: str
    path: str
    kind: Literal["dir", "file"]
    ome_zarr: OmeZarrInfo | None = None


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
