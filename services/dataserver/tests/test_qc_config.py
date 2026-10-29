from __future__ import annotations

import os
from pathlib import Path
from types import MappingProxyType

import pytest
import timed_dataserver.cli as dataserver_cli
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.qc_config import (
    QcConfigurationError,
    QcPathConfig,
    parse_qc_path_configs,
)

TOKEN = "qc-config-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "data"
    root.mkdir()
    index = root / "fixture.index.parquet"
    index.write_bytes(b"fixture")
    export = tmp_path / "export"
    export.mkdir()
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    return root, index, export, annotations


def test_qc_cli_mappings_are_paired_in_export_order(tmp_path: Path) -> None:
    root, index, export, annotations = _paths(tmp_path)
    second_index = root / "second.index.parquet"
    second_index.write_bytes(b"second")
    second_export = tmp_path / "second-export"
    second_annotations = tmp_path / "second-annotations"

    configs = parse_qc_path_configs(
        [f"{index}={export}", f"{second_index}={second_export}"],
        [f"{second_index}={second_annotations}", f"{index}={annotations}"],
    )

    assert configs == (
        QcPathConfig(index, export, annotations),
        QcPathConfig(second_index, second_export, second_annotations),
    )


@pytest.mark.parametrize(
    ("exports", "annotations", "message"),
    [
        (["missing-separator"], [], "exactly one"),
        (["=folder"], ["=folder"], "non-empty"),
        (["relative=relative"], ["relative=relative"], "absolute"),
    ],
)
def test_qc_cli_mappings_reject_malformed_or_unpaired_values(
    exports: list[str],
    annotations: list[str],
    message: str,
) -> None:
    with pytest.raises(QcConfigurationError, match=message):
        parse_qc_path_configs(exports, annotations)


def test_qc_cli_mappings_reject_duplicates_or_mismatched_keys(tmp_path: Path) -> None:
    root, index, export, annotations = _paths(tmp_path)
    with pytest.raises(QcConfigurationError, match="repeats"):
        parse_qc_path_configs(
            [f"{index}={export}", f"{index}={tmp_path / 'second-export'}"],
            [f"{index}={annotations}"],
        )
    with pytest.raises(QcConfigurationError, match="same index paths"):
        parse_qc_path_configs([f"{index}={export}"], [])


def test_qc_config_is_guarded_and_not_browsable(tmp_path: Path) -> None:
    root, index, export, annotations = _paths(tmp_path)
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
        qc_paths=(QcPathConfig(index, export, annotations),),
    )

    with TestClient(create_app(settings, TOKEN)) as client:
        handshake = client.get("/api/handshake", headers=AUTH)
        export_listing = client.get(
            "/api/fs/list", headers=AUTH, params={"path": str(export)}
        )
        annotation_listing = client.get(
            "/api/fs/list", headers=AUTH, params={"path": str(annotations)}
        )
        configured_paths = client.app.state.qc_paths

    assert handshake.status_code == 200
    assert handshake.json()["capabilities"]["qc_review"] is True
    assert handshake.json()["capabilities"]["qc_annotations_writable"] is True
    assert export_listing.status_code == 403
    assert annotation_listing.status_code == 403
    assert isinstance(configured_paths, MappingProxyType)


def test_nested_qc_export_is_filtered_and_its_descendants_are_forbidden(
    tmp_path: Path,
) -> None:
    root, index, _outside_export, annotations = _paths(tmp_path)
    parent = root / "review-support"
    export = parent / "private-export"
    nested = export / "nested"
    visible = parent / "visible"
    nested.mkdir(parents=True)
    visible.mkdir()
    packed_looking = export / "hidden.index.parquet"
    packed_looking.write_bytes(b"private")
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
        qc_paths=(QcPathConfig(index, export, annotations),),
    )

    alias = root / "private-alias"
    try:
        alias.symlink_to(export, target_is_directory=True)
    except OSError:
        alias = None

    with TestClient(create_app(settings, TOKEN)) as client:
        root_listing = client.get("/api/fs/list", headers=AUTH, params={"path": str(root)})
        parent_listing = client.get(
            "/api/fs/list", headers=AUTH, params={"path": str(parent)}
        )
        forbidden = [
            client.get("/api/fs/list", headers=AUTH, params={"path": value})
            for value in (
                str(export),
                str(nested),
                str(packed_looking),
                str(export.relative_to(root)),
            )
        ]
        if alias is not None:
            forbidden.extend(
                (
                    client.get("/api/fs/list", headers=AUTH, params={"path": str(alias)}),
                    client.get(
                        "/api/fs/list",
                        headers=AUTH,
                        params={"path": str(alias / "nested")},
                    ),
                )
            )

    assert root_listing.status_code == 200
    root_names = {entry["name"] for entry in root_listing.json()["entries"]}
    assert "review-support" in root_names
    assert "private-alias" not in root_names
    assert parent_listing.status_code == 200
    assert [entry["name"] for entry in parent_listing.json()["entries"]] == ["visible"]
    assert all(response.status_code == 403 for response in forbidden)


@pytest.mark.parametrize("location", ("equal", "inside", "contains"))
def test_annotations_cannot_overlap_a_data_root(
    tmp_path: Path,
    location: str,
) -> None:
    root, index, export, _annotations = _paths(tmp_path)
    annotations = {
        "equal": root,
        "inside": root / "annotations",
        "contains": tmp_path,
    }[location]
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
        qc_paths=(QcPathConfig(index, export, annotations),),
    )

    with pytest.raises(QcConfigurationError, match="cannot overlap a data root"):
        create_app(settings, TOKEN)


@pytest.mark.parametrize("location", ("equal", "inside", "contains"))
def test_annotations_cannot_overlap_any_qc_export(
    tmp_path: Path,
    location: str,
) -> None:
    root, index, _export, _annotations = _paths(tmp_path)
    support = tmp_path / "qc-support"
    export = support / "export"
    export.mkdir(parents=True)
    second_index = root / "second.index.parquet"
    second_index.write_bytes(b"second")
    second_export = tmp_path / "second-export"
    second_export.mkdir()
    annotations = {
        "equal": export,
        "inside": export / "annotations",
        "contains": support,
    }[location]
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
        qc_paths=(
            QcPathConfig(index, export, annotations),
            QcPathConfig(
                second_index,
                second_export,
                tmp_path / "second-annotations",
            ),
        ),
    )

    with pytest.raises(QcConfigurationError, match="cannot overlap a QC export"):
        create_app(settings, TOKEN)


@pytest.mark.parametrize("private_parent", ("root", "ancestor"))
def test_qc_folder_cannot_equal_or_contain_a_data_root(
    tmp_path: Path,
    private_parent: str,
) -> None:
    root, index, _export, default_annotations = _paths(tmp_path)
    export = root if private_parent == "root" else tmp_path
    annotations = default_annotations
    if private_parent == "ancestor":
        annotations = tmp_path.parent / f"{tmp_path.name}-annotations"
        annotations.mkdir()
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
        qc_paths=(QcPathConfig(index, export, annotations),),
    )

    with pytest.raises(QcConfigurationError, match="cannot contain a data root"):
        create_app(settings, TOKEN)


def test_missing_qc_folders_are_reported_as_unavailable(tmp_path: Path) -> None:
    root, index, export, annotations = _paths(tmp_path)
    export.rmdir()
    annotations.rmdir()
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
        qc_paths=(QcPathConfig(index, export, annotations),),
    )

    with TestClient(create_app(settings, TOKEN)) as client:
        payload = client.get("/api/handshake", headers=AUTH).json()

    assert payload["capabilities"]["qc_review"] is False
    assert payload["capabilities"]["qc_annotations_writable"] is False


def test_qc_folder_checks_require_directory_search_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, index, export, annotations = _paths(tmp_path)
    checked: list[tuple[Path, int]] = []

    def access(path: Path, mode: int) -> bool:
        checked.append((path, mode))
        return True

    monkeypatch.setattr("timed_dataserver.qc_config.os.access", access)
    config = QcPathConfig(index, export, annotations)

    assert config.export_readable is True
    assert config.annotations_writable is True
    assert checked == [
        (export, os.R_OK | os.X_OK),
        (annotations, os.R_OK | os.W_OK | os.X_OK),
    ]


def test_qc_index_must_be_a_guarded_file(tmp_path: Path) -> None:
    root, _index, export, annotations = _paths(tmp_path)
    outside = tmp_path / "outside.index.parquet"
    outside.write_bytes(b"outside")
    settings = ServerSettings(
        data_roots=(root,),
        state_dir=tmp_path / "state",
        environment="local",
        qc_paths=(QcPathConfig(outside, export, annotations),),
    )

    with pytest.raises(QcConfigurationError, match="Invalid configured QC index"):
        create_app(settings, TOKEN)


def test_cli_passes_qc_mappings_into_server_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, index, export, annotations = _paths(tmp_path)
    captured: list[ServerSettings] = []

    async def fake_serve(settings: ServerSettings, **_kwargs: object) -> None:
        captured.append(settings)

    monkeypatch.setattr(dataserver_cli, "serve", fake_serve)

    result = dataserver_cli.main(
        [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--data-root",
            str(root),
            "--connection-file",
            str(tmp_path / "connection.json"),
            "--environment",
            "local",
            "--qc-export",
            f"{index}={export}",
            "--qc-annotations",
            f"{index}={annotations}",
        ]
    )

    assert result == 0
    assert captured[0].qc_paths == (QcPathConfig(index, export, annotations),)
