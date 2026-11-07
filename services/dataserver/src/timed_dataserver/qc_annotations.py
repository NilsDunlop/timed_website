"""Append-only storage for QC review annotations.

The store owns exactly one writer file, derived from the server process's OS
user.  Requests supply annotation content, never filesystem names.  Reads fold
all regular JSONL files in the configured folder into the current annotation
state while retaining malformed-line counts for operator status.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import stat
import threading
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .tar_io import open_flags

ANNOTATION_CONFIDENCES = frozenset(("low", "medium", "high"))
MAX_NOTE_LENGTH = 2_000
_MAX_SHORT_TEXT = 256
_REVIEWER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_WELL_PATTERN = re.compile(r"^[A-Z]+[0-9]+$")
_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: dict[Path, threading.RLock] = {}

type AnnotationScope = Literal["field", "well"]
type JsonRecord = dict[str, object]


class QcAnnotationError(RuntimeError):
    """Base class for annotation-store failures safe to expose generically."""


class QcAnnotationUnavailableError(QcAnnotationError):
    """Raised when the configured annotation store cannot be read or written."""


class QcAnnotationValidationError(QcAnnotationError):
    """Raised when an annotation command violates the storage contract."""


@dataclass(frozen=True, slots=True)
class QcAnnotationTarget:
    """Validated export identity for one field or well annotation."""

    scope: AnnotationScope
    plate: int
    barcode: str
    well: str
    site: int | None
    channel: str


@dataclass(frozen=True, slots=True)
class QcAnnotationDraft:
    """Server-validated content for a new annotation record."""

    export_id: str
    registry_hash: str
    target: QcAnnotationTarget
    label: str
    confidence: str
    note: str
    blinded: bool
    app_version: str


@dataclass(frozen=True, slots=True)
class QcRetractionDraft:
    """Server-validated content for retracting one annotation by UUID."""

    annotation_id: str
    export_id: str
    registry_hash: str
    blinded: bool
    app_version: str


@dataclass(frozen=True, slots=True)
class QcAnnotationState:
    """Current annotations and audit health for one configured folder."""

    current: tuple[Mapping[str, object], ...]
    malformed_lines: int
    revision: str


@dataclass(frozen=True, slots=True)
class _ParsedState:
    public: QcAnnotationState
    annotations: Mapping[tuple[str, str], Mapping[str, object]]
    retracted: frozenset[tuple[str, str]]


def sanitize_reviewer(value: str) -> str:
    """Reduce an OS account name to the safe reviewer/file-name alphabet."""
    collapsed = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return (collapsed or "reviewer")[:128]


def _process_reviewer() -> str:
    """The server process's account name, which names its writer file.

    POSIX reads the account of the real user ID. Windows uses USERNAME, which
    every logon session sets, so a shell's USER or LOGNAME cannot change the
    file name. Otherwise Python's getpass rule applies, and "reviewer" when no
    account name is available at all.
    """
    if os.name == "posix":
        try:
            import pwd

            return pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError, OSError):
            pass
    elif os.name == "nt":
        username = os.environ.get("USERNAME", "").strip()
        if username:
            return username
    try:
        return getpass.getuser()
    except (ImportError, KeyError, OSError):
        # Python 3.12 falls back to importing pwd, which Windows lacks.
        return "reviewer"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _file_lock(path: Path) -> threading.RLock:
    """Return the process-wide lock for one canonical writer file."""
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(path, threading.RLock())


def _short_text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > _MAX_SHORT_TEXT:
        raise QcAnnotationValidationError(f"Annotation {field} is invalid")
    if not allow_empty and not value:
        raise QcAnnotationValidationError(f"Annotation {field} is invalid")
    return value


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise QcAnnotationValidationError(f"Annotation {field} is invalid")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise QcAnnotationValidationError(f"Annotation {field} is invalid")
    return value


def _uuid_text(value: object) -> str:
    text = _short_text(value, "id")
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise QcAnnotationValidationError("Annotation id is invalid") from exc
    return str(parsed)


def _time_text(value: object) -> str:
    text = _short_text(value, "time")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QcAnnotationValidationError("Annotation time is invalid") from exc
    if parsed.tzinfo is None:
        raise QcAnnotationValidationError("Annotation time is invalid")
    return text


def _normalized_target(target: QcAnnotationTarget) -> QcAnnotationTarget:
    if target.scope not in ("field", "well"):
        raise QcAnnotationValidationError("Annotation scope is invalid")
    plate = _positive_integer(target.plate, "plate")
    barcode = _short_text(target.barcode, "barcode")
    well = _short_text(target.well, "well").upper()
    if _WELL_PATTERN.fullmatch(well) is None:
        raise QcAnnotationValidationError("Annotation well is invalid")
    channel = _short_text(target.channel, "channel")
    if target.scope == "field":
        site = _positive_integer(target.site, "site")
    elif target.site is None:
        site = None
    else:
        raise QcAnnotationValidationError("Well annotations cannot include a site")
    return QcAnnotationTarget(
        scope=target.scope,
        plate=plate,
        barcode=barcode,
        well=well,
        site=site,
        channel=channel,
    )


def _normalized_annotation_draft(
    draft: QcAnnotationDraft,
    *,
    allowed_labels: Collection[str] | None = None,
) -> QcAnnotationDraft:
    export_id = _short_text(draft.export_id, "export id")
    registry_hash = _short_text(draft.registry_hash, "registry hash")
    app_version = _short_text(draft.app_version, "application version")
    if (
        not isinstance(draft.label, str)
        or len(draft.label) > 100
        or _LABEL_PATTERN.fullmatch(draft.label) is None
        or (allowed_labels is not None and draft.label not in allowed_labels)
    ):
        raise QcAnnotationValidationError("Annotation label is invalid")
    if draft.confidence not in ANNOTATION_CONFIDENCES:
        raise QcAnnotationValidationError("Annotation confidence is invalid")
    if not isinstance(draft.note, str) or len(draft.note) > MAX_NOTE_LENGTH:
        raise QcAnnotationValidationError("Annotation note is invalid")
    return QcAnnotationDraft(
        export_id=export_id,
        registry_hash=registry_hash,
        target=_normalized_target(draft.target),
        label=draft.label,
        confidence=draft.confidence,
        note=draft.note,
        blinded=_boolean(draft.blinded, "mode"),
        app_version=app_version,
    )


def _normalized_retraction_draft(draft: QcRetractionDraft) -> QcRetractionDraft:
    return QcRetractionDraft(
        annotation_id=_uuid_text(draft.annotation_id),
        export_id=_short_text(draft.export_id, "export id"),
        registry_hash=_short_text(draft.registry_hash, "registry hash"),
        blinded=_boolean(draft.blinded, "mode"),
        app_version=_short_text(draft.app_version, "application version"),
    )


def _normalized_stored_record(value: object) -> JsonRecord:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise QcAnnotationValidationError("Annotation record is invalid")
    record_type = value.get("record")
    reviewer = _short_text(value.get("reviewer"), "reviewer")
    if _REVIEWER_PATTERN.fullmatch(reviewer) is None:
        raise QcAnnotationValidationError("Annotation reviewer is invalid")
    common: JsonRecord = {
        "schema_version": 1,
        "record": record_type,
        "id": _uuid_text(value.get("id")),
        "time": _time_text(value.get("time")),
        "reviewer": reviewer,
        "export_id": _short_text(value.get("export_id"), "export id"),
        "registry_hash": _short_text(value.get("registry_hash"), "registry hash"),
        "blinded": _boolean(value.get("blinded"), "mode"),
        "app_version": _short_text(value.get("app_version"), "application version"),
    }
    if record_type == "retraction":
        return common
    if record_type != "annotation":
        raise QcAnnotationValidationError("Annotation record is invalid")
    target = _normalized_target(
        QcAnnotationTarget(
            scope=value.get("scope"),  # type: ignore[arg-type]
            plate=value.get("plate"),  # type: ignore[arg-type]
            barcode=value.get("barcode"),  # type: ignore[arg-type]
            well=value.get("well"),  # type: ignore[arg-type]
            site=value.get("site"),  # type: ignore[arg-type]
            channel=value.get("channel"),  # type: ignore[arg-type]
        )
    )
    label = value.get("label")
    confidence = value.get("confidence")
    note = value.get("note")
    if not isinstance(note, str):
        raise QcAnnotationValidationError("Annotation note is invalid")
    normalized = _normalized_annotation_draft(
        QcAnnotationDraft(
            export_id=str(common["export_id"]),
            registry_hash=str(common["registry_hash"]),
            target=target,
            label=label if isinstance(label, str) else "",
            confidence=confidence if isinstance(confidence, str) else "",
            note=note,
            blinded=bool(common["blinded"]),
            app_version=str(common["app_version"]),
        )
    )
    return {
        **common,
        "scope": normalized.target.scope,
        "plate": normalized.target.plate,
        "barcode": normalized.target.barcode,
        "well": normalized.target.well,
        "site": normalized.target.site,
        "channel": normalized.target.channel,
        "label": normalized.label,
        "confidence": normalized.confidence,
        "note": normalized.note,
    }


def _annotation_key(record: Mapping[str, object]) -> tuple[object, ...]:
    return (
        record["reviewer"],
        record["scope"],
        record["plate"],
        record["barcode"],
        record["well"],
        record["site"],
        record["channel"],
    )


class QcAnnotationStore:
    """Append and fold QC annotation JSONL records for one configured folder."""

    def __init__(self, folder: Path, *, reviewer: str | None = None) -> None:
        self.folder = folder.expanduser().resolve(strict=False)
        self.reviewer = sanitize_reviewer(
            _process_reviewer() if reviewer is None else reviewer
        )
        self.path = self.folder / f"annotations_{self.reviewer}.jsonl"
        self._lock = _file_lock(self.path)

    def append_annotation(
        self,
        draft: QcAnnotationDraft,
        *,
        allowed_labels: Collection[str] | None = None,
    ) -> Mapping[str, object]:
        """Append one annotation and return the immutable stored record."""
        normalized = _normalized_annotation_draft(draft, allowed_labels=allowed_labels)
        target = normalized.target
        record: JsonRecord = {
            "schema_version": 1,
            "record": "annotation",
            "id": str(uuid.uuid4()),
            "time": _utc_now(),
            "reviewer": self.reviewer,
            "export_id": normalized.export_id,
            "registry_hash": normalized.registry_hash,
            "scope": target.scope,
            "plate": target.plate,
            "barcode": target.barcode,
            "well": target.well,
            "site": target.site,
            "channel": target.channel,
            "label": normalized.label,
            "confidence": normalized.confidence,
            "note": normalized.note,
            "blinded": normalized.blinded,
            "app_version": normalized.app_version,
        }
        with self._lock:
            self._append_unlocked(record)
        return MappingProxyType(record)

    def append_retraction(self, draft: QcRetractionDraft) -> Mapping[str, object]:
        """Append a retraction for this reviewer's existing annotation."""
        normalized = _normalized_retraction_draft(draft)
        identity = (self.reviewer, normalized.annotation_id)
        with self._lock:
            parsed = self._read_unlocked()
            if identity not in parsed.annotations or identity in parsed.retracted:
                raise QcAnnotationValidationError(
                    "Retraction target is unavailable for this reviewer"
                )
            record: JsonRecord = {
                "schema_version": 1,
                "record": "retraction",
                "id": normalized.annotation_id,
                "time": _utc_now(),
                "reviewer": self.reviewer,
                "export_id": normalized.export_id,
                "registry_hash": normalized.registry_hash,
                "blinded": normalized.blinded,
                "app_version": normalized.app_version,
            }
            self._append_unlocked(record)
        return MappingProxyType(record)

    def read(self) -> QcAnnotationState:
        """Read current annotations, skipping and counting malformed lines."""
        with self._lock:
            return self._read_unlocked().public

    def _require_folder(self) -> None:
        try:
            if not self.folder.is_dir() or self.folder.is_symlink():
                raise QcAnnotationUnavailableError("Annotation folder is unavailable")
        except OSError as exc:
            raise QcAnnotationUnavailableError("Annotation folder is unavailable") from exc

    def _open_regular(self, path: Path, flags: int, mode: int | None = None) -> int:
        try:
            if path.is_symlink():
                raise QcAnnotationUnavailableError("Annotation file is unavailable")
            if path.parent.resolve(strict=True) != self.folder:
                raise QcAnnotationUnavailableError("Annotation file is unavailable")
            fd = (
                os.open(path, open_flags(flags))
                if mode is None
                else os.open(path, open_flags(flags), mode)
            )
        except QcAnnotationUnavailableError:
            raise
        except OSError as exc:
            raise QcAnnotationUnavailableError("Annotation file is unavailable") from exc
        try:
            descriptor = os.fstat(fd)
            path_stat = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(descriptor.st_mode) or not stat.S_ISREG(path_stat.st_mode):
                raise QcAnnotationUnavailableError("Annotation file is unavailable")
            if not os.path.samestat(descriptor, path_stat):
                raise QcAnnotationUnavailableError("Annotation file is unavailable")
            if flags & os.O_WRONLY and descriptor.st_nlink != 1:
                raise QcAnnotationUnavailableError("Annotation file is unavailable")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _append_unlocked(self, record: Mapping[str, object]) -> None:
        self._require_folder()
        payload = (
            json.dumps(
                dict(record),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        fd = self._open_regular(
            self.path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        try:
            written = os.write(fd, payload)
            if written != len(payload):
                raise QcAnnotationUnavailableError("Annotation write was incomplete")
            os.fsync(fd)
        except QcAnnotationUnavailableError:
            raise
        except OSError as exc:
            raise QcAnnotationUnavailableError("Annotation write failed") from exc
        finally:
            os.close(fd)

    def _read_unlocked(self) -> _ParsedState:
        self._require_folder()
        digest = hashlib.blake2s(digest_size=16)
        malformed = 0
        order = 0
        annotations: dict[tuple[str, str], Mapping[str, object]] = {}
        annotation_order: dict[tuple[str, str], int] = {}
        retracted: set[tuple[str, str]] = set()

        try:
            candidates = sorted(self.folder.glob("*.jsonl"), key=lambda path: path.name)
        except OSError as exc:
            raise QcAnnotationUnavailableError("Annotation folder is unavailable") from exc

        for path in candidates:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            fd = self._open_regular(path, os.O_RDONLY)
            try:
                with os.fdopen(fd, "rb", closefd=False) as handle:
                    for raw_line in handle:
                        digest.update(raw_line)
                        order += 1
                        try:
                            record = _normalized_stored_record(
                                json.loads(raw_line.decode("utf-8"))
                            )
                            identity = (str(record["reviewer"]), str(record["id"]))
                            if record["record"] == "annotation":
                                if identity in annotations:
                                    raise QcAnnotationValidationError(
                                        "Annotation id is duplicated"
                                    )
                                annotations[identity] = MappingProxyType(record)
                                annotation_order[identity] = order
                            elif identity not in annotations or identity in retracted:
                                raise QcAnnotationValidationError(
                                    "Retraction target is invalid"
                                )
                            else:
                                retracted.add(identity)
                        except (
                            UnicodeDecodeError,
                            json.JSONDecodeError,
                            QcAnnotationValidationError,
                        ):
                            malformed += 1
            except OSError as exc:
                raise QcAnnotationUnavailableError("Annotation file is unavailable") from exc
            finally:
                os.close(fd)

        latest: dict[tuple[object, ...], tuple[int, Mapping[str, object]]] = {}
        for identity, record in annotations.items():
            if identity in retracted:
                continue
            position = annotation_order[identity]
            key = _annotation_key(record)
            previous = latest.get(key)
            if previous is None or position > previous[0]:
                latest[key] = (position, record)
        current = tuple(record for _position, record in sorted(latest.values()))
        public = QcAnnotationState(
            current=current,
            malformed_lines=malformed,
            revision=digest.hexdigest(),
        )
        return _ParsedState(
            public=public,
            annotations=MappingProxyType(annotations),
            retracted=frozenset(retracted),
        )
