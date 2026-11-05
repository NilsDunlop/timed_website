"""Temporary refactor snapshots: a served tree, a client factory and a normaliser.

The last commit of the Gate A refactor deletes this folder.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pytest
from fastapi.testclient import TestClient
from served_stores import write_berzelius_plate, write_native_plate
from timed_dataserver.app import ServerSettings, create_app
from timed_dataserver.qc_config import QcPathConfig

from scripts import make_test_data
from scripts.make_test_data import generate_test_data

if TYPE_CHECKING:
    from conftest import WriteAudit

TOKEN = "refactor-snapshot-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
GENERIC_INDEX_NAME = "generic-cell-painting.index.parquet"
type Tree = Literal["generated", "berzelius", "generic"]


@dataclass(frozen=True)
class ServedTree:
    """Read-only roots for every dataset kind the server serves."""

    generated: Path
    berzelius: Path
    generic: Path

    @property
    def index(self) -> Path:
        return self.generated / "packed" / make_test_data.PACKED_FIXTURE_DIRECTORY / (
            make_test_data.PACKED_INDEX_NAME
        )

    @property
    def export(self) -> Path:
        return self.generated / make_test_data.GENERATED_QC_EXPORT_DIRECTORY

    @property
    def generic_index(self) -> Path:
        return self.generic / GENERIC_INDEX_NAME

    def roots(self) -> dict[str, Path]:
        return {"generated": self.generated, "berzelius": self.berzelius, "generic": self.generic}


@pytest.fixture(scope="session")
def served_tree(
    tmp_path_factory: pytest.TempPathFactory, session_write_audit: WriteAudit
) -> ServedTree:
    base = tmp_path_factory.mktemp("served-tree")
    generated = base / "generated"
    generate_test_data(generated)
    write_native_plate(generated / "native_plate.zarr")
    berzelius = base / "berzelius"
    berzelius.mkdir()
    write_berzelius_plate(berzelius)
    generic = Path(
        shutil.copytree(
            generated / "packed" / make_test_data.PACKED_FIXTURE_DIRECTORY,
            base / "generic",
        )
    )
    (generic / make_test_data.PACKED_INDEX_NAME).rename(generic / GENERIC_INDEX_NAME)
    session_write_audit.watch(base)
    return ServedTree(
        generated=generated.resolve(),
        berzelius=berzelius.resolve(),
        generic=generic.resolve(),
    )


@pytest.fixture
def served_client(served_tree: ServedTree, tmp_path: Path) -> Callable[[Tree], TestClient]:
    """Build a client for one served tree; state and annotations live in tmp_path."""

    def make(tree: Tree) -> TestClient:
        state = tmp_path / f"state-{tree}"
        if tree == "generated":
            annotations = tmp_path / "annotations"
            annotations.mkdir(exist_ok=True)
            settings = ServerSettings(
                data_roots=(served_tree.generated,),
                state_dir=state,
                environment="local",
                metadata_root=served_tree.generated / "metadata",
                qc_paths=(QcPathConfig(served_tree.index, served_tree.export, annotations),),
            )
        elif tree == "berzelius":
            settings = ServerSettings(
                data_roots=(served_tree.berzelius,),
                state_dir=state,
                environment="local",
                metadata_root=served_tree.berzelius / "metadata",
            )
        else:
            settings = ServerSettings(
                data_roots=(served_tree.generic,), state_dir=state, environment="local"
            )
        return TestClient(create_app(settings, TOKEN))

    return make


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_PLACEHOLDER_KEYS = {
    "app_version": "<app_version>",
    "hostname": "<hostname>",
    "linked_at": "<linked_at>",
    "python_version": "<python_version>",
    "reviewer": "<reviewer>",
    "server_version": "<server_version>",
    "time": "<time>",
}


@dataclass
class Normaliser:
    """Make responses comparable across runs, machines and platforms.

    Roots become ``<name>`` with POSIX separators, dataset and annotation ids
    follow their first appearance, and run-specific values become placeholders.
    """

    roots: dict[str, Path]
    ids: dict[str, str] = field(default_factory=dict)

    def dataset(self, dataset_id: str) -> str:
        return self.ids.setdefault(dataset_id, f"<dataset-{len(self.ids) + 1}>")

    def _text(self, value: str) -> str:
        for name, root in self.roots.items():
            for form in (str(root), root.as_posix()):
                if form in value:
                    value = value.replace(form, f"<{name}>")
                    value = value.replace("\\", "/")
        for raw, placeholder in self.ids.items():
            value = value.replace(raw, placeholder)
        return _UUID.sub(lambda match: self._uuid(match.group(0)), value)

    def _uuid(self, value: str) -> str:
        return self.ids.setdefault(value, f"<annotation-{len(self.ids) + 1}>")

    def __call__(self, value: object) -> object:
        if isinstance(value, str):
            return self._text(value)
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, dict):
            return {
                self._text(str(key)): (
                    _PLACEHOLDER_KEYS[key] if key in _PLACEHOLDER_KEYS else self(item)
                )
                for key, item in value.items()
            }
        return value


@pytest.fixture
def normaliser(served_tree: ServedTree) -> Iterator[Normaliser]:
    yield Normaliser(served_tree.roots())
