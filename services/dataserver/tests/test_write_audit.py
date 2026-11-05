"""Architecture checks for the data server's only filesystem writes."""

from __future__ import annotations

import ast
from pathlib import Path

import timed_dataserver

PACKAGE = Path(timed_dataserver.__file__).resolve().parent
WRITE_MODULES = frozenset(("cli.py", "qc_annotations.py", "registry.py"))
_WRITE_FLAGS = frozenset(("O_APPEND", "O_CREAT", "O_RDWR", "O_TRUNC", "O_WRONLY"))
_WRITE_FUNCTIONS = {
    "os": frozenset(
        (
            "chmod",
            "chown",
            "ftruncate",
            "link",
            "makedirs",
            "mkdir",
            "remove",
            "removedirs",
            "rename",
            "renames",
            "replace",
            "rmdir",
            "symlink",
            "truncate",
            "unlink",
            "utime",
            "write",
        )
    ),
    "tempfile": frozenset(
        ("NamedTemporaryFile", "TemporaryDirectory", "TemporaryFile", "mkdtemp", "mkstemp")
    ),
}
# Path and file-object methods that write; none collides with a read-only API.
_WRITE_METHODS = frozenset(
    (
        "chmod",
        "hardlink_to",
        "mkdir",
        "rmdir",
        "symlink_to",
        "touch",
        "truncate",
        "write",
        "write_bytes",
        "write_text",
    )
)
# Path.replace, Path.rename and Path.unlink are distinguished from str.replace and
# DatasetRegistry.unlink(dataset_id) by their positional arity.
_ARITY_METHODS = {"rename": 1, "replace": 1, "unlink": 0}


def _mode_writes(node: ast.Call, position: int) -> bool:
    mode: ast.expr | None = None
    if len(node.args) > position:
        mode = node.args[position]
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return False
    if not isinstance(mode, ast.Constant) or not isinstance(mode.value, str):
        return True
    return any(character in mode.value for character in "wax+")


def _call_writes(node: ast.Call) -> str | None:
    function = node.func
    if isinstance(function, ast.Name) and function.id == "open":
        return "open" if _mode_writes(node, 1) else None
    if not isinstance(function, ast.Attribute):
        return None
    owner = function.value.id if isinstance(function.value, ast.Name) else None
    name = function.attr
    if owner == "shutil":
        return f"shutil.{name}"
    if owner in _WRITE_FUNCTIONS and name in _WRITE_FUNCTIONS[owner]:
        return f"{owner}.{name}"
    if owner == "os" and name == "fdopen":
        return "os.fdopen" if _mode_writes(node, 1) else None
    if name == "open" and owner != "os":
        return ".open" if _mode_writes(node, 0) else None
    if name in _WRITE_METHODS:
        return f".{name}"
    if name in _ARITY_METHODS and not node.keywords and len(node.args) == _ARITY_METHODS[name]:
        return f".{name}"
    if name == "unlink" and not node.args:
        return ".unlink"
    return None


def write_primitives(source: str) -> list[tuple[int, str]]:
    """Return the line and kind of every filesystem write in one module."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            kind = _call_writes(node)
            if kind is not None:
                found.append((node.lineno, kind))
        elif isinstance(node, ast.Attribute) and node.attr in _WRITE_FLAGS:
            found.append((node.lineno, f"os.{node.attr}"))
        elif isinstance(node, ast.Name) and node.id in _WRITE_FLAGS:
            found.append((node.lineno, node.id))
    return sorted(found)


def test_write_primitive_scanner_detects_each_kind() -> None:
    source = "\n".join(
        (
            "open(path, 'w')",
            "path.open('ab')",
            "os.fdopen(fd, 'r+')",
            "os.open(path, os.O_WRONLY | os.O_CREAT)",
            "os.replace(source, target)",
            "Path(source).replace(target)",
            "path.unlink(missing_ok=True)",
            "path.write_text('x')",
            "shutil.rmtree(path)",
            "tempfile.NamedTemporaryFile('w')",
            "open(path, mode)",
        )
    )
    assert [kind for _line, kind in write_primitives(source)] == [
        "open",
        ".open",
        "os.fdopen",
        "os.O_CREAT",
        "os.O_WRONLY",
        "os.replace",
        ".replace",
        ".unlink",
        ".write_text",
        "shutil.rmtree",
        "tempfile.NamedTemporaryFile",
        "open",
    ]
    assert write_primitives(
        "\n".join(
            (
                "open(path)",
                "path.open('rb')",
                "os.fdopen(fd, 'r', encoding='utf-8')",
                "os.open(path, os.O_RDONLY)",
                "text.replace('a', 'b')",
                "moment.replace(tzinfo=UTC)",
                "registry.unlink(dataset_id)",
            )
        )
    ) == []


def test_write_primitives_only_in_allowed_modules() -> None:
    modules = sorted(PACKAGE.rglob("*.py"))
    assert modules, PACKAGE
    writers = {
        path.relative_to(PACKAGE).as_posix(): primitives
        for path in modules
        if (primitives := write_primitives(path.read_text(encoding="utf-8")))
    }

    unexpected = {name: found for name, found in writers.items() if name not in WRITE_MODULES}
    assert not unexpected, f"Filesystem writes outside {sorted(WRITE_MODULES)}: {unexpected}"
    # The scanner must still see the known writes, or the check above proves nothing.
    assert set(writers) == WRITE_MODULES
    assert {kind for _line, kind in writers["qc_annotations.py"]} >= {
        "os.O_APPEND",
        "os.O_CREAT",
        "os.O_WRONLY",
        "os.write",
    }
    assert {kind for _line, kind in writers["registry.py"]} >= {
        "os.replace",
        "tempfile.NamedTemporaryFile",
        ".unlink",
    }
    assert ".unlink" in {kind for _line, kind in writers["cli.py"]}
