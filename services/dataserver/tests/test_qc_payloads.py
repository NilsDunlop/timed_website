from __future__ import annotations

import json
from dataclasses import replace
from types import MappingProxyType

import pyarrow as pa
import pytest
from timed_dataserver.qc_export import QcExportSnapshot, inspect_qc_export
from timed_dataserver.qc_payloads import (
    QcFieldNotFoundError,
    QcPayloadError,
    dictionary_payload,
    display_ranges_payload,
    field_payload,
    fields_payload,
    review_list_payload,
    triage_payload,
)

from scripts import make_test_data

_NAVIGATION_KEYS = frozenset(("experiment", "plate", "well", "site"))


@pytest.fixture(scope="module")
def payload_snapshot(tmp_path_factory: pytest.TempPathFactory) -> QcExportSnapshot:
    output = tmp_path_factory.mktemp("qc-payloads")
    root = make_test_data._write_qc_export_fixture(output)
    snapshot = _inject_hidden_site_value(inspect_qc_export(root))
    return _inject_hidden_display_range_value(snapshot)


def _inject_hidden_site_value(snapshot: QcExportSnapshot) -> QcExportSnapshot:
    column = "runtime_private_column"
    value = "runtime-private-token"
    site = snapshot.table("site_metrics.csv").append_column(
        column,
        pa.array([value] * snapshot.row_count),
    )
    dictionary = snapshot.table("columns.csv")
    template = {name: None for name in dictionary.column_names}
    template.update(
        {
            "file": "site_metrics.csv",
            "column": column,
            "metric": "",
            "version": None,
            "channel": "",
            "scale": "",
            "worse": "context",
            "layer": "context",
            "gate": "",
            "status": "",
            "question": "Runtime-only private payload regression value.",
            "blind": "hide",
            "primary": False,
            "default_flag": None,
            "markers": None,
        }
    )
    dictionary = pa.concat_tables(
        [dictionary, pa.Table.from_pylist([template], schema=dictionary.schema)]
    )
    tables = dict(snapshot.tables)
    tables["site_metrics.csv"] = site
    tables["columns.csv"] = dictionary
    hidden = dict(snapshot.hidden_columns)
    hidden["site_metrics.csv"] = frozenset((*hidden["site_metrics.csv"], column))
    return replace(
        snapshot,
        tables=MappingProxyType(tables),
        hidden_columns=MappingProxyType(hidden),
    )


def _inject_hidden_display_range_value(snapshot: QcExportSnapshot) -> QcExportSnapshot:
    column = "runtime_private_range"
    value = "runtime-private-range-token"
    ranges = snapshot.table("display_ranges.csv").append_column(
        column,
        pa.array([value] * snapshot.table("display_ranges.csv").num_rows),
    )
    dictionary = snapshot.table("columns.csv")
    template = {name: None for name in dictionary.column_names}
    template.update(
        {
            "file": "display_ranges.csv",
            "column": column,
            "metric": "",
            "version": None,
            "channel": "",
            "scale": "",
            "worse": "context",
            "layer": "context",
            "gate": "",
            "status": "",
            "question": "Runtime-only private display-range regression value.",
            "blind": "hide",
            "primary": False,
            "default_flag": None,
            "markers": None,
        }
    )
    dictionary = pa.concat_tables(
        [dictionary, pa.Table.from_pylist([template], schema=dictionary.schema)]
    )
    tables = dict(snapshot.tables)
    tables["display_ranges.csv"] = ranges
    tables["columns.csv"] = dictionary
    hidden = dict(snapshot.hidden_columns)
    hidden["display_ranges.csv"] = frozenset(
        (*hidden.get("display_ranges.csv", ()), column)
    )
    return replace(
        snapshot,
        tables=MappingProxyType(tables),
        hidden_columns=MappingProxyType(hidden),
    )


@pytest.fixture(scope="module")
def phase_one_metadata(
    payload_snapshot: QcExportSnapshot,
) -> dict[tuple[str, int, str], dict[str, object]]:
    result: dict[tuple[str, int, str], dict[str, object]] = {}
    for row in payload_snapshot.table("site_metrics.csv").to_pylist():
        key = (str(row["experiment"]), int(row["plate"]), str(row["well"]))
        result.setdefault(
            key,
            {
                "gene": f"fixture-gene-{row['well']}",
                "well_type": "fixture-query",
                "well_category": "fixture-category",
                "is_control": str(row["well"]) == "A01",
            },
        )
    return result


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, MappingProxyType):
        value = dict(value)
    if isinstance(value, dict):
        return [item for key, nested in value.items() for item in (str(key), *_all_strings(nested))]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _all_strings(nested)]
    return []


def _private_tokens(
    snapshot: QcExportSnapshot,
    metadata: dict[tuple[str, int, str], dict[str, object]],
) -> set[str]:
    tokens: set[str] = set()
    for filename, columns in snapshot.hidden_columns.items():
        table = snapshot.table(filename)
        for column in columns:
            tokens.add(column)
            tokens.update(
                value
                for value in table[column].to_pylist()
                if isinstance(value, str)
                and (value.startswith("fixture-") or value.startswith("runtime-"))
            )
    tokens.update(str(name) for values in metadata.values() for name in values)
    tokens.update(
        value for values in metadata.values() for value in values.values() if isinstance(value, str)
    )
    for row in snapshot.dictionary_rows():
        marker = row.get("markers")
        if marker:
            tokens.add("markers")
            tokens.add(str(marker))
    display = snapshot.table("display_ranges.csv")
    tokens.add("plate_barcode")
    tokens.update(str(value) for value in display["plate_barcode"].to_pylist())
    return tokens


def test_core_payloads_preserve_export_columns_metadata_and_order(
    payload_snapshot: QcExportSnapshot,
    phase_one_metadata: dict[tuple[str, int, str], dict[str, object]],
) -> None:
    dictionary = dictionary_payload(payload_snapshot)
    fields = fields_payload(payload_snapshot, phase_one_metadata)
    first_source = payload_snapshot.table("site_metrics.csv").to_pylist()[0]
    field = field_payload(
        payload_snapshot,
        phase_one_metadata,
        plate=int(first_source["plate"]),
        well=str(first_source["well"]),
        site=int(first_source["site"]),
    )
    triage = triage_payload(payload_snapshot)
    ranges = display_ranges_payload(payload_snapshot)
    review = review_list_payload(payload_snapshot)

    site_table = payload_snapshot.table("site_metrics.csv")
    site_rows = site_table.to_pylist()
    expected_columns = {
        name: [row[name] for row in site_rows] for name in site_table.column_names
    }
    assert fields["row_count"] == site_table.num_rows
    assert fields["columns"] == expected_columns
    assert list(fields["columns"]) == site_table.column_names
    assert set(fields["well_metadata"]) == {
        "gene",
        "well_type",
        "well_category",
        "is_control",
    }
    assert field["field"]["runtime_private_column"] == "runtime-private-token"
    assert field["well_metadata"]["gene"] == f"fixture-gene-{first_source['well']}"
    expected_labels = [dict(row) for row in payload_snapshot.annotation_labels()]
    assert fields["labels"] == expected_labels
    assert field["labels"] == expected_labels
    assert set(triage["rows"][0]) == set(
        payload_snapshot.table("triage_plate_channel.csv").column_names
    )
    assert set(ranges["rows"][0]) == set(payload_snapshot.table("display_ranges.csv").column_names)
    assert set(review["rows"][0]) == set(payload_snapshot.table("review_list.csv").column_names)
    assert [row["entry_id"] for row in review["rows"]] == payload_snapshot.table("review_list.csv")[
        "entry_id"
    ].to_pylist()
    assert any(row["blind"] == "hide" for row in dictionary["rows"])

    visible_statuses = {str(row["status"] or "") for row in dictionary["rows"]}
    assert visible_statuses <= {"", "registered", "kept", "pending_review"}
    assert all(row["layer"] != "retired" for row in dictionary["rows"])
    for payload in (dictionary, fields, field, triage, ranges, review):
        json.dumps(payload, allow_nan=False)


def test_every_blind_payload_removes_private_and_header_tokens(
    payload_snapshot: QcExportSnapshot,
    phase_one_metadata: dict[tuple[str, int, str], dict[str, object]],
) -> None:
    first_source = payload_snapshot.table("site_metrics.csv").to_pylist()[0]
    payloads = (
        dictionary_payload(payload_snapshot, blind=True),
        fields_payload(payload_snapshot, phase_one_metadata, blind=True),
        field_payload(
            payload_snapshot,
            phase_one_metadata,
            plate=int(first_source["plate"]),
            well=str(first_source["well"]),
            site=int(first_source["site"]),
            blind=True,
        ),
        triage_payload(payload_snapshot, blind=True),
        display_ranges_payload(payload_snapshot, blind=True),
        review_list_payload(payload_snapshot, blind=True),
    )
    forbidden = _private_tokens(payload_snapshot, phase_one_metadata)

    for payload in payloads:
        strings = set(_all_strings(payload))
        overlap = strings.intersection(forbidden)
        assert not overlap, sorted(overlap)
        json.dumps(payload, allow_nan=False)

    fields = payloads[1]
    assert _NAVIGATION_KEYS.issubset(fields["columns"])
    assert "well_metadata" not in fields
    assert "well_metadata" not in payloads[2]
    header_columns = {
        str(row.get("column") or "")
        for row in payload_snapshot.dictionary_rows(filename="site_metrics.csv")
        if row.get("layer") == "key" and str(row.get("column") or "") not in _NAVIGATION_KEYS
    }
    assert set(fields["columns"]).isdisjoint(header_columns)
    assert set(payloads[2]["field"]).isdisjoint(header_columns)
    assert {
        str(row["column"]) for row in payloads[0]["rows"] if row["file"] == "site_metrics.csv"
    }.isdisjoint(header_columns)
    review = payloads[-1]
    assert set(review["rows"][0]) == {
        "entry_id",
        "review_group",
        "scope",
        "plate",
        "well",
        "site",
        "channel",
    }
    assert any(row["site"] is None for row in review["rows"])


def test_payloads_require_core_contract_and_complete_metadata(
    payload_snapshot: QcExportSnapshot,
    phase_one_metadata: dict[tuple[str, int, str], dict[str, object]],
) -> None:
    incomplete = replace(payload_snapshot, missing_contract=("fixture-contract",))
    with pytest.raises(QcPayloadError, match="core response contract"):
        dictionary_payload(incomplete)

    missing_metadata = dict(phase_one_metadata)
    missing_metadata.pop(next(iter(missing_metadata)))
    with pytest.raises(QcPayloadError, match="does not cover"):
        fields_payload(payload_snapshot, missing_metadata)


def test_field_payload_rejects_unknown_field(
    payload_snapshot: QcExportSnapshot,
    phase_one_metadata: dict[tuple[str, int, str], dict[str, object]],
) -> None:
    with pytest.raises(QcFieldNotFoundError, match="does not exist"):
        field_payload(
            payload_snapshot,
            phase_one_metadata,
            plate=-1,
            well="A01",
            site=1,
        )


def test_field_payload_does_not_cross_reused_plate_barcodes(
    payload_snapshot: QcExportSnapshot,
    phase_one_metadata: dict[tuple[str, int, str], dict[str, object]],
) -> None:
    first_source = payload_snapshot.table("site_metrics.csv").to_pylist()[0]
    target = {
        "scope": "field",
        "plate": int(first_source["plate"]),
        "barcode": str(first_source["plate_barcode"]),
        "well": str(first_source["well"]),
        "site": int(first_source["site"]),
    }
    matching = {**target, "id": "matching"}
    reused = {**target, "id": "reused", "barcode": "fixture-reused-barcode"}

    payload = field_payload(
        payload_snapshot,
        phase_one_metadata,
        plate=target["plate"],
        well=target["well"],
        site=target["site"],
        annotations=(matching, reused),
    )
    blinded = field_payload(
        payload_snapshot,
        phase_one_metadata,
        plate=target["plate"],
        well=target["well"],
        site=target["site"],
        blind=True,
        annotations=(matching, reused),
    )

    assert [record["id"] for record in payload["annotations"]] == ["matching"]
    assert [record["id"] for record in blinded["annotations"]] == ["matching"]
    assert "barcode" not in blinded["annotations"][0]
