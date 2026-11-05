"""Temporary refactor snapshot: the generated fixture tree, byte for byte.

Recorded from unchanged code on 2026-11-05. Text fixtures are written in text
mode, so Windows and POSIX bytes differ; each platform has its own manifest.
Set TIMED_UPDATE_FIXTURE_MANIFEST=1 to rewrite this platform's manifest. The
last commit of the Gate A refactor deletes this folder.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.make_test_data import generate_test_data

MANIFEST = Path(__file__).with_name(
    f"fixture-manifest.{'windows' if os.name == 'nt' else 'posix'}.json"
)


def _manifest(root: Path) -> dict[str, list[int | str]]:
    return {
        path.relative_to(root).as_posix(): [
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        ]
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_generated_fixture_tree_matches_committed_manifest(tmp_path: Path) -> None:
    output = tmp_path / "test-data"
    generate_test_data(output)
    actual = _manifest(output)
    if os.environ.get("TIMED_UPDATE_FIXTURE_MANIFEST") == "1":
        MANIFEST.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not MANIFEST.is_file():
        pytest.skip(f"No fixture manifest is recorded for this platform: {MANIFEST.name}")
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert sorted(actual) == sorted(expected)
    changed = sorted(path for path in actual if actual[path] != expected[path])
    assert not changed, f"Generated fixture bytes changed: {changed}"
