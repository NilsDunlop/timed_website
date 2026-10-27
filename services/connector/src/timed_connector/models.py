"""Typed connector API and session-state models."""

from __future__ import annotations

import posixpath
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlurmProfile(ApiModel):
    account: str
    partition: str
    time: str
    cpus: int = Field(gt=0)
    mem: str


class CollectionConfig(ApiModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str
    description: str | None = None
    path: str
    default: bool = False


class Profile(ApiModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    kind: Literal["slurm", "local"]
    data_roots: list[str] = Field(min_length=1)
    metadata_root: str | None = None
    host: str | None = None
    port: int = Field(default=22, ge=1, le=65535)
    username: str | None = None
    known_hosts: str | None = None
    slurm: SlurmProfile | None = None
    remote_state_dir: str = "~/.timed"
    auto_install_uv: bool = True
    module_loads: list[str] = Field(default_factory=list)
    collections: list[CollectionConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> Profile:
        if self.kind == "slurm":
            missing = [
                field
                for field, value in (
                    ("host", self.host),
                    ("username", self.username),
                    ("slurm", self.slurm),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"Slurm profile is missing: {', '.join(missing)}")

        collection_ids = [collection.id for collection in self.collections]
        duplicate_ids = sorted(
            collection_id
            for collection_id in set(collection_ids)
            if collection_ids.count(collection_id) > 1
        )
        if duplicate_ids:
            raise ValueError(
                "Collection ids must be unique; duplicated: " + ", ".join(duplicate_ids)
            )

        if sum(collection.default for collection in self.collections) > 1:
            raise ValueError("At most one collection may be the default")

        if self.kind == "slurm":
            roots = [PurePosixPath(posixpath.normpath(root)) for root in self.data_roots]
            for collection in self.collections:
                collection_path = PurePosixPath(posixpath.normpath(collection.path))
                if not any(collection_path.is_relative_to(root) for root in roots):
                    raise ValueError(
                        f"Collection {collection.id!r} path must be inside a data root: "
                        f"{collection.path}"
                    )
        else:
            self.collections = [
                collection.model_copy(update={"path": str(_normalized_local_path(collection.path))})
                for collection in self.collections
            ]
        return self


def _normalized_local_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve(strict=False)


class ProfilesDocument(ApiModel):
    profiles: list[Profile]

    @model_validator(mode="after")
    def unique_ids(self) -> ProfilesDocument:
        ids = [profile.id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("Profile ids must be unique")
        return self


class Prompt(ApiModel):
    prompt: str
    echo: bool


class ExistingJob(ApiModel):
    job_id: str
    state: str
    node: str | None = None


SessionState = Literal[
    "disconnected",
    "connecting",
    "host_key_confirm",
    "authenticating",
    "awaiting_input",
    "preparing_server",
    "choose_existing_job",
    "submitting_job",
    "queued",
    "starting",
    "connected",
    "disconnecting",
    "error",
]


class SessionSnapshot(ApiModel):
    state: SessionState = "disconnected"
    profile_id: str | None = None
    prompts: list[Prompt] = Field(default_factory=list)
    host_key_fingerprint: str | None = None
    existing_jobs: list[ExistingJob] = Field(default_factory=list)
    job_id: str | None = None
    slurm_state: str | None = None
    pending_reason: str | None = None
    node: str | None = None
    local_forward_port: int | None = None
    handshake: dict[str, Any] | None = None
    message: str | None = None
    hint: str | None = None
    logs: list[str] = Field(default_factory=list)


class ConnectRequest(ApiModel):
    profile_id: str


class ExistingJobChoice(ApiModel):
    action: Literal["reuse", "cancel"]
    job_id: str


class RespondRequest(ApiModel):
    answers: list[str] | None = None
    accept_host_key: bool | None = None
    existing_job: ExistingJobChoice | None = None

    @model_validator(mode="after")
    def exactly_one_response(self) -> RespondRequest:
        choices = (
            self.answers is not None,
            self.accept_host_key is not None,
            self.existing_job is not None,
        )
        if sum(choices) != 1:
            raise ValueError("Provide exactly one response type")
        return self


class DisconnectRequest(ApiModel):
    cancel_job: bool = True
