"""A path this account can't read never stops the server: it is left out and reported."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import timed_dataserver.access as access
import timed_dataserver.cli as dataserver_cli
from fastapi.testclient import TestClient
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.plate_metadata import PlateMetadataError
from timed_dataserver.qc_config import QcPathConfig
from timed_dataserver.timed_mask_config import TimedMaskMapping

TOKEN = "access-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DENIED = "Permission denied"


@dataclass(frozen=True)
class Layout:
    """A data root inside ``area``, its two collections and their folders beside it."""

    base: Path
    area: Path
    data: Path
    timed: Path
    index: Path
    masks: Path
    seg: Path
    export: Path
    annotations: Path
    metadata: Path

    def settings(self, **changes: Any) -> ServerSettings:
        values: dict[str, Any] = {
            "data_roots": (self.data,),
            "state_dir": self.base / "state",
            "environment": "local",
            "metadata_root": self.metadata,
            "qc_paths": (QcPathConfig(self.index, self.export, self.annotations),),
            "timed_masks": (
                TimedMaskMapping(self.timed, self.masks),
                TimedMaskMapping(self.timed, self.seg, "production"),
            ),
            "collections": (str(self.timed), str(self.index)),
        }
        values.update(changes)
        return ServerSettings(**values)


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    data = tmp_path / "area" / "data"
    timed = data / "timed"
    timed.mkdir(parents=True)
    index = data / "cells.index.parquet"
    index.write_bytes(b"index")
    folders = {name: tmp_path / name for name in ("masks", "seg", "export", "annotations")}
    for folder in folders.values():
        folder.mkdir()
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    return Layout(tmp_path, tmp_path / "area", data, timed, index, metadata=metadata, **folders)


class Locks:
    """Folders this account can't read, as if their mode were 000.

    A locked folder can be looked up but not listed; a path below it can't
    even be looked up. Only the access checks see the locks.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.folders: list[Path] = []
        monkeypatch.setattr(access, "denied", self._below)
        monkeypatch.setattr(access, "unreadable", self._reason)
        monkeypatch.setattr(access, "unwritable", self._reason)

    def lock(self, *folders: Path) -> None:
        self.folders.extend(folders)

    def _below(self, path: Path) -> bool:
        return any(path != folder and path.is_relative_to(folder) for folder in self.folders)

    def _reason(self, path: Path) -> str | None:
        return DENIED if path in self.folders or self._below(path) else None


@pytest.fixture
def locks(monkeypatch: pytest.MonkeyPatch) -> Locks:
    return Locks(monkeypatch)


@pytest.fixture
def served() -> Iterator[Any]:
    clients: list[TestClient] = []

    def serve(settings: ServerSettings) -> tuple[TestClient, Any]:
        client = TestClient(create_app(settings, TOKEN))
        client.__enter__()
        clients.append(client)
        return client, client.app.state.context  # type: ignore[attr-defined]

    yield serve
    for client in clients:
        client.__exit__(None, None, None)


def _issue(kind: str, *, path: Path | None = None, collection: Path | None = None) -> dict:
    return {
        "kind": kind,
        "reason": DENIED,
        "path": None if path is None else str(path),
        "collection": None if collection is None else str(collection),
    }


def test_a_readable_configuration_reports_nothing(layout: Layout, served: Any) -> None:
    client, context = served(layout.settings())
    handshake = client.get("/api/handshake", headers=AUTH).json()

    assert handshake["access_issues"] == []
    assert handshake["data_roots"] == [str(layout.data.resolve())]
    assert handshake["capabilities"]["plate_metadata"] is True
    assert handshake["capabilities"]["qc_review"] is True
    assert len(context.timed_masks) == 2
    assert len(context.qc_paths) == 1


def test_a_data_root_it_cant_look_up_is_left_out(
    layout: Layout, locks: Locks, served: Any
) -> None:
    vault = layout.base / "vault"
    extra = vault / "extra"
    extra.mkdir(parents=True)
    locks.lock(vault)

    client, _context = served(layout.settings(data_roots=(layout.data, extra)))
    handshake = client.get("/api/handshake", headers=AUTH).json()

    assert handshake["data_roots"] == [str(layout.data.resolve())]
    assert handshake["access_issues"] == [_issue("data_root", path=extra)]


def test_collections_it_cant_read_take_their_features_with_them(
    layout: Layout, locks: Locks, served: Any
) -> None:
    locks.lock(layout.area)

    client, context = served(layout.settings())
    handshake = client.get("/api/handshake", headers=AUTH).json()

    # The root's own issue is left out: each collection in it is reported instead.
    assert handshake["access_issues"] == [
        _issue("collection", path=layout.timed, collection=layout.timed),
        _issue("collection", path=layout.index, collection=layout.index),
    ]
    assert handshake["data_roots"] == []
    assert handshake["capabilities"]["qc_review"] is False
    assert context.timed_masks == ()
    assert dict(context.qc_paths) == {}
    # Folders left out of service stay private all the same.
    for folder in (layout.export, layout.annotations):
        assert context.qc_private_roots.contains(folder.resolve())
    for folder in (layout.masks, layout.seg):
        assert context.mask_private_roots.contains(folder.resolve())
    # With no data root left, a relative path is refused rather than failing.
    assert client.get("/api/fs/list", headers=AUTH).json()["entries"] == []
    assert client.get("/api/fs/list", params={"path": "timed"}, headers=AUTH).status_code == 403


def test_feature_folders_it_cant_read_are_reported_without_their_paths(
    layout: Layout, locks: Locks, served: Any
) -> None:
    locks.lock(layout.metadata, layout.masks, layout.seg, layout.export)

    client, context = served(layout.settings())
    handshake = client.get("/api/handshake", headers=AUTH).json()

    # Mask and QC folders are never named: profiles keep those paths private.
    assert handshake["access_issues"] == [
        _issue("metadata", path=layout.metadata),
        _issue("masks", collection=layout.timed),
        _issue("segmentation", collection=layout.timed),
        _issue("qc_export", collection=layout.index),
    ]
    assert handshake["capabilities"]["plate_metadata"] is False
    assert context.metadata_catalog is None
    assert context.timed_masks == ()
    # QC stays configured, so its status keeps answering, as for any unreadable export.
    assert list(context.qc_paths) == [layout.index.resolve()]


def test_annotations_it_cant_write_are_reported_and_qc_stays_configured(
    layout: Layout, locks: Locks, served: Any
) -> None:
    locks.lock(layout.annotations)

    client, context = served(layout.settings())
    handshake = client.get("/api/handshake", headers=AUTH).json()

    assert handshake["access_issues"] == [_issue("qc_annotations", collection=layout.index)]
    assert list(context.qc_paths) == [layout.index.resolve()]
    assert len(context.timed_masks) == 2


def test_a_collection_outside_every_data_root_is_never_opened(
    layout: Layout, locks: Locks, served: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = layout.base / "elsewhere"
    outside.mkdir()
    locks.lock(outside)
    opened: list[Path] = []
    check = access.unreadable

    def unreadable(path: Path) -> str | None:
        opened.append(path)
        return check(path)

    monkeypatch.setattr(access, "unreadable", unreadable)
    client, _context = served(layout.settings(collections=(str(layout.timed), str(outside))))

    assert client.get("/api/handshake", headers=AUTH).json()["access_issues"] == []
    assert outside not in opened
    assert layout.timed in opened


def test_missing_paths_keep_their_existing_errors(layout: Layout, served: Any) -> None:
    with pytest.raises(ValueError, match="Data root does not exist"):
        create_app(layout.settings(data_roots=(layout.base / "absent",)), TOKEN)
    with pytest.raises(PlateMetadataError, match="Metadata root does not exist"):
        create_app(layout.settings(metadata_root=layout.base / "absent"), TOKEN)

    layout.seg.rmdir()
    layout.export.rmdir()
    client, context = served(layout.settings())
    handshake = client.get("/api/handshake", headers=AUTH).json()
    assert handshake["access_issues"] == []
    assert len(context.timed_masks) == 2
    assert handshake["capabilities"]["qc_review"] is False


def test_the_checks_tell_permission_problems_from_missing_paths(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    absent = layout.base / "absent"
    assert access.unreadable(layout.timed) is None
    assert access.unreadable(layout.index) is None
    assert access.unreadable(absent) is None
    assert access.denied(layout.timed) is False
    assert access.denied(absent) is False
    assert access.unwritable(layout.annotations) is None
    assert access.unwritable(absent) is None

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, DENIED)

    monkeypatch.setattr(access, "open", refuse, raising=False)
    assert access.unreadable(layout.index) == DENIED
    monkeypatch.setattr(access.os, "scandir", refuse)
    assert access.unreadable(layout.timed) == DENIED


def test_qc_folder_checks_answer_false_when_a_folder_cant_be_looked_up(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_path: Path) -> bool:
        raise PermissionError(13, DENIED)

    monkeypatch.setattr(Path, "is_dir", refuse)
    config = QcPathConfig(layout.index, layout.export, layout.annotations)
    assert config.export_readable is False
    assert config.annotations_writable is False


def test_the_cli_passes_collections_and_refuses_relative_ones(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[ServerSettings] = []

    async def fake_serve(settings: ServerSettings, **_kwargs: object) -> None:
        captured.append(settings)

    monkeypatch.setattr(dataserver_cli, "serve", fake_serve)
    arguments = [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--data-root",
        str(layout.data),
        "--connection-file",
        str(layout.base / "connection.json"),
        "--environment",
        "local",
    ]
    assert dataserver_cli.main([*arguments, "--collection", str(layout.timed)]) == 0
    assert captured[0].collections == (str(layout.timed),)
    with pytest.raises(SystemExit):
        dataserver_cli.main([*arguments, "--collection", "relative/zarr"])


@contextmanager
def _modes(modes: dict[Path, int]) -> Iterator[None]:
    originals = {path: path.stat().st_mode & 0o777 for path in modes}
    for path, mode in modes.items():
        path.chmod(mode)
    try:
        yield
    finally:
        for path, mode in originals.items():
            path.chmod(mode)


POSIX_ACCOUNT = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="file modes bind only a POSIX account other than root",
)


@pytest.mark.posix_only
@POSIX_ACCOUNT
def test_real_modes_on_a_collection_metadata_annotations_and_a_root(
    layout: Layout, served: Any
) -> None:
    vault = layout.base / "vault"
    extra = vault / "extra"
    extra.mkdir(parents=True)
    modes = {layout.timed: 0, layout.metadata: 0, layout.annotations: 0o555, vault: 0}
    with _modes(modes):
        client, context = served(layout.settings(data_roots=(layout.data, extra)))
        handshake = client.get("/api/handshake", headers=AUTH).json()
        status = client.get("/api/qc/status", params={"path": str(layout.index)}, headers=AUTH)

    issues = handshake["access_issues"]
    assert [(issue["kind"], issue["path"], issue["collection"]) for issue in issues] == [
        ("data_root", str(extra), None),
        ("metadata", str(layout.metadata), None),
        ("collection", str(layout.timed), str(layout.timed)),
        ("qc_annotations", None, str(layout.index)),
    ]
    assert issues[-1]["reason"] == access.NOT_WRITABLE
    assert handshake["data_roots"] == [str(layout.data.resolve())]
    assert handshake["capabilities"]["plate_metadata"] is False
    assert handshake["capabilities"]["qc_review"] is True
    assert handshake["capabilities"]["qc_annotations_writable"] is False
    assert context.timed_masks == ()
    assert status.status_code == 200
    assert status.json()["annotations_writable"] is False


@pytest.mark.posix_only
@POSIX_ACCOUNT
def test_real_modes_on_the_segmentation_and_the_qc_export(
    layout: Layout, served: Any
) -> None:
    with _modes({layout.seg: 0, layout.export: 0}):
        client, context = served(layout.settings())
        handshake = client.get("/api/handshake", headers=AUTH).json()
        status = client.get("/api/qc/status", params={"path": str(layout.index)}, headers=AUTH)

    assert [(issue["kind"], issue["collection"]) for issue in handshake["access_issues"]] == [
        ("segmentation", str(layout.timed)),
        ("qc_export", str(layout.index)),
    ]
    assert handshake["capabilities"]["qc_review"] is False
    assert [mapping.layout for mapping in context.timed_masks] == ["export"]
    assert status.status_code == 200
    assert status.json()["export_readable"] is False
