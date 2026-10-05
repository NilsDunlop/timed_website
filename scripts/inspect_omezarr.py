#!/usr/bin/env python3
"""Inspect OME-Zarr metadata without importing Zarr or reading image chunks.

The script reads only metadata files at paths declared by plate, well, and
multiscales metadata. It never recursively walks a store.

Supports OME-Zarr 0.4 and earlier (Zarr v2, metadata at the top level of
.zattrs) and OME-Zarr 0.5+ (Zarr v3, metadata nested under the "ome" key of
zarr.json attributes).

Also handles "collection" folders: a plain directory (no Zarr metadata of its
own) that holds one Zarr dataset per field, named like ``B02_s1.zarr`` (well
B02, field 1), plus sidecar files such as acquisition_config.json. A field may
be an OME-Zarr image or, as on Arrhenius, a bare Zarr array.

Usage:
    inspect_omezarr.py FOLDER                # every store/collection in FOLDER
    inspect_omezarr.py A.zarr B.zarr ...     # specific stores
    inspect_omezarr.py FOLDER --jsonl        # one JSON line per store, streamed
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

JSON = Dict[str, Any]
GROUP_METADATA_FILES = ("zarr.json", ".zgroup", ".zattrs")
SCHEMA_VERSION = 4
FIELD_NAME = re.compile(r"^(?P<row>[A-Za-z]+)(?P<col>\d+)_s(?P<field>\d+)\.zarr$")
MAX_JSON_SIDECAR_BYTES = 256 * 1024
MAX_TEXT_SIDECAR_BYTES = 20 * 1024
PROGRESS_EVERY = 100


class MetadataError(RuntimeError):
    """Raised when required metadata is missing or structurally invalid."""


def _read_json(path: Path) -> Any:
    """Read one JSON metadata file."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MetadataError(f"Could not read {path}: {exc}") from exc


def _require_mapping(value: Any, description: str) -> JSON:
    if not isinstance(value, dict):
        raise MetadataError(f"{description} must be a JSON object")
    return value


def _normalize_attributes(attributes: JSON) -> JSON:
    """Flatten OME-Zarr 0.5+ attributes, which live under an ``ome`` key.

    The result looks like 0.4-style attributes (``plate``, ``well``,
    ``multiscales`` ... at the top level). The namespace version is kept as
    ``_ome_version`` because 0.5 no longer repeats it inside each object.
    """
    ome = attributes.get("ome")
    if not isinstance(ome, dict):
        return attributes
    merged = {key: value for key, value in attributes.items() if key != "ome"}
    merged.update(ome)
    merged["_ome_namespace"] = True
    if ome.get("version") is not None:
        merged["_ome_version"] = str(ome["version"])
    return merged


def _group_metadata(path: Path) -> Tuple[int, JSON]:
    """Return ``(zarr_format, normalized attributes)`` for a v2 or v3 group."""
    v3_path = path / "zarr.json"
    if v3_path.is_file():
        metadata = _require_mapping(_read_json(v3_path), str(v3_path))
        if metadata.get("node_type") not in (None, "group"):
            raise MetadataError(f"{v3_path} is not a Zarr group")
        attributes = metadata.get("attributes", {})
        return int(metadata.get("zarr_format", 3)), _normalize_attributes(
            _require_mapping(attributes, f"{v3_path}.attributes")
        )

    group_path = path / ".zgroup"
    attrs_path = path / ".zattrs"
    if not group_path.is_file() and not attrs_path.is_file():
        raise MetadataError(f"No Zarr group metadata (zarr.json, .zgroup, .zattrs) at {path}")
    group = (
        _require_mapping(_read_json(group_path), str(group_path)) if group_path.is_file() else {}
    )
    attributes = (
        _require_mapping(_read_json(attrs_path), str(attrs_path)) if attrs_path.is_file() else {}
    )
    return int(group.get("zarr_format", 2)), _normalize_attributes(attributes)


def _is_array(path: Path) -> bool:
    """True if ``path`` is a bare Zarr array (v2 .zarray or v3 node_type array)."""
    if (path / ".zarray").is_file():
        return True
    v3_path = path / "zarr.json"
    if not v3_path.is_file():
        return False
    try:
        metadata = _read_json(v3_path)
    except MetadataError:
        return False
    return isinstance(metadata, dict) and metadata.get("node_type") == "array"


def _has_group_metadata(path: Path) -> bool:
    return any((path / name).is_file() for name in GROUP_METADATA_FILES)


def _metadata_child(root: Path, relative_path: str) -> Path:
    """Resolve a metadata-declared POSIX path without allowing store escape."""
    if not isinstance(relative_path, str) or not relative_path:
        raise MetadataError("Metadata path must be a non-empty string")
    if chr(92) in relative_path:
        raise MetadataError(f"Metadata path contains a backslash: {relative_path}")
    logical = PurePosixPath(relative_path)
    if logical.is_absolute() or ".." in logical.parts:
        raise MetadataError(f"Metadata path escapes the store: {relative_path}")
    candidate = root.joinpath(*[part for part in logical.parts if part not in ("", ".")])
    resolved_root = str(root.resolve())
    resolved_candidate = str(candidate.resolve())
    try:
        common = os.path.commonpath([resolved_root, resolved_candidate])
    except ValueError as exc:
        raise MetadataError(f"Metadata path escapes the store: {relative_path}") from exc
    if common != resolved_root:
        raise MetadataError(f"Metadata path escapes the store: {relative_path}")
    return candidate


def _version_from_attributes(attributes: JSON) -> Optional[str]:
    if attributes.get("_ome_version") is not None:
        return str(attributes["_ome_version"])
    multiscales = attributes.get("multiscales")
    if isinstance(multiscales, list):
        for multiscale in multiscales:
            if isinstance(multiscale, dict) and multiscale.get("version") is not None:
                return str(multiscale["version"])
    for key in ("plate", "well", "omero"):
        value = attributes.get(key)
        if isinstance(value, dict) and value.get("version") is not None:
            return str(value["version"])
    return None


def _store_kind(attributes: JSON) -> Optional[str]:
    if isinstance(attributes.get("plate"), dict):
        return "plate"
    if isinstance(attributes.get("well"), dict):
        return "well"
    if isinstance(attributes.get("multiscales"), list):
        return "image"
    return None


def _looks_like_ome_zarr(attributes: JSON) -> bool:
    return (
        _store_kind(attributes) is not None
        or attributes.get("_ome_namespace") is True
        or any(key.startswith("bioformats2raw") for key in attributes)
    )


def _codec_name(codec: JSON) -> Optional[str]:
    name = codec.get("name") or codec.get("id")
    return str(name) if name is not None else None


def _codec_configuration(codec: JSON) -> JSON:
    configuration = codec.get("configuration", codec.get("config", {}))
    return configuration if isinstance(configuration, dict) else {}


def _sharding_codec(codecs: Sequence[Any]) -> Optional[JSON]:
    for value in codecs:
        if not isinstance(value, dict):
            continue
        name = _codec_name(value)
        if name and name.rsplit("/", 1)[-1] in ("sharding_indexed", "sharding-indexed"):
            return value
    return None


def _array_metadata(path: Path, coordinate_transformations: Any) -> JSON:
    """Read a v2 ``.zarray`` or v3 array ``zarr.json`` metadata document."""
    v3_path = path / "zarr.json"
    if v3_path.is_file():
        metadata = _require_mapping(_read_json(v3_path), str(v3_path))
        if metadata.get("node_type") not in (None, "array"):
            raise MetadataError(f"{v3_path} is not a Zarr array")
        chunk_grid = metadata.get("chunk_grid", {})
        chunk_grid = chunk_grid if isinstance(chunk_grid, dict) else {}
        chunk_configuration = chunk_grid.get("configuration", chunk_grid.get("config", {}))
        chunk_configuration = chunk_configuration if isinstance(chunk_configuration, dict) else {}
        outer_chunk_shape = chunk_configuration.get("chunk_shape")
        codecs = metadata.get("codecs", [])
        codecs = codecs if isinstance(codecs, list) else []
        sharding = _sharding_codec(codecs)
        inner_chunk_shape = None
        if sharding is not None:
            inner_chunk_shape = _codec_configuration(sharding).get("chunk_shape")
        return {
            "zarr_format": int(metadata.get("zarr_format", 3)),
            "shape": metadata.get("shape"),
            "chunks": inner_chunk_shape or outer_chunk_shape,
            "shard_shape": outer_chunk_shape if sharding is not None else None,
            "dtype": metadata.get("data_type", metadata.get("dtype")),
            "compressor": None,
            "codecs": codecs,
            "dimension_names": metadata.get("dimension_names"),
            "fill_value": metadata.get("fill_value"),
            "attributes": metadata.get("attributes") or {},
            "coordinate_transformations": coordinate_transformations or [],
        }

    v2_path = path / ".zarray"
    if not v2_path.is_file():
        raise MetadataError(f"No array metadata at {path}")
    metadata = _require_mapping(_read_json(v2_path), str(v2_path))
    v2_attrs_path = path / ".zattrs"
    v2_attributes = _read_json(v2_attrs_path) if v2_attrs_path.is_file() else {}
    v2_attributes = v2_attributes if isinstance(v2_attributes, dict) else {}
    storage_transformers = metadata.get("storage_transformers", [])
    storage_transformers = storage_transformers if isinstance(storage_transformers, list) else []
    sharding = _sharding_codec(storage_transformers)
    physical_chunks = metadata.get("chunks")
    inner_chunks = _codec_configuration(sharding).get("chunk_shape") if sharding else None
    return {
        "zarr_format": int(metadata.get("zarr_format", 2)),
        "shape": metadata.get("shape"),
        "chunks": inner_chunks or physical_chunks,
        "shard_shape": physical_chunks if sharding is not None else None,
        "dtype": metadata.get("dtype"),
        "compressor": metadata.get("compressor"),
        "codecs": metadata.get("filters", []),
        "dimension_names": v2_attributes.get("_ARRAY_DIMENSIONS"),
        "fill_value": metadata.get("fill_value"),
        "attributes": v2_attributes,
        "coordinate_transformations": coordinate_transformations or [],
    }


def _axis_names(axes: Any) -> List[str]:
    if not isinstance(axes, list):
        return []
    names = []
    for axis in axes:
        if isinstance(axis, str):
            names.append(axis)
        elif isinstance(axis, dict) and axis.get("name") is not None:
            names.append(str(axis["name"]))
    return names


def _omero_summary(attributes: JSON) -> JSON:
    omero = attributes.get("omero", {})
    if not isinstance(omero, dict):
        return {"channels": [], "rdefs": None}
    channels = []
    raw_channels = omero.get("channels", [])
    if isinstance(raw_channels, list):
        for channel in raw_channels:
            if not isinstance(channel, dict):
                continue
            channels.append(
                {
                    "label": channel.get("label"),
                    "color": channel.get("color"),
                    "window": channel.get("window"),
                }
            )
    return {"channels": channels, "rdefs": omero.get("rdefs")}


def _inspect_image(store_root: Path, image_path: str) -> JSON:
    image_root = store_root if image_path in ("", ".") else _metadata_child(store_root, image_path)
    _, attributes = _group_metadata(image_root)
    multiscales = attributes.get("multiscales")
    if not isinstance(multiscales, list) or not multiscales:
        raise MetadataError("Image {} has no multiscales metadata".format(image_path or "."))
    multiscale = _require_mapping(multiscales[0], "first multiscales entry")
    datasets = multiscale.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise MetadataError("Image {} has no multiscale datasets".format(image_path or "."))

    levels = []
    for dataset in datasets:
        dataset = _require_mapping(dataset, "multiscales dataset")
        dataset_path = dataset.get("path")
        if not isinstance(dataset_path, str) or not dataset_path:
            raise MetadataError("Multiscales dataset has no path")
        array_root = _metadata_child(image_root, dataset_path)
        level = _array_metadata(
            array_root,
            dataset.get("coordinateTransformations", dataset.get("coordinate_transformations", [])),
        )
        level["path"] = dataset_path
        levels.append(level)

    version = multiscale.get("version")
    if version is None:
        version = attributes.get("_ome_version")
    axes_metadata = multiscale.get("axes", [])
    first_level = levels[0]
    return {
        "path": image_path or ".",
        "ome_zarr_version": str(version) if version is not None else None,
        "multiscales_name": multiscale.get("name"),
        "axes": _axis_names(axes_metadata),
        "axes_metadata": axes_metadata,
        "coordinate_transformations": multiscale.get(
            "coordinateTransformations", multiscale.get("coordinate_transformations", [])
        ),
        "shape": first_level.get("shape"),
        "dtype": first_level.get("dtype"),
        "levels": levels,
        "omero": _omero_summary(attributes),
        "labels_group": _has_group_metadata(image_root / "labels"),
    }


def _named_values(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        if isinstance(value, dict) and value.get("name") is not None:
            result.append(str(value["name"]))
        elif isinstance(value, (str, int)):
            result.append(str(value))
    return result


def _well_path(well: Any) -> Optional[str]:
    if isinstance(well, str):
        return well
    if isinstance(well, dict) and isinstance(well.get("path"), str):
        return well["path"]
    return None


def _inspect_well(store_root: Path, well_path: str) -> Tuple[JSON, Optional[str]]:
    well_root = _metadata_child(store_root, well_path)
    _, attributes = _group_metadata(well_root)
    well = attributes.get("well")
    if not isinstance(well, dict):
        raise MetadataError(f"Well {well_path} has no well metadata")
    raw_images = well.get("images", [])
    raw_images = raw_images if isinstance(raw_images, list) else []
    fields = []
    first_image_path = None
    for image in raw_images:
        if not isinstance(image, dict) or not isinstance(image.get("path"), str):
            continue
        field_path = image["path"]
        fields.append({"path": field_path, "acquisition": image.get("acquisition")})
        if first_image_path is None:
            first_image_path = str(PurePosixPath(well_path) / PurePosixPath(field_path))
    return (
        {
            "path": well_path,
            "field_count": len(fields),
            "fields": fields,
            "attribute_keys": sorted(k for k in attributes if not k.startswith("_ome_")),
        },
        first_image_path,
    )


def _inspect_plate(store_root: Path, attributes: JSON) -> Tuple[JSON, Optional[JSON]]:
    plate = _require_mapping(attributes.get("plate"), "plate metadata")
    raw_wells = plate.get("wells", [])
    raw_wells = raw_wells if isinstance(raw_wells, list) else []
    well_paths = [path for path in (_well_path(well) for well in raw_wells) if path]
    summary = {
        "name": plate.get("name"),
        "rows": _named_values(plate.get("rows")),
        "columns": _named_values(plate.get("columns")),
        "acquisitions": plate.get("acquisitions", []),
        "field_count": plate.get("field_count"),
        "well_count": len(well_paths),
        "sample_well_paths": well_paths[:5],
        "first_well": None,
    }
    image = None
    if well_paths:
        first_well, first_image_path = _inspect_well(store_root, well_paths[0])
        summary["first_well"] = first_well
        if first_image_path is not None:
            image = _inspect_image(store_root, first_image_path)
    return summary, image


def _bioformats2raw_summary(store_root: Path, attributes: JSON) -> JSON:
    layout = attributes.get("bioformats2raw.layout")
    if layout is None and isinstance(attributes.get("bioformats2raw"), dict):
        layout = attributes["bioformats2raw"].get("layout")
    return {
        "present": layout is not None,
        "layout": layout,
        "ome_folder": _has_group_metadata(store_root / "OME"),
    }


def _inspect_store(store_root: Path) -> JSON:
    record = {
        "name": store_root.name,
        "path": str(store_root),
        "zarr_format": None,
        "ome_zarr_version": None,
        "ome_namespace": False,
        "kind": None,
        "root_attribute_keys": [],
        "bioformats2raw": {"present": False, "layout": None, "ome_folder": False},
        "plate": None,
        "image": None,
        "array": None,
        "errors": [],
    }
    try:
        if _is_array(store_root):
            array = _array_metadata(store_root, None)
            array.pop("coordinate_transformations", None)
            record["kind"] = "array"
            record["zarr_format"] = array["zarr_format"]
            record["array"] = array
            record["root_attribute_keys"] = sorted(array.get("attributes") or {})
            return record
        zarr_format, attributes = _group_metadata(store_root)
        record["zarr_format"] = zarr_format
        record["ome_namespace"] = attributes.get("_ome_namespace") is True
        record["root_attribute_keys"] = sorted(k for k in attributes if not k.startswith("_ome_"))
        record["bioformats2raw"] = _bioformats2raw_summary(store_root, attributes)
        kind = _store_kind(attributes)
        record["kind"] = kind or (
            "bioformats2raw" if record["bioformats2raw"]["present"] else "unknown"
        )
        record["ome_zarr_version"] = _version_from_attributes(attributes)

        if kind == "plate":
            plate, image = _inspect_plate(store_root, attributes)
            record["plate"] = plate
            record["image"] = image
        elif kind == "image":
            record["image"] = _inspect_image(store_root, ".")
        elif kind == "well":
            raise MetadataError("A well group is not a top-level OME-Zarr dataset")
        elif record["bioformats2raw"]["present"] and _has_group_metadata(store_root / "0"):
            record["image"] = _inspect_image(store_root, "0")
        else:
            raise MetadataError("No plate, well, or multiscales metadata at the store root")

        image = record.get("image")
        if record["ome_zarr_version"] is None and isinstance(image, dict):
            record["ome_zarr_version"] = image.get("ome_zarr_version")
    except (MetadataError, OSError, TypeError, ValueError) as exc:
        record["errors"].append(str(exc))
    return record


def _child_zarr_dirs(path: Path) -> List[Path]:
    return sorted(
        (c for c in path.iterdir() if c.is_dir() and c.suffix.lower() == ".zarr"),
        key=lambda p: p.name,
    )


def _is_collection(path: Path) -> bool:
    """A plain folder (no Zarr metadata) that contains *.zarr stores."""
    if _has_group_metadata(path):
        return False
    try:
        return any(c.is_dir() and c.suffix.lower() == ".zarr" for c in path.iterdir())
    except OSError:
        return False


def _is_store(path: Path) -> bool:
    """True if ``path`` should be inspected as a single OME-Zarr store."""
    if not _has_group_metadata(path):
        # An empty/broken *.zarr folder is still reported, with an error.
        return path.suffix.lower() == ".zarr"
    try:
        _, attributes = _group_metadata(path)
    except MetadataError:
        return True  # malformed group: inspect so the error gets reported
    return _looks_like_ome_zarr(attributes)


def _read_sidecar_json(path: Path) -> Any:
    size = path.stat().st_size
    if size > MAX_JSON_SIDECAR_BYTES:
        return {"_omitted": f"{size} bytes, larger than {MAX_JSON_SIDECAR_BYTES}"}
    try:
        return _read_json(path)
    except MetadataError as exc:
        return {"_error": str(exc)}


def _read_sidecar_text(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(MAX_TEXT_SIDECAR_BYTES + 1)
    except OSError as exc:
        return f"<could not read: {exc}>"
    if len(text) > MAX_TEXT_SIDECAR_BYTES:
        text = text[:MAX_TEXT_SIDECAR_BYTES] + "\n<truncated>"
    return text


def _image_signature(record: JSON) -> JSON:
    """The properties that should normally be identical across fields of a plate."""
    array = record.get("array")
    if isinstance(array, dict):
        return {
            "kind": "array",
            "zarr_format": array.get("zarr_format"),
            "dimension_names": array.get("dimension_names"),
            "shape": array.get("shape"),
            "dtype": array.get("dtype"),
            "chunks": array.get("chunks"),
            "shard_shape": array.get("shard_shape"),
            "codecs": array.get("codecs") or array.get("compressor"),
            "attribute_keys": sorted(array.get("attributes") or {}),
        }
    image = record.get("image") or {}
    levels = image.get("levels") or []
    level0 = levels[0] if levels else {}
    scale = None
    for transform in level0.get("coordinate_transformations") or []:
        if isinstance(transform, dict) and transform.get("type") == "scale":
            scale = transform.get("scale")
    channels = (image.get("omero") or {}).get("channels") or []
    return {
        "kind": record.get("kind"),
        "zarr_format": record.get("zarr_format"),
        "ome_zarr_version": record.get("ome_zarr_version"),
        "axes": image.get("axes"),
        "shape": image.get("shape"),
        "dtype": image.get("dtype"),
        "chunks": level0.get("chunks"),
        "shard_shape": level0.get("shard_shape"),
        "level_count": len(levels),
        "level0_scale": scale,
        "channel_labels": [c.get("label") for c in channels],
    }


def _sort_rows(rows: Sequence[str]) -> List[str]:
    return sorted(set(rows), key=lambda r: (len(r), r))


def _inspect_collection(path: Path, all_fields: bool, progress: bool) -> JSON:
    record: JSON = {
        "name": path.name,
        "path": str(path),
        "kind": "collection",
        "field_store_count": 0,
        "well_count": 0,
        "rows": [],
        "columns": [],
        "fields_per_well": {},
        "unparsed_store_names": [],
        "signatures": [],
        "field_error_count": 0,
        "field_errors": [],
        "first_field": None,
        "fields": None,
        "files": [],
        "acquisition_config": None,
        "conversion_notes": None,
        "errors": [],
    }
    try:
        children = _child_zarr_dirs(path)
        for entry in sorted(path.iterdir(), key=lambda p: p.name):
            if entry.is_file():
                record["files"].append({"name": entry.name, "bytes": entry.stat().st_size})
        if (path / "acquisition_config.json").is_file():
            record["acquisition_config"] = _read_sidecar_json(path / "acquisition_config.json")
        if (path / "CONVERSION.md").is_file():
            record["conversion_notes"] = _read_sidecar_text(path / "CONVERSION.md")
    except OSError as exc:
        record["errors"].append(str(exc))
        return record

    wells: Dict[str, List[int]] = {}
    rows: List[str] = []
    columns: List[int] = []
    signatures: Dict[str, JSON] = {}
    fields: List[JSON] = []
    first_ok: Optional[JSON] = None
    first_any: Optional[JSON] = None

    for index, child in enumerate(children, start=1):
        field_record = _inspect_store(child)
        match = FIELD_NAME.match(child.name)
        if match:
            row, col, field = match["row"].upper(), int(match["col"]), int(match["field"])
            field_record["well"] = f"{row}{col:02d}"
            field_record["field"] = field
            wells.setdefault(field_record["well"], []).append(field)
            rows.append(row)
            columns.append(col)
        elif len(record["unparsed_store_names"]) < 20:
            record["unparsed_store_names"].append(child.name)

        if first_any is None:
            first_any = field_record
        if field_record["errors"]:
            record["field_error_count"] += 1
            if len(record["field_errors"]) < 20:
                record["field_errors"].append(
                    {"name": child.name, "errors": field_record["errors"]}
                )
        else:
            if first_ok is None:
                first_ok = field_record
            signature = _image_signature(field_record)
            key = json.dumps(signature, sort_keys=True)
            entry = signatures.setdefault(key, {"count": 0, "examples": [], "signature": signature})
            entry["count"] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append(child.name)
        if all_fields:
            fields.append(field_record)
        if progress and index % PROGRESS_EVERY == 0:
            sys.stderr.write(f"    {path.name}: {index}/{len(children)} fields\n")
            sys.stderr.flush()

    record["field_store_count"] = len(children)
    record["well_count"] = len(wells)
    record["rows"] = _sort_rows(rows)
    record["columns"] = sorted(set(columns))
    record["fields_per_well"] = {
        str(n): count for n, count in sorted(Counter(len(v) for v in wells.values()).items())
    }
    record["signatures"] = sorted(signatures.values(), key=lambda s: -s["count"])
    record["first_field"] = first_ok or first_any
    record["fields"] = fields if all_fields else None
    return record


def plan(paths: Sequence[Path]) -> Tuple[List[Path], List[JSON], List[str]]:
    """Return ``(items to inspect, skipped entries, input errors)``.

    Each input path is a store, a collection, or a folder whose direct
    children are checked. Plain files (e.g. CONVERSION.md) are ignored.
    """
    items: List[Path] = []
    skipped: List[JSON] = []
    errors: List[str] = []
    for raw in paths:
        path = raw.expanduser()
        if not path.is_dir():
            errors.append(f"Not a directory: {path}")
            continue
        if path.suffix.lower() == ".zarr" and (_is_collection(path) or _is_store(path)):
            items.append(path)
            continue
        if _has_group_metadata(path) and _is_store(path):
            items.append(path)
            continue
        for child in sorted(path.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            if _is_collection(child) or _is_store(child):
                items.append(child)
            elif _has_group_metadata(child):
                skipped.append({"path": str(child), "reason": "Zarr group without OME metadata"})
            else:
                skipped.append(
                    {"path": str(child), "reason": "no Zarr metadata and no *.zarr inside"}
                )
    return items, skipped, errors


def _has_errors(record: JSON) -> bool:
    return bool(record.get("errors")) or bool(record.get("field_error_count"))


def _status_line(record: JSON) -> str:
    if record.get("kind") == "collection":
        status = (
            f"collection, {record['field_store_count']} field stores in "
            f"{record['well_count']} wells, {len(record['signatures'])} image signature(s), "
            f"{record['field_error_count']} field error(s)"
        )
        if record["errors"]:
            status += " - ERROR: " + "; ".join(record["errors"])
        return status
    if record["errors"]:
        return f"{record['kind']} - ERROR: " + "; ".join(record["errors"])
    return f"{record['kind']} - ok"


def iter_records(items: Sequence[Path], all_fields: bool, progress: bool = True) -> Iterator[JSON]:
    """Inspect items one at a time, yielding each record as soon as it is done."""
    total = len(items)
    for index, item in enumerate(items, start=1):
        if _is_collection(item):
            record = _inspect_collection(item, all_fields, progress)
        else:
            record = _inspect_store(item)
        if progress:
            sys.stderr.write(f"[{index}/{total}] {item.name}: {_status_line(record)}\n")
            sys.stderr.flush()
        yield record


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect OME-Zarr metadata without installing third-party packages."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Folder(s) containing OME-Zarr stores or collections, or the items themselves",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Stream one JSON object per line (one per store/collection)",
    )
    parser.add_argument(
        "--all-fields",
        action="store_true",
        help="Include the full record of every field store in collections (large output)",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Do not print progress to stderr"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    items, skipped, errors = plan(args.paths)
    progress = not args.quiet

    if progress:
        sys.stderr.write(f"Found {len(items)} item(s), skipped {len(skipped)} folder(s)\n")
        for entry in skipped:
            sys.stderr.write(f"  skipped {entry['path']}: {entry['reason']}\n")

    any_errors = False
    if args.jsonl:
        for error in errors:
            print(json.dumps({"type": "error", "message": error}), flush=True)
        for entry in skipped:
            print(json.dumps({"type": "skipped", **entry}), flush=True)
        for record in iter_records(items, args.all_fields, progress):
            any_errors = any_errors or _has_errors(record)
            print(json.dumps({"type": record.get("kind") or "store", **record}), flush=True)
    else:
        records = []
        for record in iter_records(items, args.all_fields, progress):
            any_errors = any_errors or _has_errors(record)
            records.append(record)
        inventory = {
            "schema_version": SCHEMA_VERSION,
            "inputs": [str(p.expanduser()) for p in args.paths],
            "items": records,
            "skipped": skipped,
            "errors": errors,
        }
        json.dump(inventory, sys.stdout, indent=2)
        sys.stdout.write("\n")

    return 1 if errors or any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
