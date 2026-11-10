"""Typed connector API and session-state models."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

USERNAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$"
"""A login name: no path separators, spaces or leading dash."""
USERNAME_PROBLEM = "Enter only the login name: letters, digits, '.', '_' or '-'"
USERNAME_PLACEHOLDER = "{username}"
"""Replaced with the connecting user's name in the per-user paths of Slurm profiles."""
_PLACEHOLDER_COLLECTION_FIELDS = ("qc_export", "qc_annotations", "masks_root", "segmentation_root")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlurmProfile(ApiModel):
    account: str
    partition: str | None = None
    time: str
    cpus: int = Field(gt=0)
    mem: str | None = None


class CollectionConfig(ApiModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str
    description: str | None = None
    path: str
    default: bool = False
    qc_export: str | None = Field(default=None, min_length=1, exclude=True)
    qc_annotations: str | None = Field(default=None, min_length=1, exclude=True)
    masks_root: str | None = Field(default=None, min_length=1, exclude=True)
    """A read-only folder mirroring this TIMED collection's plates and fields."""
    segmentation_root: str | None = Field(default=None, min_length=1, exclude=True)
    """A read-only folder of production segmentation runs for this TIMED collection."""

    @computed_field
    @property
    def qc_available(self) -> bool:
        """Report QC availability without exposing configured server paths."""
        return self.qc_export is not None

    @model_validator(mode="after")
    def qc_paths_are_paired(self) -> CollectionConfig:
        if (self.qc_export is None) != (self.qc_annotations is None):
            raise ValueError("Collection qc_export and qc_annotations must be configured together")
        if self.qc_export is not None and any(
            "=" in value for value in (self.path, self.qc_export, self.qc_annotations or "")
        ):
            raise ValueError("QC mapping paths must not contain '='")
        if any(
            folder is not None and "=" in self.path + folder
            for folder in (self.masks_root, self.segmentation_root)
        ):
            raise ValueError("Mask mapping paths must not contain '='")
        return self


class Profile(ApiModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    kind: Literal["slurm", "local"]
    data_roots: list[str] = Field(min_length=1)
    metadata_root: str | None = None
    host: str | None = None
    port: int = Field(default=22, ge=1, le=65535)
    username: str | None = None
    """The login name, if fixed; otherwise the Connect page asks for it."""
    known_hosts: str | None = None
    slurm: SlurmProfile | None = None
    remote_state_dir: str = "~/.timed"
    auto_install_uv: bool = True
    module_loads: list[str] = Field(default_factory=list)
    collections: list[CollectionConfig] = Field(default_factory=list)
    ssh_key: str | None = Field(default=None, min_length=1, exclude=True)
    """A private key file for SSH; otherwise the user's default keys and agent."""

    @model_validator(mode="after")
    def validate_kind_fields(self) -> Profile:
        if self.kind == "slurm":
            missing = [
                field
                for field, value in (
                    ("host", self.host),
                    ("slurm", self.slurm),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"Slurm profile is missing: {', '.join(missing)}")
        if self.username is not None and re.fullmatch(USERNAME_PATTERN, self.username) is None:
            raise ValueError(f"Profile {self.id!r} username is not a valid login name")
        self._check_placeholders()

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
                for field_name, value in (
                    ("qc_export", collection.qc_export),
                    ("qc_annotations", collection.qc_annotations),
                    ("masks_root", collection.masks_root),
                    ("segmentation_root", collection.segmentation_root),
                ):
                    if value is None:
                        continue
                    if not PurePosixPath(posixpath.normpath(value)).is_absolute():
                        raise ValueError(
                            f"Collection {collection.id!r} {field_name} must be an "
                            f"absolute POSIX path: {value}"
                        )
        else:
            self.collections = [
                collection.model_copy(
                    update={
                        "path": str(_normalized_local_path(collection.path)),
                        "qc_export": (
                            str(_normalized_local_path(collection.qc_export))
                            if collection.qc_export is not None
                            else None
                        ),
                        "qc_annotations": (
                            str(_normalized_local_path(collection.qc_annotations))
                            if collection.qc_annotations is not None
                            else None
                        ),
                        "masks_root": (
                            str(_normalized_local_path(collection.masks_root))
                            if collection.masks_root is not None
                            else None
                        ),
                        "segmentation_root": (
                            str(_normalized_local_path(collection.segmentation_root))
                            if collection.segmentation_root is not None
                            else None
                        ),
                    }
                )
                for collection in self.collections
            ]
            for collection in self.collections:
                if collection.qc_export is not None and any(
                    "=" in value
                    for value in (
                        collection.path,
                        collection.qc_export,
                        collection.qc_annotations or "",
                    )
                ):
                    raise ValueError(
                        "QC mapping paths must not contain '=' after local path normalization"
                    )
                if any(
                    folder is not None and "=" in collection.path + folder
                    for folder in (collection.masks_root, collection.segmentation_root)
                ):
                    raise ValueError(
                        "Mask mapping paths must not contain '=' after local path normalization"
                    )
        return self

    def _check_placeholders(self) -> None:
        """``{username}`` may appear only in server-side, per-user paths of Slurm profiles."""
        allowed = [self.metadata_root] + [
            getattr(collection, field)
            for collection in self.collections
            for field in _PLACEHOLDER_COLLECTION_FIELDS
        ]
        if self.kind == "local" and any(
            value is not None and USERNAME_PLACEHOLDER in value for value in allowed
        ):
            raise ValueError("{username} placeholders are only for Slurm profiles")
        elsewhere = [
            name
            for name, value in (
                ("data_roots", " ".join(self.data_roots)),
                ("host", self.host),
                ("username", self.username),
                ("known_hosts", self.known_hosts),
                ("remote_state_dir", self.remote_state_dir),
                ("ssh_key", self.ssh_key),
                *((f"collection {c.id!r} path", c.path) for c in self.collections),
            )
            if value is not None and USERNAME_PLACEHOLDER in value
        ]
        if elsewhere:
            raise ValueError(
                "{username} is allowed only in metadata_root and in a collection's "
                "qc_export, qc_annotations, masks_root and segmentation_root; found in "
                + ", ".join(elsewhere)
            )

    def for_user(self, username: str) -> Profile:
        """This profile for one login: the username set and its placeholders filled in."""
        if self.kind != "slurm":
            return self
        if re.fullmatch(USERNAME_PATTERN, username) is None:
            raise ValueError(USERNAME_PROBLEM)

        def fill(value: str | None) -> str | None:
            return None if value is None else value.replace(USERNAME_PLACEHOLDER, username)

        collections = [
            collection.model_copy(
                update={
                    field: fill(getattr(collection, field))
                    for field in _PLACEHOLDER_COLLECTION_FIELDS
                }
            )
            for collection in self.collections
        ]
        return self.model_copy(
            update={
                "username": username,
                "metadata_root": fill(self.metadata_root),
                "collections": collections,
            }
        )


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
    username: str | None = Field(default=None, max_length=64, pattern=USERNAME_PATTERN)
    """The login name for a Slurm profile; local profiles ignore it."""


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
