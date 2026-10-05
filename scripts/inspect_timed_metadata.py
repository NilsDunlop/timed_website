#!/usr/bin/env python3
"""Inspect TIMED experiment metadata without reading microscopy pixels.

The report is intended to guide metadata-aware plate UI work. It summarizes:

* acquisition JSON structure and values which differ between files;
* CSV headers, categorical values, and likely plate/well/treatment columns;
* Parquet schema, row groups, column statistics, and representative rows; and
* metadata-building Python scripts by hash, imports, and public definitions.

Credential-like JSON keys and table columns are redacted. Table reads are
bounded by --max-table-rows. Parquet support is optional so the script can
still inspect JSON and CSV with the Python standard library.

Usage:
    python inspect_timed_metadata.py /path/to/metadata
    uv run --with pyarrow python scripts/inspect_timed_metadata.py /path/to/metadata
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_REPRESENTATIVE_JSON_BYTES = 64 * 1024
MAX_REPRESENTATIVE_LIST_ITEMS = 100
MAX_STRING_LENGTH = 500
SENSITIVE_TERMS = ("password", "passwd", "secret", "token", "credential", "api_key", "apikey")

ROLE_TERMS = {
    "plate": ("plate", "barcode"),
    "well": ("well",),
    "row": ("row",),
    "column": ("column", "col"),
    "site": ("site", "field", "position"),
    "compound": ("compound", "drug", "treatment", "perturbation", "perturbagen", "moa"),
    "concentration": ("concentration", "dose", "dosage"),
    "control": ("control", "vehicle", "dmso"),
    "replicate": ("replicate", "rep"),
    "acquisition": ("acquisition", "acq"),
    "time": ("timepoint", "time_point", "timestamp", "datetime"),
}


class InspectionError(RuntimeError):
    """Raised when the requested metadata cannot be inspected."""


class ParquetDependencyError(InspectionError):
    """Raised when a Parquet file is present but PyArrow is unavailable."""


def _is_sensitive(name: str) -> bool:
    lowered = name.lower()
    return any(term in lowered for term in SENSITIVE_TERMS)


def _safe_value(value: Any, path: str = "") -> Any:
    if _is_sensitive(path):
        return "<redacted>"
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
            return value[:MAX_STRING_LENGTH] + "…"
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "hex_prefix": value[:16].hex(),
        }
    if isinstance(value, dict):
        return {str(key): _safe_value(item, path + "." + str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        result = [_safe_value(item, path + "[]") for item in value[:MAX_REPRESENTATIVE_LIST_ITEMS]]
        if len(value) > MAX_REPRESENTATIVE_LIST_ITEMS:
            result.append({"truncated_items": len(value) - MAX_REPRESENTATIVE_LIST_ITEMS})
        return result
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _safe_value(item(), path)
        except (TypeError, ValueError):
            pass
    return str(value)


def _value_key(value: Any) -> str:
    return json.dumps(_safe_value(value), sort_keys=True, ensure_ascii=False)


def _decode_value_key(value: str) -> Any:
    return json.loads(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _discover(
    root: Path,
    excluded_paths: Sequence[Path] = (),
) -> dict[str, list[Path]]:
    excluded = {path.expanduser().resolve() for path in excluded_paths}
    found: dict[str, list[Path]] = {
        "json": [],
        "csv": [],
        "parquet": [],
        "python": [],
        "other": [],
    }
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name for name in directories if not (current_path / name).is_symlink()
        )
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink() or path.resolve() in excluded:
                continue
            kind = {
                ".json": "json",
                ".csv": "csv",
                ".parquet": "parquet",
                ".py": "python",
            }.get(path.suffix.lower(), "other")
            found[kind].append(path)
    return found


def _other_file_summary(paths: Sequence[Path], root: Path) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for path in paths:
        suffix = path.suffix.lower() or "<none>"
        entry = groups.setdefault(
            suffix,
            {"extension": suffix, "count": 0, "total_bytes": 0, "examples": []},
        )
        entry["count"] += 1
        entry["total_bytes"] += path.stat().st_size
        if len(entry["examples"]) < 5:
            entry["examples"].append(_relative(path, root))
    return sorted(groups.values(), key=lambda item: item["extension"])


def _walk_json(
    value: Any,
    path: str,
    source: str,
    fields: dict[str, dict[str, Any]],
    containers: dict[str, dict[str, Any]],
    structural_paths: set[str],
) -> None:
    if isinstance(value, dict):
        structural_paths.add("object:" + path)
        container = containers.setdefault(
            path,
            {"path": path, "kind": "object", "files": set(), "lengths": Counter()},
        )
        container["files"].add(source)
        container["lengths"][len(value)] += 1
        for key in sorted(value):
            _walk_json(
                value[key],
                path + "." + str(key),
                source,
                fields,
                containers,
                structural_paths,
            )
        return
    if isinstance(value, list):
        structural_paths.add("array:" + path)
        container = containers.setdefault(
            path,
            {"path": path, "kind": "array", "files": set(), "lengths": Counter()},
        )
        container["files"].add(source)
        container["lengths"][len(value)] += 1
        for item in value:
            _walk_json(
                item,
                path + "[]",
                source,
                fields,
                containers,
                structural_paths,
            )
        return

    structural_paths.add("value:" + path)
    field = fields.setdefault(
        path,
        {
            "path": path,
            "occurrences": 0,
            "files": set(),
            "types": Counter(),
            "values": Counter(),
            "values_by_file": defaultdict(Counter),
        },
    )
    safe = "<redacted>" if _is_sensitive(path) else _safe_value(value, path)
    key = _value_key(safe)
    field["occurrences"] += 1
    field["files"].add(source)
    field["types"][type(value).__name__] += 1
    field["values"][key] += 1
    field["values_by_file"][source][key] += 1


def _limited_values(counter: Counter[str], maximum: int) -> list[dict[str, Any]]:
    values = []
    for key, count in counter.most_common(maximum):
        values.append({"value": _decode_value_key(key), "count": count})
    return values


def _inspect_json_files(
    paths: Sequence[Path],
    root: Path,
    maximum_values: int,
) -> tuple[dict[str, Any], list[str]]:
    documents = []
    errors = []
    fields: dict[str, dict[str, Any]] = {}
    containers: dict[str, dict[str, Any]] = {}
    signatures: dict[str, dict[str, Any]] = {}

    for path in paths:
        source = _relative(path, root)
        try:
            with path.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{source}: {exc}")
            continue

        structural_paths: set[str] = set()
        _walk_json(document, "$", source, fields, containers, structural_paths)
        signature_text = "\n".join(sorted(structural_paths))
        signature = hashlib.sha256(signature_text.encode()).hexdigest()[:16]
        group = signatures.setdefault(
            signature,
            {
                "signature": signature,
                "files": [],
                "representative_file": source,
                "representative": None,
            },
        )
        group["files"].append(source)
        if path.stat().st_size <= MAX_REPRESENTATIVE_JSON_BYTES and group["representative"] is None:
            group["representative"] = _safe_value(document, "$")
        documents.append(
            {
                "path": source,
                "bytes": path.stat().st_size,
                "top_level_type": type(document).__name__,
                "top_level_keys": sorted(document) if isinstance(document, dict) else None,
                "structural_signature": signature,
            }
        )

    field_records = []
    varying_fields = []
    document_count = len(documents)
    for path, field in sorted(fields.items()):
        record = {
            "path": path,
            "occurrences": field["occurrences"],
            "file_count": len(field["files"]),
            "types": dict(sorted(field["types"].items())),
            "distinct_value_count": len(field["values"]),
            "top_values": _limited_values(field["values"], maximum_values),
        }
        field_records.append(record)

        per_file_keys = {
            source: tuple(sorted(values)) for source, values in field["values_by_file"].items()
        }
        varies = len(set(per_file_keys.values())) > 1 or len(per_file_keys) < document_count
        if varies:
            varying_fields.append(
                {
                    "path": path,
                    "values_by_file": {
                        source: _limited_values(values, maximum_values)
                        for source, values in sorted(field["values_by_file"].items())
                    },
                }
            )

    container_records = []
    for _, container in sorted(containers.items()):
        container_records.append(
            {
                "path": container["path"],
                "kind": container["kind"],
                "file_count": len(container["files"]),
                "length_counts": [
                    {"length": length, "count": count}
                    for length, count in sorted(container["lengths"].items())
                ],
            }
        )

    return (
        {
            "file_count": len(paths),
            "documents": documents,
            "structural_groups": sorted(signatures.values(), key=lambda item: item["signature"]),
            "containers": container_records,
            "fields": field_records,
            "varying_fields": varying_fields,
        },
        errors,
    )


def _normalized_name(name: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _candidate_roles(columns: Iterable[str]) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for column in columns:
        normalized = _normalized_name(column)
        tokens = set(normalized.split("_"))
        for role, terms in ROLE_TERMS.items():
            if any(term in tokens for term in terms):
                roles.setdefault(role, []).append(column)
    return roles


def _summarize_values(
    name: str,
    values: Sequence[Any],
    maximum_values: int,
    complete: bool,
) -> dict[str, Any]:
    if _is_sensitive(name):
        return {
            "name": name,
            "redacted": True,
            "sample_count": len(values),
        }
    null_count = sum(value is None or value == "" for value in values)
    non_null = [value for value in values if value is not None and value != ""]
    counts = Counter(_value_key(value) for value in non_null)
    record: dict[str, Any] = {
        "name": name,
        "sample_count": len(values),
        "null_count": null_count,
        "distinct_value_count" if complete else "sample_distinct_value_count": len(counts),
        "top_values": _limited_values(counts, maximum_values),
    }
    numeric = [
        value
        for value in non_null
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    ]
    if numeric and len(numeric) == len(non_null):
        record["minimum"] = _safe_value(min(numeric), name)
        record["maximum"] = _safe_value(max(numeric), name)
    return record


def _inspect_csv(
    path: Path,
    root: Path,
    maximum_rows: int,
    maximum_values: int,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": _relative(path, root),
        "bytes": path.stat().st_size,
        "columns": [],
        "candidate_roles": {},
        "sample_rows": [],
        "errors": [],
    }
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(8192)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(handle, dialect=dialect)
            names = reader.fieldnames or []
            values: dict[str, list[Any]] = {name: [] for name in names}
            rows = []
            for index, row in enumerate(reader):
                if index >= maximum_rows:
                    record["truncated"] = True
                    break
                for name in names:
                    values[name].append(row.get(name))
                if len(rows) < 5:
                    rows.append(
                        {
                            name: (
                                "<redacted>"
                                if _is_sensitive(name)
                                else _safe_value(row.get(name), name)
                            )
                            for name in names
                        }
                    )
            record["sample_row_count"] = max((len(items) for items in values.values()), default=0)
            record["delimiter"] = dialect.delimiter
            record["candidate_roles"] = _candidate_roles(names)
            record["columns"] = [
                _summarize_values(
                    name,
                    values[name],
                    maximum_values,
                    complete=not record.get("truncated", False),
                )
                for name in names
            ]
            record["sample_rows"] = rows
    except (OSError, UnicodeError, csv.Error) as exc:
        record["errors"].append(str(exc))
    return record


def _load_pyarrow() -> tuple[Any, Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ParquetDependencyError(
            "PyArrow is required for Parquet inspection. Run with "
            "uv run --with pyarrow python scripts/inspect_timed_metadata.py ...."
        ) from exc
    return pa, pc, pq


def _decode_metadata(metadata: Any, path: str) -> dict[str, Any]:
    result = {}
    for raw_key, raw_value in (metadata or {}).items():
        key = raw_key.decode("utf-8", errors="replace")
        if key == "ARROW:schema":
            result[key] = f"<omitted base64 schema: {len(raw_value)} bytes>"
        elif _is_sensitive(key):
            result[key] = "<redacted>"
        else:
            result[key] = _safe_value(
                raw_value.decode("utf-8", errors="replace"),
                path + "." + key,
            )
    return result


def _inspect_parquet(
    path: Path,
    root: Path,
    maximum_rows: int,
    maximum_values: int,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": _relative(path, root),
        "bytes": path.stat().st_size,
        "errors": [],
    }
    try:
        pa, _, pq = _load_pyarrow()
        parquet = pq.ParquetFile(path)
        metadata = parquet.metadata
        schema = parquet.schema_arrow
        record.update(
            {
                "row_count": metadata.num_rows,
                "row_group_count": metadata.num_row_groups,
                "created_by": metadata.created_by,
                "schema_metadata": _decode_metadata(schema.metadata, record["path"]),
                "schema": [
                    {
                        "name": field.name,
                        "type": str(field.type),
                        "nullable": field.nullable,
                        "metadata": _decode_metadata(field.metadata, field.name),
                    }
                    for field in schema
                ],
                "candidate_roles": _candidate_roles(schema.names),
            }
        )

        if metadata.num_row_groups:
            first_group = metadata.row_group(0)
            record["first_row_group"] = {
                "rows": first_group.num_rows,
                "total_byte_size": first_group.total_byte_size,
                "columns": [
                    {
                        "name": first_group.column(index).path_in_schema,
                        "physical_type": first_group.column(index).physical_type,
                        "compression": first_group.column(index).compression,
                        "encodings": list(first_group.column(index).encodings),
                    }
                    for index in range(first_group.num_columns)
                ],
            }

        row_groups = []
        selected_rows = 0
        for index in range(metadata.num_row_groups):
            if selected_rows >= maximum_rows:
                break
            row_groups.append(index)
            selected_rows += metadata.row_group(index).num_rows
        table = (
            parquet.read_row_groups(row_groups)
            if row_groups
            else pa.Table.from_batches([], schema=schema)
        )
        table = table.slice(0, maximum_rows)
        complete = table.num_rows == metadata.num_rows
        record["sample_row_count"] = table.num_rows
        record["sample_complete"] = complete
        record["columns"] = [
            {
                **_summarize_values(
                    name,
                    table.column(name).to_pylist(),
                    maximum_values,
                    complete,
                ),
                "arrow_type": str(schema.field(name).type),
            }
            for name in schema.names
        ]

        display_columns = []
        for names in record["candidate_roles"].values():
            for name in names:
                if name not in display_columns:
                    display_columns.append(name)
        for name in schema.names:
            if len(display_columns) >= 16:
                break
            if name not in display_columns:
                display_columns.append(name)
        rows = table.select(display_columns).slice(0, 5).to_pylist()
        record["sample_rows"] = [_safe_value(row, "$row") for row in rows]
    except (
        InspectionError,
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        record["errors"].append(str(exc))
    return record


def _inspect_python(path: Path, root: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": _relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "imports": [],
        "functions": [],
        "classes": [],
        "errors": [],
    }
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    record["functions"].append(node.name)
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                record["classes"].append(node.name)
        record["imports"] = sorted(imports)
    except (OSError, UnicodeError, SyntaxError) as exc:
        record["errors"].append(str(exc))
    return record


def inspect_metadata(
    root: Path,
    maximum_rows: int = 200_000,
    maximum_values: int = 20,
    excluded_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise InspectionError(f"Not a metadata directory: {root}")

    files = _discover(root, excluded_paths)
    json_report, json_errors = _inspect_json_files(files["json"], root, maximum_values)
    csv_reports = [_inspect_csv(path, root, maximum_rows, maximum_values) for path in files["csv"]]
    parquet_reports = [
        _inspect_parquet(path, root, maximum_rows, maximum_values) for path in files["parquet"]
    ]
    python_reports = [_inspect_python(path, root) for path in files["python"]]

    errors = list(json_errors)
    for report in [*csv_reports, *parquet_reports, *python_reports]:
        errors.extend(f"{report['path']}: {message}" for message in report["errors"])

    return {
        "schema_version": SCHEMA_VERSION,
        "metadata_root": str(root),
        "limits": {
            "max_table_rows": maximum_rows,
            "max_values": maximum_values,
        },
        "file_counts": {kind: len(paths) for kind, paths in files.items()},
        "other_files": _other_file_summary(files["other"], root),
        "json": json_report,
        "csv": csv_reports,
        "parquet": parquet_reports,
        "python_builders": python_reports,
        "errors": errors,
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect TIMED acquisition and plate metadata without reading image data."
    )
    parser.add_argument("metadata_root", type=Path)
    parser.add_argument(
        "--max-table-rows",
        type=_positive_int,
        default=200_000,
        help="Maximum rows sampled from each CSV or Parquet file (default: 200000)",
    )
    parser.add_argument(
        "--max-values",
        type=_positive_int,
        default=20,
        help="Maximum top values included per field or column (default: 20)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Do not print the inspection summary to stderr",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the JSON report to this file instead of standard output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = inspect_metadata(
            args.metadata_root,
            maximum_rows=args.max_table_rows,
            maximum_values=args.max_values,
            excluded_paths=[args.output] if args.output else (),
        )
    except InspectionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        try:
            args.output.expanduser().write_text(serialized, encoding="utf-8")
        except OSError as exc:
            print(f"Could not write {args.output}: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(serialized)
    if not args.quiet:
        counts = report["file_counts"]
        print(
            "Inspected "
            f"{counts['json']} JSON, {counts['csv']} CSV, "
            f"{counts['parquet']} Parquet, and {counts['python']} Python file(s); "
            f"{len(report['errors'])} error(s).",
            file=sys.stderr,
        )
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
