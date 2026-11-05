"""Temporary refactor snapshot: today's exact annotation JSONL bytes.

Recorded from unchanged code on 2026-11-05. The last commit of the Gate A
refactor deletes this folder.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import timed_dataserver.qc_annotations as annotation_module
from timed_dataserver.qc_annotations import (
    QcAnnotationDraft,
    QcAnnotationStore,
    QcAnnotationTarget,
    QcRetractionDraft,
)

ANNOTATION_LINE = (
    '{"app_version":"0.5.1","barcode":"SQ00014610","blinded":false,"channel":"DNA",'
    '"confidence":"high","export_id":"fixture-export",'
    '"id":"0f8c5a52-3d1e-4b7a-9c2d-5e6f7a8b9c0d","label":"blurred",'
    '"note":"Fokus unscharf · 2 µm","plate":14610,"record":"annotation",'
    '"registry_hash":"fixture-registry-hash","reviewer":"fixture-a","schema_version":1,'
    '"scope":"field","site":1,"time":"2026-11-05T12:00:00.000001Z","well":"A01"}\n'
).encode()
RETRACTION_LINE = (
    b'{"app_version":"0.5.1","blinded":true,"export_id":"fixture-export",'
    b'"id":"0f8c5a52-3d1e-4b7a-9c2d-5e6f7a8b9c0d","record":"retraction",'
    b'"registry_hash":"fixture-registry-hash","reviewer":"fixture-a","schema_version":1,'
    b'"time":"2026-11-05T12:00:01.000002Z"}\n'
)
WELL_LINE = (
    b'{"app_version":"0.5.1","barcode":"SQ00014610","blinded":true,"channel":"all",'
    b'"confidence":"low","export_id":"fixture-export",'
    b'"id":"1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d","label":"ok","note":"","plate":14610,'
    b'"record":"annotation","registry_hash":"fixture-registry-hash","reviewer":"fixture-b",'
    b'"schema_version":1,"scope":"well","site":null,"time":"2026-11-05T12:00:02.000003Z",'
    b'"well":"A01"}\n'
)
FOLDER_REVISION = "eef15bbe7ec14d3f7fc87f4b1613226b"


def test_annotation_and_retraction_lines_are_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = iter(
        (
            uuid.UUID("0f8c5a52-3d1e-4b7a-9c2d-5e6f7a8b9c0d"),
            uuid.UUID("1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d"),
        )
    )
    times = iter(
        (
            "2026-11-05T12:00:00.000001Z",
            "2026-11-05T12:00:01.000002Z",
            "2026-11-05T12:00:02.000003Z",
        )
    )
    monkeypatch.setattr(annotation_module.uuid, "uuid4", lambda: next(ids))
    monkeypatch.setattr(annotation_module, "_utc_now", lambda: next(times))
    first = QcAnnotationStore(tmp_path, reviewer="fixture-a")
    second = QcAnnotationStore(tmp_path, reviewer="fixture-b")
    labels = {"blurred", "ok"}

    record = first.append_annotation(
        QcAnnotationDraft(
            export_id="fixture-export",
            registry_hash="fixture-registry-hash",
            target=QcAnnotationTarget(
                scope="field",
                plate=14610,
                barcode="SQ00014610",
                well="a01",
                site=1,
                channel="DNA",
            ),
            label="blurred",
            confidence="high",
            note="Fokus unscharf · 2 µm",
            blinded=False,
            app_version="0.5.1",
        ),
        allowed_labels=labels,
    )
    first.append_retraction(
        QcRetractionDraft(
            annotation_id=str(record["id"]),
            export_id="fixture-export",
            registry_hash="fixture-registry-hash",
            blinded=True,
            app_version="0.5.1",
        )
    )
    second.append_annotation(
        QcAnnotationDraft(
            export_id="fixture-export",
            registry_hash="fixture-registry-hash",
            target=QcAnnotationTarget(
                scope="well",
                plate=14610,
                barcode="SQ00014610",
                well="A01",
                site=None,
                channel="all",
            ),
            label="ok",
            confidence="low",
            note="",
            blinded=True,
            app_version="0.5.1",
        ),
        allowed_labels=labels,
    )

    assert first.path.read_bytes() == ANNOTATION_LINE + RETRACTION_LINE
    assert second.path.read_bytes() == WELL_LINE
    state = first.read()
    assert state.revision == FOLDER_REVISION
    assert state.malformed_lines == 0
    assert [current["id"] for current in state.current] == [
        "1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d"
    ]
