"""Pure response shaping for validated QC export snapshots.

This module has no filesystem or HTTP dependencies.  It converts one immutable
export snapshot plus already-mapped Phase 1 well metadata into the public core
or blinded endpoint contracts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import pyarrow as pa
import pyarrow.compute as pc

from .qc_export import QcExportSnapshot

type JsonScalar = str | int | float | bool | None
type JsonObject = dict[str, object]
type WellKey = tuple[str, int, str]
type WellMetadata = Mapping[WellKey, Mapping[str, JsonScalar]]

_DISPLAYED_STATUSES = frozenset(("", "registered", "kept", "pending_review"))
_BLIND_SITE_NAVIGATION = frozenset(("experiment", "plate", "well", "site"))
_BLIND_REVIEW_COLUMNS = (
    "entry_id",
    "review_group",
    "scope",
    "plate",
    "well",
    "site",
    "channel",
)
_BLIND_DISPLAY_OMISSIONS = frozenset(("plate_barcode",))


class QcPayloadError(ValueError):
    """Raised when a validated snapshot cannot form an endpoint payload."""


class QcFieldNotFoundError(QcPayloadError):
    """Raised when a requested plate/well/site key is absent."""


def _require_core(snapshot: QcExportSnapshot) -> None:
    if not snapshot.core_ready:
        raise QcPayloadError("QC export does not satisfy the core response contract")


def _envelope(snapshot: QcExportSnapshot, *, blind: bool) -> JsonObject:
    return {
        "export_id": snapshot.export_id,
        "registry_hash": snapshot.registry_hash,
        "blinded": blind,
    }


def _scalar(value: object) -> JsonScalar:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    raise QcPayloadError(f"QC value has unsupported JSON type {type(value).__name__}")


def _record(row: Mapping[str, object]) -> dict[str, JsonScalar]:
    return {str(key): _scalar(value) for key, value in row.items()}


def _column(values: pa.ChunkedArray) -> list[JsonScalar]:
    return [_scalar(value) for value in values.to_pylist()]


def _records(table: pa.Table) -> list[dict[str, JsonScalar]]:
    return [_record(row) for row in table.to_pylist()]


def annotation_record_payload(
    record: Mapping[str, object],
    *,
    blind: bool,
) -> dict[str, JsonScalar]:
    """Project one stored annotation record into its response mode."""
    payload = _record(record)
    if blind:
        payload.pop("barcode", None)
    return payload


def _applicable_dictionary_rows(
    snapshot: QcExportSnapshot,
    *,
    blind: bool,
    filename: str | None = None,
) -> list[dict[str, JsonScalar]]:
    rows: list[dict[str, JsonScalar]] = []
    for source in snapshot.dictionary_rows(filename=filename, blind=blind):
        status = "" if source.get("status") is None else str(source.get("status"))
        layer = "" if source.get("layer") is None else str(source.get("layer"))
        if status not in _DISPLAYED_STATUSES or layer == "retired":
            continue
        if blind:
            source_file = str(source.get("file") or "")
            column = str(source.get("column") or "")
            if (
                source_file == "site_metrics.csv"
                and layer == "key"
                and column not in _BLIND_SITE_NAVIGATION
            ):
                continue
            if source_file == "review_list.csv" and column not in _BLIND_REVIEW_COLUMNS:
                continue
        row = _record(source)
        if blind:
            row.pop("markers", None)
        rows.append(row)
    return rows


def dictionary_payload(snapshot: QcExportSnapshot, *, blind: bool = False) -> JsonObject:
    """Return applicable, dictionary-driven UI definitions."""
    _require_core(snapshot)
    return {
        **_envelope(snapshot, blind=blind),
        "rows": _applicable_dictionary_rows(snapshot, blind=blind),
    }


def _blind_site_table(snapshot: QcExportSnapshot) -> pa.Table:
    table = snapshot.table("site_metrics.csv", blind=True)
    omitted = {
        str(row.get("column") or "")
        for row in snapshot.dictionary_rows(filename="site_metrics.csv", blind=True)
        if str(row.get("layer") or "") == "key"
        and str(row.get("column") or "") not in _BLIND_SITE_NAVIGATION
    }
    return table.drop_columns(sorted(omitted.intersection(table.column_names)))


def _site_table(snapshot: QcExportSnapshot, *, blind: bool) -> pa.Table:
    return _blind_site_table(snapshot) if blind else snapshot.table("site_metrics.csv")


def _metadata_columns(metadata: WellMetadata) -> tuple[str, ...]:
    return tuple(sorted({str(name) for values in metadata.values() for name in values}))


def _metadata_for_row(
    row: Mapping[str, object],
    metadata: WellMetadata,
) -> Mapping[str, JsonScalar]:
    return _metadata_for_key(
        row.get("experiment"),
        row.get("plate"),
        row.get("well"),
        metadata,
    )


def _metadata_for_key(
    experiment: object,
    plate: object,
    well: object,
    metadata: WellMetadata,
) -> Mapping[str, JsonScalar]:
    try:
        key = (
            str(experiment),
            int(plate),
            str(well),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise QcPayloadError("QC field has an invalid Phase 1 metadata key") from exc
    try:
        return metadata[key]
    except KeyError as exc:
        raise QcPayloadError("Phase 1 metadata does not cover every QC well") from exc


def _metadata_payload(
    table: pa.Table,
    metadata: WellMetadata,
) -> dict[str, list[JsonScalar]]:
    names = _metadata_columns(metadata)
    columns = {name: [] for name in names}
    keys = zip(
        table["experiment"].to_pylist(),
        table["plate"].to_pylist(),
        table["well"].to_pylist(),
        strict=True,
    )
    for experiment, plate, well in keys:
        additions = _metadata_for_key(experiment, plate, well, metadata)
        for name in names:
            columns[name].append(_scalar(additions.get(name)))
    return columns


def fields_payload(
    snapshot: QcExportSnapshot,
    metadata: WellMetadata,
    *,
    blind: bool = False,
    annotations: tuple[Mapping[str, object], ...] = (),
) -> JsonObject:
    """Return the full site table in a browser-efficient columnar layout."""
    _require_core(snapshot)
    table = _site_table(snapshot, blind=blind)
    payload: JsonObject = {
        **_envelope(snapshot, blind=blind),
        "row_count": table.num_rows,
        "columns": {name: _column(table[name]) for name in table.column_names},
        "annotations": [
            annotation_record_payload(row, blind=blind) for row in annotations
        ],
    }
    if not blind:
        payload["well_metadata"] = _metadata_payload(table, metadata)
    return payload


def field_payload(
    snapshot: QcExportSnapshot,
    metadata: WellMetadata,
    *,
    plate: int,
    well: str,
    site: int,
    blind: bool = False,
    annotations: tuple[Mapping[str, object], ...] = (),
) -> JsonObject:
    """Return one field and its applicable site dictionary rows."""
    _require_core(snapshot)
    canonical_well = well.upper()
    table = _site_table(snapshot, blind=blind)
    selected = table.filter(
        pc.and_(
            pc.and_(pc.equal(table["plate"], plate), pc.equal(table["well"], canonical_well)),
            pc.equal(table["site"], site),
        )
    )
    if selected.num_rows != 1:
        raise QcFieldNotFoundError("QC field does not exist")
    field = _record(selected.to_pylist()[0])
    identity_table = snapshot.table("site_metrics.csv")
    identity_selected = identity_table.filter(
        pc.and_(
            pc.and_(
                pc.equal(identity_table["plate"], plate),
                pc.equal(identity_table["well"], canonical_well),
            ),
            pc.equal(identity_table["site"], site),
        )
    )
    if identity_selected.num_rows != 1:
        raise QcFieldNotFoundError("QC field does not exist")
    field_barcode = str(identity_selected["plate_barcode"][0].as_py() or "")
    payload: JsonObject = {
        **_envelope(snapshot, blind=blind),
        "field": field,
        "dictionary": _applicable_dictionary_rows(
            snapshot,
            blind=blind,
            filename="site_metrics.csv",
        ),
        "annotations": [
            annotation_record_payload(annotation, blind=blind)
            for annotation in annotations
            if annotation.get("plate") == plate
            and annotation.get("barcode") == field_barcode
            and str(annotation.get("well") or "").upper() == canonical_well
            and (
                annotation.get("scope") == "well"
                or (
                    annotation.get("scope") == "field"
                    and annotation.get("site") == site
                )
            )
        ],
    }
    if not blind:
        payload["well_metadata"] = {
            name: _scalar(value) for name, value in _metadata_for_row(field, metadata).items()
        }
    return payload


def triage_payload(snapshot: QcExportSnapshot, *, blind: bool = False) -> JsonObject:
    """Return the plate-by-channel panel rows in export order."""
    _require_core(snapshot)
    return {
        **_envelope(snapshot, blind=blind),
        "rows": _records(snapshot.table("triage_plate_channel.csv", blind=blind)),
    }


def display_ranges_payload(
    snapshot: QcExportSnapshot,
    *,
    blind: bool = False,
) -> JsonObject:
    """Return plain-linear QC display ranges in export order."""
    _require_core(snapshot)
    table = snapshot.table("display_ranges.csv", blind=blind)
    if blind:
        table = table.drop_columns(
            sorted(_BLIND_DISPLAY_OMISSIONS.intersection(table.column_names))
        )
    return {**_envelope(snapshot, blind=blind), "rows": _records(table)}


def review_list_payload(snapshot: QcExportSnapshot, *, blind: bool = False) -> JsonObject:
    """Return the export queue in file order, with an explicit blinded projection."""
    _require_core(snapshot)
    table = snapshot.table("review_list.csv", blind=blind)
    if blind:
        missing = sorted(set(_BLIND_REVIEW_COLUMNS).difference(table.column_names))
        if missing:
            raise QcPayloadError("Blinded review queue is missing required navigation columns")
        table = table.select(_BLIND_REVIEW_COLUMNS)
    return {**_envelope(snapshot, blind=blind), "rows": _records(table)}
