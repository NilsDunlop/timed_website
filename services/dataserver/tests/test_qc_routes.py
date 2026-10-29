from __future__ import annotations

import csv
import hashlib
import json
import threading
from pathlib import Path

import httpx
import pytest
import timed_dataserver.app as app_module
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.qc_config import QcPathConfig
from timed_dataserver.qc_export import QcExportSnapshot, inspect_qc_export

from scripts import make_test_data

TOKEN = "qc-routes-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}", "Accept-Encoding": "gzip"}
_NAVIGATION_KEYS = frozenset(("experiment", "plate", "well", "site"))
_BLIND_REVIEW_KEYS = frozenset(
    ("entry_id", "review_group", "scope", "plate", "well", "site", "channel")
)


@pytest.fixture(scope="module")
def qc_routes_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, Path, Path, QcExportSnapshot]:
    output = tmp_path_factory.mktemp("qc-routes")
    packed_root = make_test_data._write_packed_fixture(output)
    export = make_test_data._write_qc_export_fixture(output)
    annotations = output / "annotations"
    annotations.mkdir()
    index = packed_root / make_test_data.PACKED_INDEX_NAME
    return packed_root, index, export, annotations, inspect_qc_export(export)


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


def _params(index: Path, *, blind: bool = False) -> dict[str, object]:
    return {"path": str(index), "blind": "1" if blind else "0"}


def _field_params(index: Path, *, blind: bool = False, site: int = 1) -> dict[str, object]:
    return {
        **_params(index, blind=blind),
        "plate": 14610,
        "well": "a01",
        "site": site,
    }


def _all_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {
            item
            for key, nested in value.items()
            for item in (str(key), *_all_strings(nested))
        }
    if isinstance(value, list):
        return {item for nested in value for item in _all_strings(nested)}
    return set()


def _blind_forbidden_tokens(
    snapshot: QcExportSnapshot,
    core_payloads: dict[str, dict[str, object]],
) -> set[str]:
    tokens: set[str] = set()
    for filename, columns in snapshot.hidden_columns.items():
        table = snapshot.table(filename)
        for column in columns:
            tokens.add(column)
            tokens.update(value for value in table[column].to_pylist() if isinstance(value, str))

    for row in snapshot.dictionary_rows():
        marker = row.get("markers")
        if marker:
            tokens.update(("markers", str(marker)))
        if row.get("file") == "site_metrics.csv" and row.get("layer") == "key":
            column = str(row.get("column") or "")
            if column not in _NAVIGATION_KEYS:
                tokens.update(
                    value
                    for value in snapshot.table("site_metrics.csv")[column].to_pylist()
                    if isinstance(value, str)
                )
        if row.get("blind") == "hide":
            tokens.update(
                value
                for value in row.values()
                if isinstance(value, str)
                and (value.startswith("fixture_") or value.startswith("fixture-"))
            )

    ranges = snapshot.table("display_ranges.csv")
    tokens.add("plate_barcode")
    tokens.update(str(value) for value in ranges["plate_barcode"].to_pylist())
    fields_metadata = core_payloads["fields"]["well_metadata"]
    field_metadata = core_payloads["field"]["well_metadata"]
    assert isinstance(fields_metadata, dict)
    assert isinstance(field_metadata, dict)
    tokens.update(_all_strings(fields_metadata))
    tokens.update(_all_strings(field_metadata))
    return tokens


def _route_specs(index: Path, *, blind: bool) -> tuple[tuple[str, dict[str, object]], ...]:
    common = _params(index, blind=blind)
    return (
        ("status", common),
        ("dictionary", common),
        ("fields", common),
        ("field", _field_params(index, blind=blind)),
        ("triage", common),
        ("display-ranges", common),
        ("review-list", common),
    )


def test_qc_routes_require_authentication_and_return_gzip_etags(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations, _snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)

    with TestClient(app) as client:
        unauthorized = client.get("/api/qc/dictionary", params=_params(index))
        responses = {
            name: client.get(f"/api/qc/{name}", headers=AUTH, params=params)
            for name, params in _route_specs(index, blind=False)
        }

    assert unauthorized.status_code == 401
    assert unauthorized.headers["content-encoding"] == "gzip"
    assert unauthorized.headers["etag"].startswith('W/"qc-')
    assert unauthorized.headers["www-authenticate"] == "Bearer"
    for response in responses.values():
        assert response.status_code == 200, response.text
        assert response.headers["content-encoding"] == "gzip"
        assert response.headers["vary"] == "Accept-Encoding"
        assert response.headers["etag"].startswith('W/"qc-')


def test_core_qc_routes_preserve_export_columns_metadata_and_order(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations, snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)

    with TestClient(app) as client:
        payloads = {
            name: client.get(f"/api/qc/{name}", headers=AUTH, params=params).json()
            for name, params in _route_specs(index, blind=False)
        }

    fields = payloads["fields"]
    field = payloads["field"]
    review = payloads["review-list"]
    assert set(fields["columns"]) == set(snapshot.table("site_metrics.csv").column_names)
    assert set(fields["well_metadata"]) == {
        "gene",
        "well_type",
        "perturbation_type",
        "is_control",
    }
    assert set(field["field"]) == set(snapshot.table("site_metrics.csv").column_names)
    assert set(field["well_metadata"]) == set(fields["well_metadata"])
    assert [row["entry_id"] for row in review["rows"]] == snapshot.table(
        "review_list.csv"
    )["entry_id"].to_pylist()
    assert len(payloads["triage"]["rows"]) == snapshot.table(
        "triage_plate_channel.csv"
    ).num_rows
    assert len(payloads["display-ranges"]["rows"]) == snapshot.table(
        "display_ranges.csv"
    ).num_rows
    assert payloads["status"]["core_ready"] is True


def test_every_blind_qc_route_removes_private_and_header_tokens(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations, snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)

    with TestClient(app) as client:
        core_payloads = {
            name: client.get(f"/api/qc/{name}", headers=AUTH, params=params).json()
            for name, params in _route_specs(index, blind=False)
        }
        blind_payloads = {
            name: client.get(f"/api/qc/{name}", headers=AUTH, params=params).json()
            for name, params in _route_specs(index, blind=True)
        }

    forbidden = _blind_forbidden_tokens(snapshot, core_payloads)
    for payload in blind_payloads.values():
        assert not _all_strings(payload).intersection(forbidden)
    assert "well_metadata" not in blind_payloads["fields"]
    assert "well_metadata" not in blind_payloads["field"]
    assert set(blind_payloads["review-list"]["rows"][0]) == _BLIND_REVIEW_KEYS
    assert _NAVIGATION_KEYS.issubset(blind_payloads["fields"]["columns"])
    non_navigation_keys = {
        str(row.get("column") or "")
        for row in snapshot.dictionary_rows(filename="site_metrics.csv")
        if row.get("layer") == "key" and row.get("column") not in _NAVIGATION_KEYS
    }
    assert not set(blind_payloads["fields"]["columns"]).intersection(non_navigation_keys)
    assert not set(blind_payloads["field"]["field"]).intersection(non_navigation_keys)
    assert not {
        str(row["column"])
        for row in blind_payloads["dictionary"]["rows"]
        if row["file"] == "site_metrics.csv"
    }.intersection(non_navigation_keys)


def test_qc_field_normalizes_well_reports_missing_and_separates_etags(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations, _snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)

    with TestClient(app) as client:
        first = client.get("/api/qc/field", headers=AUTH, params=_field_params(index, site=1))
        second = client.get("/api/qc/field", headers=AUTH, params=_field_params(index, site=2))
        missing = client.get("/api/qc/field", headers=AUTH, params=_field_params(index, site=99))
        not_modified = client.get(
            "/api/qc/field",
            headers={**AUTH, "If-None-Match": first.headers["etag"]},
            params=_field_params(index, site=1),
        )

    assert first.status_code == 200
    assert first.json()["field"]["well"] == "A01"
    assert second.status_code == 200
    assert first.headers["etag"] != second.headers["etag"]
    assert missing.status_code == 404
    assert missing.headers["content-encoding"] == "gzip"
    assert missing.headers["etag"].startswith('W/"qc-')
    assert not_modified.status_code == 304
    assert not_modified.content == b""


def test_qc_validation_errors_use_the_scoped_json_response_contract(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations, _snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)

    with TestClient(app) as client:
        response = client.get(
            "/api/qc/field",
            headers=AUTH,
            params={**_field_params(index), "site": 0},
        )

    assert response.status_code == 422
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["etag"].startswith('W/"qc-')
    assert response.headers["cache-control"] == "private, no-cache"


def test_qc_etag_changes_with_the_installed_server_version(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packed_root, index, export, annotations, _snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)

    with TestClient(app) as client:
        first = client.get("/api/qc/dictionary", headers=AUTH, params=_params(index))
        monkeypatch.setattr(app_module, "__version__", "0.5.0.dev999")
        second = client.get("/api/qc/dictionary", headers=AUTH, params=_params(index))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["etag"] != second.headers["etag"]


@pytest.mark.parametrize("endpoint", ("fields", "field"))
def test_qc_metadata_routes_include_phase_one_metadata_in_etags(
    tmp_path: Path,
    endpoint: str,
) -> None:
    packed_root = make_test_data._write_packed_fixture(tmp_path)
    export = make_test_data._write_qc_export_fixture(tmp_path)
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    index = packed_root / make_test_data.PACKED_INDEX_NAME
    params = _field_params(index) if endpoint == "field" else _params(index)
    first_app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "state-before"),
        TOKEN,
    )

    with TestClient(first_app) as client:
        first = client.get(f"/api/qc/{endpoint}", headers=AUTH, params=params)
        unchanged = client.get(
            f"/api/qc/{endpoint}",
            headers={**AUTH, "If-None-Match": first.headers["etag"]},
            params=params,
        )

    metadata_path = packed_root / "metadata" / "metadata_idr0080.csv"
    with metadata_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)
    changed_gene = "etag_changed_gene"
    rows[0]["gene"] = changed_gene
    with metadata_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    changed_app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "state-after"),
        TOKEN,
    )
    with TestClient(changed_app) as client:
        changed = client.get(
            f"/api/qc/{endpoint}",
            headers={**AUTH, "If-None-Match": first.headers["etag"]},
            params=params,
        )

    assert first.status_code == 200
    assert unchanged.status_code == 304
    assert changed.status_code == 200
    assert changed.headers["etag"] != first.headers["etag"]
    metadata = changed.json()["well_metadata"]
    if endpoint == "field":
        assert metadata["gene"] == changed_gene
    else:
        assert changed_gene in metadata["gene"]


def _make_non_core_export(output: Path) -> tuple[Path, Path, Path, Path]:
    packed_root = make_test_data._write_packed_fixture(output)
    export = make_test_data._write_qc_export_fixture(output)
    annotations = output / "annotations"
    annotations.mkdir()
    columns_path = export / "columns.csv"
    with columns_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)
    for row in rows:
        if row["layer"] == "panel":
            row["layer"] = "context"
    with columns_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    version_path = export / "version.json"
    version = json.loads(version_path.read_text(encoding="utf-8"))
    version["files"]["columns.csv"] = hashlib.sha256(columns_path.read_bytes()).hexdigest()
    version_path.write_text(
        json.dumps(version, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return packed_root, packed_root / make_test_data.PACKED_INDEX_NAME, export, annotations


def test_non_core_export_is_status_only(tmp_path: Path) -> None:
    packed_root, index, export, annotations = _make_non_core_export(tmp_path)
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)

    with TestClient(app) as client:
        status_response = client.get("/api/qc/status", headers=AUTH, params=_params(index))
        unavailable = [
            client.get(f"/api/qc/{name}", headers=AUTH, params=params)
            for name, params in _route_specs(index, blind=False)
            if name != "status"
        ]

    assert status_response.status_code == 200
    assert status_response.json()["available"] is True
    assert status_response.json()["core_ready"] is False
    assert all(response.status_code == 503 for response in unavailable)
    assert all(response.headers["content-encoding"] == "gzip" for response in unavailable)
    assert all(response.headers["etag"].startswith('W/"qc-') for response in unavailable)


@pytest.mark.asyncio
async def test_qc_payload_construction_runs_off_the_event_loop(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packed_root, index, export, annotations, _snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)
    event_loop_thread = threading.get_ident()
    payload_threads: list[int] = []
    original = app_module.fields_payload

    def tracked_payload(*args: object, **kwargs: object) -> object:
        payload_threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(app_module, "fields_payload", tracked_payload)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/qc/fields",
            headers=AUTH,
            params=_params(index, blind=True),
        )

    assert response.status_code == 200
    assert payload_threads
    assert all(thread_id != event_loop_thread for thread_id in payload_threads)
