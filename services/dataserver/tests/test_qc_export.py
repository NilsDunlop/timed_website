from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from timed_dataserver.packed import PackedCatalogue, PackedCatalogueCache
from timed_dataserver.packed_profiles import IDR0080_FIXTURE_PROFILE
from timed_dataserver.qc_export import QcExportStore, inspect_qc_export

from scripts import make_test_data


@pytest.fixture(scope="module")
def qc_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, PackedCatalogue]:
    output = tmp_path_factory.mktemp("qc-export")
    packed = make_test_data._write_packed_fixture(output)
    make_test_data._write_qc_export_fixture(output)
    make_test_data._write_qc_export_fixture(
        output,
        directory=make_test_data.GENERATED_NEXT_QC_EXPORT_DIRECTORY,
        variant="next",
    )
    catalogue = PackedCatalogueCache().get(packed / make_test_data.PACKED_INDEX_NAME)
    return output, catalogue


def _copy_export(source: Path, destination: Path) -> Path:
    return Path(shutil.copytree(source, destination))


def _rewrite_rows(path: Path, update: object) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)
    assert fieldnames
    assert callable(update)
    update(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _publish_changed_file(root: Path, filename: str, *, label: str) -> None:
    version_path = root / "version.json"
    version = json.loads(version_path.read_text(encoding="utf-8"))
    version["files"][filename] = hashlib.sha256((root / filename).read_bytes()).hexdigest()
    version["label"] = label
    version_path.write_text(
        json.dumps(version, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _drop_csv_columns(
    path: Path,
    excluded: set[str],
    *,
    keep_row: object | None = None,
) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(name for name in reader.fieldnames or () if name not in excluded)
        rows = list(reader)
    assert fieldnames
    if keep_row is not None:
        assert callable(keep_row)
        rows = [row for row in rows if keep_row(row)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_generated_qc_exports_are_deterministic_and_follow_next_contract(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for output in (first, second):
        make_test_data._write_qc_export_fixture(output)
        make_test_data._write_qc_export_fixture(
            output,
            directory=make_test_data.GENERATED_NEXT_QC_EXPORT_DIRECTORY,
            variant="next",
        )

    for directory in (
        make_test_data.GENERATED_QC_EXPORT_DIRECTORY,
        make_test_data.GENERATED_NEXT_QC_EXPORT_DIRECTORY,
    ):
        for filename in (*make_test_data.QC_EXPORT_FILES, "version.json"):
            assert (first / directory / filename).read_bytes() == (
                second / directory / filename
            ).read_bytes()

    snapshot = inspect_qc_export(first / make_test_data.GENERATED_NEXT_QC_EXPORT_DIRECTORY)
    assert snapshot.core_ready
    assert snapshot.row_count == 30
    assert "fixture_texture__ER__raw" in snapshot.table("site_metrics.csv").column_names
    triage_columns = set(snapshot.table("triage_plate_channel.csv").column_names)
    assert {
        row["column"]
        for row in snapshot.dictionary_rows(filename="triage_plate_channel.csv")
    } == triage_columns
    primary = [
        row
        for row in snapshot.dictionary_rows()
        if row["column"] == "fixture_texture__ER__raw"
    ]
    assert len(primary) == 1
    assert primary[0]["primary"] is True
    assert primary[0]["default_flag_lo"] == -1.5
    assert primary[0]["default_flag_hi"] == 1.5


def test_loader_validates_catalogue_channels_and_retains_all_core_columns(
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    output, catalogue = qc_fixture
    store = QcExportStore(
        output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY,
        catalogue,
        IDR0080_FIXTURE_PROFILE,
    )
    snapshot = store.require_snapshot()

    assert snapshot.core_ready
    assert snapshot.row_count == len(catalogue.fields) == 30
    assert snapshot.unknown_statuses == (
        "fixture_future_status",
        "fixture_hidden_future_status",
    )
    assert snapshot.unknown_statuses_for(blind=True) == ("fixture_future_status",)
    assert store.status_for(blind=True).unknown_statuses == ("fixture_future_status",)
    assert store.status.available
    assert store.status.core_ready
    assert store.status.hash_check == "ok"
    assert store.status.error is None

    channel_rows = snapshot.table("channels.csv").to_pylist()
    mapping = [
        (row["channel"], row["packed_slot"], row["packed_member_suffix"])
        for row in channel_rows
    ]
    assert mapping == [
        ("DNA", 1, "_ch1.tiff"),
        ("ER", 2, "_ch2.tiff"),
        ("AGP", 3, "_ch3.tiff"),
        ("RNA", 4, "_ch4.tiff"),
        ("Mito", 5, "_ch5.tiff"),
    ]
    signatures = [
        int(make_test_data.make_packed_plane(14610, 0, 1, slot)[0, 0])
        for slot in range(1, 6)
    ]
    assert len(set(signatures)) == 5
    first_site = snapshot.table("site_metrics.csv").to_pylist()[0]
    assert first_site["member_AGP"].endswith("_ch3.tiff")
    assert first_site["member_RNA"].endswith("_ch4.tiff")


def test_blind_policy_only_removes_dictionary_hidden_columns(
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    output, catalogue = qc_fixture
    snapshot = QcExportStore(
        output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY,
        catalogue,
        IDR0080_FIXTURE_PROFILE,
    ).require_snapshot()

    for filename in make_test_data.QC_EXPORT_FILES:
        assert set(snapshot.table(filename, blind=True).column_names).issubset(
            snapshot.table(filename).column_names
        )
    assert sum(len(columns) for columns in snapshot.hidden_columns.values()) >= 1
    for filename, hidden in snapshot.hidden_columns.items():
        assert all(name.startswith("fixture_") for name in hidden)
        assert hidden.isdisjoint(snapshot.table(filename, blind=True).column_names)
        assert hidden.issubset(snapshot.table(filename).column_names)
    assert all(row["blind"] != "hide" for row in snapshot.dictionary_rows(blind=True))
    review_hidden = snapshot.hidden_columns["review_list.csv"]
    assert review_hidden == frozenset(make_test_data.QC_REVIEW_HIDDEN_COLUMNS)
    assert len(review_hidden) == 8
    review_dictionary_hidden = {
        row["column"]
        for row in snapshot.dictionary_rows(filename="review_list.csv")
        if row["blind"] == "hide"
    }
    assert review_dictionary_hidden == review_hidden
    assert review_hidden.isdisjoint(
        snapshot.table("review_list.csv", blind=True).column_names
    )


def test_reload_publishes_second_export_atomically(
    tmp_path: Path,
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    output, catalogue = qc_fixture
    live = _copy_export(output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY, tmp_path / "live")
    store = QcExportStore(live, catalogue, IDR0080_FIXTURE_PROFILE)
    first = store.require_snapshot()
    assert "fixture_texture__ER__raw" not in first.table("site_metrics.csv").column_names

    replacement = output / make_test_data.GENERATED_NEXT_QC_EXPORT_DIRECTORY
    for filename in make_test_data.QC_EXPORT_FILES:
        shutil.copyfile(replacement / filename, live / filename)
    shutil.copyfile(replacement / "version.json", live / "version.json")

    second = store.refresh()
    assert second is not None
    assert second is not first
    assert second.export_id != first.export_id
    assert "fixture_texture__ER__raw" in second.table("site_metrics.csv").column_names
    assert store.status.hash_check == "ok"


def test_hash_mismatch_retries_same_version_and_then_publishes(
    tmp_path: Path,
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    output, catalogue = qc_fixture
    live = _copy_export(output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY, tmp_path / "live")
    store = QcExportStore(live, catalogue, IDR0080_FIXTURE_PROFILE, retry_seconds=0)
    first = store.require_snapshot()
    replacement = output / make_test_data.GENERATED_NEXT_QC_EXPORT_DIRECTORY

    shutil.copyfile(replacement / "version.json", live / "version.json")
    assert store.refresh() is first
    assert store.status.hash_check == "error"

    for filename in make_test_data.QC_EXPORT_FILES:
        shutil.copyfile(replacement / filename, live / filename)
    second = store.refresh()
    assert second is not None
    assert second.export_id != first.export_id
    assert store.status.hash_check == "ok"
    assert store.status.error is None


def test_current_contract_loads_as_base_but_is_not_core_ready(
    tmp_path: Path,
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    output, catalogue = qc_fixture
    live = _copy_export(output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY, tmp_path / "live")
    _drop_csv_columns(
        live / "columns.csv",
        set(make_test_data.QC_DICTIONARY_FIELDS[-4:]),
        keep_row=lambda row: row["file"] != "triage_plate_channel.csv"
        and not (
            row["file"] == "review_list.csv"
            and row["column"] in {"entry_id", "review_group"}
        ),
    )
    _drop_csv_columns(live / "review_list.csv", {"entry_id", "review_group"})
    _publish_changed_file(live, "columns.csv", label="current-contract fixture")
    _publish_changed_file(live, "review_list.csv", label="current-contract fixture")

    store = QcExportStore(live, catalogue, IDR0080_FIXTURE_PROFILE)
    snapshot = store.require_snapshot()
    assert store.status.base_available
    assert not snapshot.core_ready
    assert not store.status.core_ready
    assert {
        "columns.csv.primary",
        "columns.csv.default_flag_lo",
        "columns.csv.default_flag_hi",
        "columns.csv.markers",
        "review_list.csv.entry_id",
        "review_list.csv.review_group",
        "dictionary:triage_plate_channel.csv",
        "columns.csv.layer=panel",
    }.issubset(snapshot.missing_contract)


def test_hash_failure_preserves_prior_snapshot(
    tmp_path: Path,
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    output, catalogue = qc_fixture
    live = _copy_export(output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY, tmp_path / "live")
    store = QcExportStore(live, catalogue, IDR0080_FIXTURE_PROFILE)
    first = store.require_snapshot()
    with (live / "site_metrics.csv").open("ab") as handle:
        handle.write(b"\n")
    version_path = live / "version.json"
    version = json.loads(version_path.read_text(encoding="utf-8"))
    version["label"] = "hash mismatch reload"
    version_path.write_text(json.dumps(version, sort_keys=True) + "\n", encoding="utf-8")

    assert store.refresh() is first
    assert store.status.available
    assert store.status.hash_check == "error"
    assert store.status.error is not None
    assert "SHA-256 mismatch" in store.status.error
    assert store.status.export_id == first.export_id


def test_channel_mapping_mismatch_never_publishes_snapshot(
    tmp_path: Path,
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    output, catalogue = qc_fixture
    live = _copy_export(output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY, tmp_path / "live")

    def swap_slots(rows: list[dict[str, str]]) -> None:
        for row in rows:
            if row["channel"] == "AGP":
                row["packed_slot"] = "4"
                row["packed_member_suffix"] = "_ch4.tiff"
            elif row["channel"] == "RNA":
                row["packed_slot"] = "3"
                row["packed_member_suffix"] = "_ch3.tiff"

    _rewrite_rows(live / "channels.csv", swap_slots)
    _publish_changed_file(live, "channels.csv", label="invalid channel mapping")
    store = QcExportStore(live, catalogue, IDR0080_FIXTURE_PROFILE)

    assert store.refresh() is None
    assert not store.status.available
    assert store.status.hash_check == "error"
    assert store.status.error == "channels.csv does not match the packed dataset profile"


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("display_lo", "nan"),
        ("display_hi", "inf"),
        ("display_lo", "5250"),
        ("display_mode", "adaptive"),
        ("quantile_lo", "nan"),
        ("quantile_hi", "inf"),
        ("quantile_lo", "-0.1"),
        ("quantile_hi", "1.1"),
        ("quantile_lo", "0.995"),
        ("reference_fields", "0"),
        ("reference_fields", "1.5"),
    ),
)
def test_invalid_display_ranges_never_publish_snapshot(
    tmp_path: Path,
    qc_fixture: tuple[Path, PackedCatalogue],
    column: str,
    value: str,
) -> None:
    output, catalogue = qc_fixture
    live = _copy_export(
        output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY,
        tmp_path / f"invalid-{column}-{value.replace('.', '_')}",
    )

    def invalidate_first_row(rows: list[dict[str, str]]) -> None:
        rows[0][column] = value

    _rewrite_rows(live / "display_ranges.csv", invalidate_first_row)
    _publish_changed_file(live, "display_ranges.csv", label="invalid display range")
    store = QcExportStore(live, catalogue, IDR0080_FIXTURE_PROFILE)

    assert store.refresh() is None
    assert not store.status.available
    assert store.status.hash_check == "error"
    assert store.status.error is not None
    assert store.status.error.startswith("display_ranges.csv")


def test_parser_and_dictionary_errors_do_not_echo_export_contents(
    tmp_path: Path,
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    output, catalogue = qc_fixture
    malformed = _copy_export(
        output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY,
        tmp_path / "malformed",
    )
    (malformed / "site_metrics.csv").write_bytes(
        b"first,second\n1,2,fixture-sensitive-value\n"
    )
    _publish_changed_file(malformed, "site_metrics.csv", label="malformed CSV")
    malformed_store = QcExportStore(malformed, catalogue, IDR0080_FIXTURE_PROFILE)
    assert malformed_store.refresh() is None
    assert malformed_store.status.error == "Could not parse site_metrics.csv as CSV"
    assert "fixture-sensitive-value" not in (malformed_store.status.error or "")

    duplicate = _copy_export(
        output / make_test_data.GENERATED_QC_EXPORT_DIRECTORY,
        tmp_path / "duplicate",
    )
    columns_path = duplicate / "columns.csv"
    with columns_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = tuple(rows[0])
    hidden_row = next(row for row in rows if row["blind"] == "hide")
    rows.append(hidden_row)
    with columns_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    _publish_changed_file(duplicate, "columns.csv", label="duplicate dictionary row")
    duplicate_store = QcExportStore(duplicate, catalogue, IDR0080_FIXTURE_PROFILE)
    assert duplicate_store.refresh() is None
    assert duplicate_store.status.error is not None
    assert duplicate_store.status.error.startswith("columns.csv row ")
    assert "duplicates an earlier entry" in duplicate_store.status.error
    assert hidden_row["column"] not in duplicate_store.status.error


def test_missing_export_has_explicit_unavailable_status(
    tmp_path: Path,
    qc_fixture: tuple[Path, PackedCatalogue],
) -> None:
    _output, catalogue = qc_fixture
    store = QcExportStore(tmp_path / "missing", catalogue, IDR0080_FIXTURE_PROFILE)
    assert store.refresh() is None
    assert not store.status.available
    assert not store.status.core_ready
    assert store.status.hash_check == "unavailable"
    assert store.status.error is not None


def test_real_export_structure_opt_in() -> None:
    if os.environ.get("TIMED_TEST_REAL_QC_EXPORT") != "1":
        pytest.skip("set TIMED_TEST_REAL_QC_EXPORT=1 to validate the private local export")
    root = Path(__file__).parents[3] / "docs" / "data" / "qc-export"
    if not root.is_dir():
        pytest.skip("private local QC export is unavailable")
    snapshot = inspect_qc_export(root)
    expected_shapes = {
        "site_metrics.csv": (31_104, 69),
        "columns.csv": (87, 12),
        "channels.csv": (5, 14),
        "review_list.csv": (158, 18),
        "triage_plate_channel.csv": (54, 10),
        "display_ranges.csv": (45, 9),
    }

    assert set(snapshot.tables) == set(make_test_data.QC_EXPORT_FILES)
    assert {
        filename: (table.num_rows, table.num_columns)
        for filename, table in snapshot.tables.items()
    } == expected_shapes
    assert snapshot.row_count == snapshot.expected_fields == 31_104
    assert len(snapshot.registry_hash) == 64
    assert not snapshot.core_ready
    assert snapshot.missing_contract

    described = {
        (str(row["file"]), str(row["column"])) for row in snapshot.dictionary_rows()
    }
    for filename in ("site_metrics.csv", "review_list.csv"):
        assert {
            column for described_file, column in described if described_file == filename
        } == set(snapshot.table(filename).column_names)

    assert len(snapshot.hidden_columns["site_metrics.csv"]) == 16
    assert len(snapshot.hidden_columns["review_list.csv"]) == 8
    assert len(snapshot.hidden_columns["triage_plate_channel.csv"]) == 0
    assert sum(len(columns) for columns in snapshot.hidden_columns.values()) == 24
