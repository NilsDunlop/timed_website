from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import timed_dataserver.app as app_module
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.qc_annotations import QcAnnotationUnavailableError
from timed_dataserver.qc_config import QcPathConfig
from timed_dataserver.qc_export import QcExportSnapshot, inspect_qc_export

from scripts import make_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "qc-routes-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}", "Accept-Encoding": "gzip"}
_NAVIGATION_KEYS = frozenset(("experiment", "plate", "well", "site"))
_BLIND_REVIEW_KEYS = frozenset(
    ("entry_id", "review_group", "scope", "plate", "well", "site", "channel")
)


@pytest.fixture(scope="module")
def qc_routes_fixture(
    tmp_path_factory: pytest.TempPathFactory,
    write_audit: WriteAudit,
) -> tuple[Path, Path, Path, Path, QcExportSnapshot]:
    output = tmp_path_factory.mktemp("qc-routes")
    packed_root = make_test_data._write_packed_fixture(output)
    export = make_test_data._write_qc_export_fixture(output)
    annotations = output / "annotations"
    annotations.mkdir()
    index = packed_root / make_test_data.PACKED_INDEX_NAME
    write_audit.watch(output, allow=(annotations,))
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
        ("annotations", common),
    )


def _qc_routes(app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    return {
        (method, route.path): route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/qc/")
        for method in route.methods
    }


def _annotation_command(
    snapshot: QcExportSnapshot,
    *,
    scope: str = "field",
    well: str = "A01",
    label: str = "blurred",
) -> dict[str, object]:
    return {
        "record": "annotation",
        "export_id": snapshot.export_id,
        "scope": scope,
        "plate": 14610,
        "well": well,
        "site": 1 if scope == "field" else None,
        "channel": "DNA" if scope == "field" else "all",
        "label": label,
        "confidence": "high",
        "note": "Fixture contract review.",
    }


def test_blind_route_set_matches_the_qc_routes(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations, _snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)
    routes = _qc_routes(app)

    checked = {("GET", f"/api/qc/{name}") for name, _params in _route_specs(index, blind=True)}
    assert {key for key in routes if key[0] == "GET"} == checked
    # The one QC write: test_blind_annotation_routes_omit_barcode_and_derive_write_mode.
    assert {key for key in routes if key[0] != "GET"} == {("POST", "/api/qc/annotations")}
    for key, route in routes.items():
        assert "blind" in {parameter.name for parameter in route.dependant.query_params}, key


def test_qc_route_payloads_validate_against_declared_response_models(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, _shared_annotations, snapshot = qc_routes_fixture
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)
    routes = _qc_routes(app)
    responses: list[tuple[tuple[str, str], httpx.Response]] = []
    write = ("POST", "/api/qc/annotations")

    with TestClient(app) as client:
        for blind in (False, True):
            created = client.post(
                "/api/qc/annotations",
                headers=AUTH,
                params=_params(index, blind=blind),
                json=_annotation_command(snapshot, label="blurred"),
            )
            retracted = client.post(
                "/api/qc/annotations",
                headers=AUTH,
                params=_params(index, blind=blind),
                json={
                    "record": "retraction",
                    "export_id": snapshot.export_id,
                    "id": created.json()["record"]["id"],
                },
            )
            kept = client.post(
                "/api/qc/annotations",
                headers=AUTH,
                params=_params(index, blind=blind),
                json=_annotation_command(snapshot, scope="well", label="ok"),
            )
            responses.extend((write, response) for response in (created, retracted, kept))
        for blind in (False, True):
            for name, params in _route_specs(index, blind=blind):
                responses.append(
                    (
                        ("GET", f"/api/qc/{name}"),
                        client.get(f"/api/qc/{name}", headers=AUTH, params=params),
                    )
                )

    assert {key for key, _response in responses} == set(routes)
    for key, response in responses:
        assert response.status_code in (200, 201), (key, response.text)
        TypeAdapter(routes[key].response_model).validate_json(response.content)
    annotation_lists = [
        response.json()
        for key, response in responses
        if key == ("GET", "/api/qc/annotations")
    ]
    # Both field annotations are retracted; the later well annotation supersedes the first.
    assert [len(payload["annotations"]) for payload in annotation_lists] == [1, 1]


_TRUE_SPELLINGS = ("1", "true", "True", "TRUE", "on", "yes", "t", "y")
_FALSE_SPELLINGS = ("0", "false", "False", "off", "no", "f", "n")


def test_blind_spellings_select_the_blinded_representation(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, annotations, _snapshot = qc_routes_fixture
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)
    missing_site = {**_field_params(index), "site": 99}

    with TestClient(app) as client:

        def get(name: str, params: dict[str, object], spelling: str) -> httpx.Response:
            return client.get(
                f"/api/qc/{name}",
                headers=AUTH,
                params={**params, "blind": spelling},
            )

        for name, params in _route_specs(index, blind=False):
            blinded = get(name, params, "1")
            core = get(name, params, "0")
            assert blinded.status_code == core.status_code == 200, name
            assert blinded.content != core.content, name
            for spellings, expected in ((_TRUE_SPELLINGS, blinded), (_FALSE_SPELLINGS, core)):
                for spelling in spellings:
                    response = get(name, params, spelling)
                    assert response.status_code == 200, (name, spelling)
                    assert response.content == expected.content, (name, spelling)
                    assert response.headers["etag"] == expected.headers["etag"], (name, spelling)
        errors = {
            spelling: get("field", missing_site, spelling)
            for spelling in (*_TRUE_SPELLINGS, *_FALSE_SPELLINGS)
        }

    # Error responses use the handler's own parser, which accepts 1, true, on and
    # yes (any case) but not t or y: those two get the core error ETag.
    assert {response.status_code for response in errors.values()} == {404}
    assert len({response.content for response in errors.values()}) == 1
    blinded_etag = errors["1"].headers["etag"]
    core_etag = errors["0"].headers["etag"]
    assert blinded_etag != core_etag
    for spelling, response in errors.items():
        expected = core_etag if spelling in ("t", "y", *_FALSE_SPELLINGS) else blinded_etag
        assert response.headers["etag"] == expected, spelling


def test_blind_error_responses_never_carry_private_tokens(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, _shared_annotations, snapshot = qc_routes_fixture
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    app = create_app(_settings(packed_root, index, export, annotations, tmp_path / "state"), TOKEN)
    blind = _params(index, blind=True)

    with TestClient(app) as client:
        core_payloads = {
            name: client.get(f"/api/qc/{name}", headers=AUTH, params=params).json()
            for name, params in _route_specs(index, blind=False)
        }
        errors = [
            client.get(
                "/api/qc/field",
                headers=AUTH,
                params={**_field_params(index, blind=True), "site": 99},
            ),
            client.get(
                "/api/qc/field",
                headers=AUTH,
                params={**_field_params(index, blind=True), "site": 0},
            ),
            client.get(
                "/api/qc/dictionary",
                headers=AUTH,
                params={"path": str(packed_root / "unconfigured.index.parquet"), "blind": "1"},
            ),
            client.post(
                "/api/qc/annotations",
                headers=AUTH,
                params=blind,
                json={**_annotation_command(snapshot), "export_id": "stale-export"},
            ),
            client.post(
                "/api/qc/annotations",
                headers=AUTH,
                params=blind,
                json=_annotation_command(snapshot, well="H12"),
            ),
            client.post(
                "/api/qc/annotations",
                headers=AUTH,
                params=blind,
                json=_annotation_command(snapshot, label="not_a_label"),
            ),
        ]
    non_core_root = tmp_path / "non-core"
    non_core_root.mkdir()
    non_core = _make_non_core_export(non_core_root)
    non_core_app = create_app(_settings(*non_core, tmp_path / "non-core-state"), TOKEN)
    with TestClient(non_core_app) as client:
        errors.extend(
            client.get(f"/api/qc/{name}", headers=AUTH, params=params)
            for name, params in _route_specs(non_core[1], blind=True)
            if name != "status"
        )

    forbidden = _blind_forbidden_tokens(snapshot, core_payloads)
    assert {response.status_code for response in errors} == {404, 409, 422, 503}
    for response in errors:
        assert response.headers["etag"].startswith('W/"qc-'), response.text
        assert not _all_strings(response.json()).intersection(forbidden), response.text


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
    assert payloads["annotations"]["annotations"] == []
    assert fields["annotations"] == []
    assert field["annotations"] == []
    expected_labels = [dict(row) for row in snapshot.annotation_labels()]
    assert payloads["annotations"]["labels"] == expected_labels
    assert fields["labels"] == expected_labels
    assert field["labels"] == expected_labels


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


def test_qc_annotation_routes_append_join_retract_and_change_etags(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, _shared_annotations, snapshot = qc_routes_fixture
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "state"),
        TOKEN,
    )
    field_command = {
        "record": "annotation",
        "export_id": snapshot.export_id,
        "scope": "field",
        "plate": 14610,
        "well": "a01",
        "site": 1,
        "channel": "DNA",
        "label": "blurred",
        "confidence": "high",
        "note": "Fixture field review.",
    }
    well_command = {
        "record": "annotation",
        "export_id": snapshot.export_id,
        "scope": "well",
        "plate": 14610,
        "well": "A01",
        "site": None,
        "channel": "all",
        "label": "ok",
        "confidence": "medium",
        "note": "Fixture well review.",
    }

    with TestClient(app) as client:
        before_annotations = client.get(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
        )
        before_fields = client.get(
            "/api/qc/fields",
            headers=AUTH,
            params=_params(index),
        )
        field_write = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json=field_command,
        )
        well_write = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json=well_command,
        )
        current = client.get(
            "/api/qc/annotations",
            headers={**AUTH, "If-None-Match": before_annotations.headers["etag"]},
            params=_params(index),
        )
        fields = client.get(
            "/api/qc/fields",
            headers={**AUTH, "If-None-Match": before_fields.headers["etag"]},
            params=_params(index),
        )
        first_field = client.get(
            "/api/qc/field",
            headers=AUTH,
            params=_field_params(index, site=1),
        )
        second_field = client.get(
            "/api/qc/field",
            headers=AUTH,
            params=_field_params(index, site=2),
        )
        retraction = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json={
                "record": "retraction",
                "export_id": snapshot.export_id,
                "id": field_write.json()["record"]["id"],
            },
        )
        after_retraction = client.get(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
        )

    assert field_write.status_code == 201
    assert field_write.headers["content-encoding"] == "gzip"
    field_record = field_write.json()["record"]
    assert field_record["blinded"] is False
    assert field_record["well"] == "A01"
    assert field_record["export_id"] == snapshot.export_id
    assert field_record["registry_hash"] == snapshot.registry_hash
    assert field_record["app_version"]
    assert well_write.status_code == 201
    assert current.status_code == 200
    assert current.headers["etag"] != before_annotations.headers["etag"]
    assert len(current.json()["annotations"]) == 2
    assert fields.status_code == 200
    assert fields.headers["etag"] != before_fields.headers["etag"]
    assert len(fields.json()["annotations"]) == 2
    assert len(first_field.json()["annotations"]) == 2
    assert [record["scope"] for record in second_field.json()["annotations"]] == ["well"]
    assert retraction.status_code == 201
    assert retraction.json()["record"]["record"] == "retraction"
    assert [record["scope"] for record in after_retraction.json()["annotations"]] == [
        "well"
    ]


def test_blind_annotation_routes_omit_barcode_and_derive_write_mode(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, _shared_annotations, snapshot = qc_routes_fixture
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "state"),
        TOKEN,
    )
    command = {
        "record": "annotation",
        "export_id": snapshot.export_id,
        "scope": "field",
        "plate": 14610,
        "well": "A01",
        "site": 1,
        "channel": "DNA",
        "label": "ok",
        "confidence": "high",
        "note": "Fixture review.",
    }

    with TestClient(app) as client:
        core_write = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json=command,
        )
        blind_write = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index, blind=True),
            json={**command, "channel": "ER"},
        )
        core_payloads = {
            "annotations": client.get(
                "/api/qc/annotations", headers=AUTH, params=_params(index)
            ).json(),
            "fields": client.get(
                "/api/qc/fields", headers=AUTH, params=_params(index)
            ).json(),
            "field": client.get(
                "/api/qc/field", headers=AUTH, params=_field_params(index)
            ).json(),
        }
        blind_payloads = {
            "annotations": client.get(
                "/api/qc/annotations",
                headers=AUTH,
                params=_params(index, blind=True),
            ).json(),
            "fields": client.get(
                "/api/qc/fields",
                headers=AUTH,
                params=_params(index, blind=True),
            ).json(),
            "field": client.get(
                "/api/qc/field",
                headers=AUTH,
                params=_field_params(index, blind=True),
            ).json(),
        }

    assert core_write.status_code == 201
    assert core_write.json()["record"]["blinded"] is False
    assert "barcode" in core_write.json()["record"]
    assert blind_write.status_code == 201
    assert blind_write.json()["record"]["blinded"] is True
    assert "barcode" not in blind_write.json()["record"]
    for payload in core_payloads.values():
        assert payload["annotations"]
        assert all("barcode" in record for record in payload["annotations"])
    for payload in blind_payloads.values():
        assert payload["annotations"]
        assert all("barcode" not in record for record in payload["annotations"])


def test_qc_annotation_routes_reject_invalid_or_stale_writes_without_appending(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, _shared_annotations, snapshot = qc_routes_fixture
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "state"),
        TOKEN,
    )
    command = {
        "record": "annotation",
        "export_id": snapshot.export_id,
        "scope": "field",
        "plate": 14610,
        "well": "A01",
        "site": 1,
        "channel": "not-a-channel",
        "label": "blurred",
        "confidence": "medium",
        "note": "",
    }

    with TestClient(app) as client:
        invalid = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json=command,
        )
        stale = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json={**command, "channel": "DNA", "export_id": "stale-export"},
        )
        spoofed = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json={**command, "channel": "DNA", "reviewer": "spoofed"},
        )
        removed_label = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json={**command, "channel": "DNA", "label": "misregistered"},
        )

    assert invalid.status_code == 422
    assert stale.status_code == 409
    assert spoofed.status_code == 422
    assert removed_label.status_code == 422
    assert list(annotations.iterdir()) == []


def test_qc_annotation_write_does_not_reread_after_durable_append(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packed_root, index, export, _shared_annotations, snapshot = qc_routes_fixture
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "state"),
        TOKEN,
    )
    with TestClient(app) as client:
        initial = client.get(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
        )
        assert initial.status_code == 200
        store = app.state.qc_annotation_stores[index.resolve()]

        def fail_read() -> object:
            raise QcAnnotationUnavailableError("forced post-append read failure")

        monkeypatch.setattr(store, "read", fail_read)
        response = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json={
                "record": "annotation",
                "export_id": snapshot.export_id,
                "scope": "field",
                "plate": 14610,
                "well": "A01",
                "site": 1,
                "channel": "DNA",
                "label": "ok",
                "confidence": "medium",
                "note": "",
            },
        )

    assert response.status_code == 201
    lines = next(annotations.glob("annotations_*.jsonl")).read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 1


def test_qc_annotations_handle_missing_storage_and_report_malformed_lines(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
) -> None:
    packed_root, index, export, _shared_annotations, snapshot = qc_routes_fixture
    missing = tmp_path / "missing-annotations"
    missing_app = create_app(
        _settings(packed_root, index, export, missing, tmp_path / "missing-state"),
        TOKEN,
    )
    command = {
        "record": "annotation",
        "export_id": snapshot.export_id,
        "scope": "well",
        "plate": 14610,
        "well": "A01",
        "site": None,
        "channel": "all",
        "label": "ok",
        "confidence": "low",
        "note": "",
    }
    with TestClient(missing_app) as client:
        unavailable = client.get(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
        )
        rejected = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json=command,
        )

    assert unavailable.status_code == 200
    assert unavailable.json()["writable"] is False
    assert unavailable.json()["annotations"] == []
    assert rejected.status_code == 503
    assert not missing.exists()

    annotations = tmp_path / "malformed-annotations"
    annotations.mkdir()
    (annotations / "external.jsonl").write_text("not-json\n", encoding="utf-8")
    malformed_app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "malformed-state"),
        TOKEN,
    )
    with TestClient(malformed_app) as client:
        status_response = client.get(
            "/api/qc/status",
            headers=AUTH,
            params=_params(index),
        )
        annotation_response = client.get(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
        )

    assert status_response.json()["malformed_annotation_lines"] == 1
    assert annotation_response.json()["malformed_lines"] == 1


def test_qc_annotations_handle_read_only_storage_without_disabling_review(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packed_root, index, export, _shared_annotations, snapshot = qc_routes_fixture
    annotations = tmp_path / "read-only-annotations"
    annotations.mkdir()
    real_access = os.access

    def access(path: object, mode: int) -> bool:
        if Path(path) == annotations and mode == os.R_OK | os.W_OK | os.X_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr("timed_dataserver.qc_config.os.access", access)
    app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "state"),
        TOKEN,
    )
    command = {
        "record": "annotation",
        "export_id": snapshot.export_id,
        "scope": "well",
        "plate": 14610,
        "well": "A01",
        "site": None,
        "channel": "all",
        "label": "ok",
        "confidence": "low",
        "note": "",
    }

    with TestClient(app) as client:
        handshake = client.get("/api/handshake", headers=AUTH)
        status_response = client.get(
            "/api/qc/status", headers=AUTH, params=_params(index)
        )
        annotation_response = client.get(
            "/api/qc/annotations", headers=AUTH, params=_params(index)
        )
        rejected = client.post(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
            json=command,
        )

    assert handshake.json()["capabilities"]["qc_review"] is True
    assert handshake.json()["capabilities"]["qc_annotations_writable"] is False
    assert status_response.status_code == 200
    assert status_response.json()["available"] is True
    assert status_response.json()["annotations_writable"] is False
    assert annotation_response.status_code == 200
    assert annotation_response.json()["writable"] is False
    assert rejected.status_code == 503
    assert list(annotations.iterdir()) == []


def test_qc_annotation_etag_changes_with_live_writability(
    qc_routes_fixture: tuple[Path, Path, Path, Path, QcExportSnapshot],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packed_root, index, export, _shared_annotations, _snapshot = qc_routes_fixture
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    app = create_app(
        _settings(packed_root, index, export, annotations, tmp_path / "state"),
        TOKEN,
    )
    with TestClient(app) as client:
        writable = client.get(
            "/api/qc/annotations",
            headers=AUTH,
            params=_params(index),
        )
        real_access = os.access

        def access(path: object, mode: int) -> bool:
            if Path(path) == annotations and mode == os.R_OK | os.W_OK | os.X_OK:
                return False
            return real_access(path, mode)

        monkeypatch.setattr("timed_dataserver.qc_config.os.access", access)
        read_only = client.get(
            "/api/qc/annotations",
            headers={**AUTH, "If-None-Match": writable.headers["etag"]},
            params=_params(index),
        )

    assert writable.status_code == 200
    assert writable.json()["writable"] is True
    assert read_only.status_code == 200
    assert read_only.json()["writable"] is False
    assert read_only.headers["etag"] != writable.headers["etag"]


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
        if row["layer"] == "triage":
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
