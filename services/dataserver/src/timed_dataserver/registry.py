"""Persistent registry of user-linked datasets."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .models import DatasetRecord, OmeZarrInfo


class RegistryError(RuntimeError):
    """Raised when persisted registry state is malformed."""


def atomic_json_write(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    """Write JSON through a same-directory temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        os.chmod(path, mode)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


class DatasetRegistry:
    """A small process-local view of an atomically persisted JSON registry."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir.expanduser() / "datasets.json"
        self._lock = threading.RLock()
        self._records = self._load()

    def _load(self) -> list[DatasetRecord]:
        if not self.path.exists():
            return []
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("version") != 1:
                raise RegistryError(f"Unsupported registry format: {self.path}")
            return TypeAdapter(list[DatasetRecord]).validate_python(document.get("datasets", []))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise RegistryError(f"Could not read registry {self.path}: {exc}") from exc

    def _save(self) -> None:
        atomic_json_write(
            self.path,
            {
                "version": 1,
                "datasets": [record.model_dump(mode="json") for record in self._records],
            },
        )

    def list(self) -> list[DatasetRecord]:
        with self._lock:
            return list(self._records)

    def get(self, dataset_id: str) -> DatasetRecord | None:
        with self._lock:
            return next((record for record in self._records if record.id == dataset_id), None)

    def link(self, path: Path, name: str, ome_zarr: OmeZarrInfo) -> DatasetRecord:
        resolved = str(path.resolve(strict=True))
        return self._link_record(resolved, name, ome_zarr)

    def link_key(self, key: str, name: str, ome_zarr: OmeZarrInfo) -> DatasetRecord:
        """Link a caller-validated virtual key without resolving it as a real path."""
        return self._link_record(os.path.normpath(key), name, ome_zarr)

    def _link_record(
        self,
        path: str,
        name: str,
        ome_zarr: OmeZarrInfo,
    ) -> DatasetRecord:
        canonical = os.path.normcase(os.path.normpath(path))
        with self._lock:
            existing = next(
                (
                    record
                    for record in self._records
                    if os.path.normcase(os.path.normpath(record.path)) == canonical
                ),
                None,
            )
            if existing is not None:
                return existing
            record = DatasetRecord(
                id=uuid.uuid4().hex,
                path=path,
                name=name,
                linked_at=datetime.now(UTC).isoformat(),
                ome_zarr=ome_zarr,
            )
            self._records.append(record)
            self._save()
            return record

    def unlink(self, dataset_id: str) -> bool:
        with self._lock:
            retained = [record for record in self._records if record.id != dataset_id]
            if len(retained) == len(self._records):
                return False
            self._records = retained
            self._save()
            return True
