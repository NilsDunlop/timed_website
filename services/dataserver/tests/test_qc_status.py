from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.qc_config import QcPathConfig
from timed_dataserver.qc_export import QcExportStore

from scripts import make_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "qc-status-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def qc_http_fixture(
    tmp_path_factory: pytest.TempPathFactory,
    write_audit: WriteAudit,
) -> tuple[Path, Path, Path, Path]:
    output = tmp_path_factory.mktemp("qc-status")
    packed_root = make_test_data._write_packed_fixture(output)
    export = make_test_data._write_qc_export_fixture(output)
    annotations = output / "annotations"
    annotations.mkdir()
    index = packed_root / make_test_data.PACKED_INDEX_NAME
    write_audit.watch(output, allow=(annotations,))
    return packed_root, index, export, annotations


def _settings(
    packed_root: Path,
    index: Path,
    export: Path,
    annotations: Path,
    state: Path,
) -> ServerSettings:
    return ServerSettings(
        data_roots=(packed_root,),
        state_dir=state,
        environment="local",
        qc_paths=(QcPathConfig(index, export, annotations),),
    )


def test_qc_status_loads_valid_export_and_redacts_hidden_statuses(
    qc_http_fixture: tuple[Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations = qc_http_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)

    with TestClient(app) as client:
        core = client.get("/api/qc/status", headers=AUTH, params={"path": str(index)})
        blinded = client.get(
            "/api/qc/status",
            headers=AUTH,
            params={"path": str(index), "blind": "1"},
        )

    assert core.status_code == 200
    payload = core.json()
    assert payload == {
        "available": True,
        "core_ready": True,
        "export_readable": True,
        "annotations_writable": True,
        "export_id": "fixture-qc-primary-20261029",
        "registry_hash": payload["registry_hash"],
        "row_count": 30,
        "hash_check": "ok",
        "missing_contract": [],
        "unknown_statuses": ["fixture_future_status", "fixture_hidden_future_status"],
        "malformed_annotation_lines": 0,
        "error": None,
    }
    assert len(payload["registry_hash"]) == 64
    assert blinded.status_code == 200
    assert blinded.json()["unknown_statuses"] == ["fixture_future_status"]
    serialized = json.dumps(payload)
    assert str(export) not in serialized
    assert str(annotations) not in serialized


def test_qc_status_reports_missing_annotation_folder_without_disabling_export(
    qc_http_fixture: tuple[Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    packed_root, index, export, _annotations = qc_http_fixture
    missing_annotations = tmp_path / "missing-annotations"
    app = create_app(
        _settings(
            packed_root,
            index,
            export,
            missing_annotations,
            tmp_path / "state",
        ),
        TOKEN,
    )

    with TestClient(app) as client:
        response = client.get("/api/qc/status", headers=AUTH, params={"path": str(index)})

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["annotations_writable"] is False


def test_qc_status_rejects_unconfigured_or_unguarded_indexes(
    qc_http_fixture: tuple[Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations = qc_http_fixture
    app = create_app(
        ServerSettings(
            data_roots=(packed_root,),
            state_dir=tmp_path / "state",
            environment="local",
        ),
        TOKEN,
    )
    outside = tmp_path / "outside.index.parquet"
    outside.write_bytes(b"outside")

    with TestClient(app) as client:
        unconfigured = client.get(
            "/api/qc/status", headers=AUTH, params={"path": str(index)}
        )
        forbidden = client.get(
            "/api/qc/status", headers=AUTH, params={"path": str(outside)}
        )
        missing = client.get(
            "/api/qc/status",
            headers=AUTH,
            params={"path": str(packed_root / "missing.index.parquet")},
        )

    assert unconfigured.status_code == 404
    assert forbidden.status_code == 403
    assert missing.status_code == 404
    assert str(export) not in unconfigured.text
    assert str(annotations) not in unconfigured.text


@pytest.mark.asyncio
async def test_qc_status_refreshes_export_off_the_event_loop(
    qc_http_fixture: tuple[Path, Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packed_root, index, export, annotations = qc_http_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)
    event_loop_thread = threading.get_ident()
    refresh_threads: list[int] = []
    original_refresh = QcExportStore.refresh

    def tracked_refresh(self: QcExportStore):
        refresh_threads.append(threading.get_ident())
        return original_refresh(self)

    monkeypatch.setattr(QcExportStore, "refresh", tracked_refresh)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/qc/status",
            headers=AUTH,
            params={"path": str(index)},
        )

    assert response.status_code == 200
    assert refresh_threads
    assert all(thread_id != event_loop_thread for thread_id in refresh_threads)
