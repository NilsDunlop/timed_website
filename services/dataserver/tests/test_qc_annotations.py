from __future__ import annotations

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import timed_dataserver.qc_annotations as annotation_module
from timed_dataserver.qc_annotations import (
    MAX_NOTE_LENGTH,
    QcAnnotationDraft,
    QcAnnotationStore,
    QcAnnotationTarget,
    QcAnnotationUnavailableError,
    QcAnnotationValidationError,
    QcRetractionDraft,
    sanitize_reviewer,
)


def _target(
    *,
    scope: str = "field",
    site: int | None = 1,
    channel: str = "DNA",
) -> QcAnnotationTarget:
    return QcAnnotationTarget(
        scope=scope,  # type: ignore[arg-type]
        plate=14610,
        barcode="SQ00014610",
        well="a01",
        site=site,
        channel=channel,
    )


def _draft(
    *,
    target: QcAnnotationTarget | None = None,
    label: str = "ok",
    confidence: str = "high",
    note: str = "looks good",
    blinded: bool = False,
) -> QcAnnotationDraft:
    return QcAnnotationDraft(
        export_id="fixture-export",
        registry_hash="fixture-registry-hash",
        target=target or _target(),
        label=label,
        confidence=confidence,
        note=note,
        blinded=blinded,
        app_version="0.5.0.dev-test",
    )


def _retraction(annotation_id: str, *, blinded: bool = False) -> QcRetractionDraft:
    return QcRetractionDraft(
        annotation_id=annotation_id,
        export_id="fixture-export",
        registry_hash="fixture-registry-hash",
        blinded=blinded,
        app_version="0.5.0.dev-test",
    )


def test_reviewer_is_sanitized_and_only_selects_a_fixed_file(tmp_path: Path) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="reviewer one/../../outside")

    record = store.append_annotation(_draft(blinded=True))

    assert store.reviewer == "reviewer_one_outside"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", store.reviewer)
    assert store.path == folder / "annotations_reviewer_one_outside.jsonl"
    assert store.path.resolve().parent == folder.resolve()
    assert record["reviewer"] == store.reviewer
    assert record["blinded"] is True
    assert record["well"] == "A01"
    assert record["record"] == "annotation"
    assert record["schema_version"] == 1
    assert uuid.UUID(str(record["id"]))
    assert str(record["time"]).endswith("Z")
    assert sanitize_reviewer("///") == "reviewer"


def test_append_uses_one_write_and_fsync_without_changing_earlier_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="alice")
    store.append_annotation(_draft(note="first"))
    before = store.path.read_bytes()
    calls: dict[str, list[object]] = {"open": [], "write": [], "fsync": []}
    real_open = annotation_module.os.open
    real_write = annotation_module.os.write
    real_fsync = annotation_module.os.fsync

    def tracked_open(path: object, flags: int, mode: int = 0o777) -> int:
        calls["open"].append((path, flags, mode))
        return real_open(path, flags, mode)

    def tracked_write(fd: int, payload: bytes) -> int:
        calls["write"].append(payload)
        return real_write(fd, payload)

    def tracked_fsync(fd: int) -> None:
        calls["fsync"].append(fd)
        real_fsync(fd)

    monkeypatch.setattr(annotation_module.os, "open", tracked_open)
    monkeypatch.setattr(annotation_module.os, "write", tracked_write)
    monkeypatch.setattr(annotation_module.os, "fsync", tracked_fsync)

    store.append_annotation(_draft(note="second"))

    after = store.path.read_bytes()
    assert after.startswith(before)
    assert len(calls["open"]) == 1
    _path, flags, _mode = calls["open"][0]  # type: ignore[misc]
    assert flags & os.O_WRONLY
    assert flags & os.O_APPEND
    assert flags & os.O_CREAT
    assert len(calls["write"]) == 1
    assert len(calls["fsync"]) == 1
    assert calls["write"][0].endswith(b"\n")  # type: ignore[union-attr]
    assert len(after.splitlines()) == 2


@pytest.mark.parametrize(
    "draft",
    [
        _draft(label="not-a-label"),
        _draft(confidence="certain"),
        _draft(note="x" * (MAX_NOTE_LENGTH + 1)),
        _draft(target=_target(scope="field", site=None)),
        _draft(target=_target(scope="well", site=1)),
        _draft(target=_target(channel="")),
    ],
)
def test_invalid_annotation_writes_nothing(
    tmp_path: Path,
    draft: QcAnnotationDraft,
) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="alice")

    with pytest.raises(QcAnnotationValidationError):
        store.append_annotation(draft)

    assert not store.path.exists()


def test_validation_error_does_not_echo_note_contents(tmp_path: Path) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="alice")
    private_note = "private-note-marker" * 200

    with pytest.raises(QcAnnotationValidationError) as raised:
        store.append_annotation(_draft(note=private_note))

    assert private_note not in str(raised.value)


def test_latest_annotation_and_retraction_fall_back_per_reviewer_and_target(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    alice = QcAnnotationStore(folder, reviewer="alice")
    bob = QcAnnotationStore(folder, reviewer="bob")
    first = alice.append_annotation(_draft(label="ok", note="first"))
    second = alice.append_annotation(_draft(label="blurred", note="second"))
    other_channel = alice.append_annotation(
        _draft(target=_target(channel="ER"), label="noisy", note="channel")
    )
    bob_record = bob.append_annotation(_draft(label="unsure", note="other reviewer"))

    initial = alice.read()
    assert [record["id"] for record in initial.current] == [
        second["id"],
        other_channel["id"],
        bob_record["id"],
    ]

    alice.append_retraction(_retraction(str(second["id"])))
    after = alice.read()
    assert [record["id"] for record in after.current] == [
        first["id"],
        other_channel["id"],
        bob_record["id"],
    ]

    before_repeat = alice.path.read_bytes()
    with pytest.raises(QcAnnotationValidationError):
        alice.append_retraction(_retraction(str(second["id"])))
    assert alice.path.read_bytes() == before_repeat

    with pytest.raises(QcAnnotationValidationError):
        alice.append_retraction(_retraction(str(bob_record["id"])))
    assert alice.path.read_bytes() == before_repeat


def test_reads_every_regular_jsonl_and_counts_malformed_lines(tmp_path: Path) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="alice")
    first = dict(store.append_annotation(_draft()))
    second = {
        **first,
        "id": str(uuid.uuid4()),
        "reviewer": "bob",
        "channel": "ER",
        "note": "second file",
    }
    extra = folder / "imported.jsonl"
    extra.write_text(
        "\n".join(
            (
                json.dumps(second, sort_keys=True),
                "not-json",
                json.dumps({"schema_version": 2}),
                json.dumps({**second, "id": str(uuid.uuid4()), "note": 7}),
                json.dumps(
                    {
                        "schema_version": 1,
                        "record": "retraction",
                        "id": str(uuid.uuid4()),
                        "time": first["time"],
                        "reviewer": "bob",
                        "export_id": "fixture-export",
                        "registry_hash": "fixture-registry-hash",
                        "blinded": False,
                        "app_version": "0.5.0.dev-test",
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    state = store.read()

    assert state.malformed_lines == 4
    assert {record["id"] for record in state.current} == {first["id"], second["id"]}
    assert re.fullmatch(r"[0-9a-f]{32}", state.revision)


def test_revision_is_deterministic_and_changes_with_a_new_record(tmp_path: Path) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="alice")
    empty = store.read()
    repeated = store.read()
    assert repeated.revision == empty.revision

    store.append_annotation(_draft())
    changed = store.read()

    assert changed.revision != empty.revision
    assert store.read().revision == changed.revision


def test_concurrent_appends_leave_complete_json_lines(tmp_path: Path) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="alice")

    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(
            executor.map(
                lambda index: store.append_annotation(_draft(note=f"record {index}")),
                range(24),
            )
        )

    lines = store.path.read_text(encoding="utf-8").splitlines()
    decoded = [json.loads(line) for line in lines]
    state = store.read()
    assert len(lines) == 24
    assert len({record["id"] for record in decoded}) == 24
    assert {record["id"] for record in records} == {
        record["id"] for record in decoded
    }
    assert state.malformed_lines == 0
    assert len(state.current) == 1


def test_separate_stores_share_the_writer_file_lock(tmp_path: Path) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    first = QcAnnotationStore(folder, reviewer="alice")
    second = QcAnnotationStore(folder, reviewer="alice")

    assert first.path == second.path
    assert first._lock is second._lock

    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(
            executor.map(
                lambda pair: pair[0].append_annotation(_draft(note=pair[1])),
                (
                    (first if index % 2 == 0 else second, f"record {index}")
                    for index in range(24)
                ),
            )
        )

    decoded = [
        json.loads(line) for line in first.path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(decoded) == 24
    assert {record["id"] for record in records} == {
        record["id"] for record in decoded
    }


def test_missing_folder_is_not_created(tmp_path: Path) -> None:
    folder = tmp_path / "missing"
    store = QcAnnotationStore(folder, reviewer="alice")

    with pytest.raises(QcAnnotationUnavailableError):
        store.read()
    with pytest.raises(QcAnnotationUnavailableError):
        store.append_annotation(_draft())

    assert not folder.exists()


def test_writer_rejects_non_regular_or_symlink_targets(tmp_path: Path) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="alice")
    store.path.mkdir()
    with pytest.raises(QcAnnotationUnavailableError):
        store.append_annotation(_draft())
    store.path.rmdir()

    outside = tmp_path / "outside.jsonl"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        store.path.symlink_to(outside)
    except OSError:
        pytest.skip("File symlinks are unavailable on this platform")

    with pytest.raises(QcAnnotationUnavailableError):
        store.append_annotation(_draft())
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_read_skips_symlinked_jsonl_files(tmp_path: Path) -> None:
    folder = tmp_path / "annotations"
    folder.mkdir()
    store = QcAnnotationStore(folder, reviewer="alice")
    record = store.append_annotation(_draft())
    outside = tmp_path / "outside.jsonl"
    outside.write_text("not-json\n", encoding="utf-8")
    alias = folder / "external.jsonl"
    try:
        alias.symlink_to(outside)
    except OSError:
        pytest.skip("File symlinks are unavailable on this platform")

    state = store.read()

    assert state.malformed_lines == 0
    assert [item["id"] for item in state.current] == [record["id"]]
