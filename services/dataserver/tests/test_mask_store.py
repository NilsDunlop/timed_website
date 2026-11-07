"""Reading tar+wal/v1 mask stores: the log rules, corruption, revisions and safety."""

from __future__ import annotations

import builtins
import io
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import tifffile
from timed_dataserver.mask_reader import MaskNotFoundError, MaskReader
from timed_dataserver.mask_store import MaskStoreError, read_state
from timed_dataserver.packed_profiles import PackedMasks

from scripts import make_test_data, mask_stores

SHAPE = (make_test_data.PACKED_HEIGHT, make_test_data.PACKED_WIDTH)
WELLS = [well.address for well in make_test_data.PACKED_WELLS]
SITES = make_test_data.PACKED_SITES
MASKS = PackedMasks(path="masks", default_model=mask_stores.DEFAULT_MODEL)


@pytest.fixture(scope="module")
def packed_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_test_data._write_packed_fixture(tmp_path_factory.mktemp("mask-store"))


def _index(root: Path) -> Path:
    return root / make_test_data.PACKED_INDEX_NAME


def _counts(
    reader: MaskReader,
    root: Path,
    model: str,
    experiment: str,
    plate: int,
    well: str,
    site: int,
) -> dict[str, int]:
    files, table = reader.plate(_index(root), MASKS, model, experiment, plate)
    return reader.counts(files, table, well, site, SHAPE)


def _expected(model: str, plate: int, well: str, site: int) -> dict[str, int]:
    return mask_stores.field_masks(model, plate, WELLS.index(well), site, SHAPE).counts


def _stores() -> Iterator[tuple[str, str, int]]:
    for experiment, plate, _cell_line in make_test_data.PACKED_PLATES:
        yield mask_stores.DEFAULT_MODEL, experiment, plate
        if plate == 14610:
            yield mask_stores.STOCK_MODEL, experiment, plate


def test_counts_are_the_largest_labels_of_each_fields_last_record(packed_root: Path) -> None:
    reader = MaskReader()
    for model, experiment, plate in _stores():
        for well in WELLS:
            for site in SITES:
                assert _counts(reader, packed_root, model, experiment, plate, well, site) == (
                    _expected(model, plate, well, site)
                ), (model, plate, well, site)
    plate, well, site = mask_stores.REWRITTEN_FIELD
    rewritten = _counts(
        reader, packed_root, mask_stores.DEFAULT_MODEL, "idr0080-A549", plate, well, site
    )
    assert rewritten != mask_stores.superseded_masks(SHAPE).counts


def test_a_half_written_last_line_and_uncommitted_tar_bytes_are_ignored(
    packed_root: Path,
) -> None:
    reader = MaskReader()
    plate, well, site = mask_stores.IN_PROGRESS_FIELD
    files, table = reader.plate(
        _index(packed_root), MASKS, mask_stores.DEFAULT_MODEL, "idr0080-A549", plate
    )
    assert table.committed_length < files.tar.stat().st_size
    assert len(table.records) == len(WELLS) * len(SITES)
    assert reader.counts(files, table, well, site, SHAPE) == _expected(
        mask_stores.DEFAULT_MODEL, plate, well, site
    )


def _fields(model: str, plate: int) -> list[tuple[str, int, mask_stores.FieldMasks]]:
    return [
        (well, site, mask_stores.field_masks(model, plate, index, site, SHAPE))
        for index, well in enumerate(WELLS)
        for site in SITES
    ]


def _model_dir(root: Path) -> Path:
    model_dir = root / "masks" / mask_stores.DEFAULT_MODEL
    mask_stores.write_manifest(model_dir, mask_stores.DEFAULT_MODEL)
    return model_dir


def _single_store(root: Path, **options: object) -> mask_stores.PlateStore:
    return mask_stores.write_plate_store(
        _model_dir(root),
        mask_stores.DEFAULT_MODEL,
        "idr0080-A549",
        14610,
        _fields(mask_stores.DEFAULT_MODEL, 14610),
        **options,  # type: ignore[arg-type]
    )


def _plate(reader: MaskReader, root: Path) -> tuple:
    return reader.plate(
        root / make_test_data.PACKED_INDEX_NAME,
        MASKS,
        mask_stores.DEFAULT_MODEL,
        "idr0080-A549",
        14610,
    )


def test_a_growing_store_is_picked_up_when_its_revision_changes(tmp_path: Path) -> None:
    store = _single_store(tmp_path, in_progress=("A02", 1, mask_stores.in_progress_masks(SHAPE)))
    assert store.pending_line is not None
    reader = MaskReader()
    files, table = _plate(reader, tmp_path)
    assert table.revision == read_state(files).revision
    log = files.log.stat()
    assert (table.revision.log_size, table.revision.log_mtime_ns, table.revision.tar_size) == (
        log.st_size,
        log.st_mtime_ns,
        files.tar.stat().st_size,
    )
    before = reader.counts(files, table, "A02", 1, SHAPE)
    assert before == _expected(mask_stores.DEFAULT_MODEL, 14610, "A02", 1)
    assert _plate(reader, tmp_path)[1] is table

    with files.log.open("ab") as handle:
        handle.write(store.pending_line[store.partial_bytes :])
    files, grown = _plate(reader, tmp_path)
    assert grown is not table
    assert grown.revision.log_size > table.revision.log_size
    assert reader.counts(files, grown, "A02", 1, SHAPE) == mask_stores.in_progress_masks(
        SHAPE
    ).counts
    # Masks decoded before the store grew stay cached; their members never move.
    assert reader.counts(files, grown, "A01", 1, SHAPE) == _expected(
        mask_stores.DEFAULT_MODEL, 14610, "A01", 1
    )
    assert reader.cache.entry_count == 9


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"tar_shortfall": 512}, "the tar has 30208 bytes but the log commits 30720"),
        ({"broken_line": 3}, "log line 4 is not valid JSON"),
    ],
)
def test_corrupt_stores_are_refused_with_their_reason(
    tmp_path: Path,
    options: dict[str, object],
    reason: str,
) -> None:
    _single_store(tmp_path, **options)
    with pytest.raises(MaskStoreError) as raised:
        _plate(MaskReader(), tmp_path)
    message = str(raised.value)
    assert message == (
        f"Mask store {mask_stores.DEFAULT_MODEL}/idr0080-A549/Plate14610 is corrupt: {reason}"
    )
    assert str(tmp_path) not in message and tmp_path.as_posix() not in message


def _first(old: bytes, new: bytes) -> object:
    """An edit that replaces text in the log's first line."""

    def edit(lines: list[bytes]) -> None:
        lines[0] = lines[0].replace(old, new)

    return edit


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        (lambda lines: lines.insert(1, b"\n"), "log line 2 is not valid JSON"),
        (lambda lines: lines.insert(1, b"[1]\n"), "log line 2 is not a JSON object"),
        (lambda lines: lines.insert(1, b"\xff\n"), "log line 2 is not valid JSON"),
        (
            lambda lines: lines.__setitem__(
                2, lines[2].replace(b'"tar_end": ', b'"tar_end": 1', 1)
            ),
            "log line 3 has a tar_end that is not a multiple of 512",
        ),
        (lambda lines: lines.append(lines[0]), "log line 11 moves tar_end back"),
        (_first(b'"site": 1', b'"site": -1'), "log line 1 has no valid site"),
        (_first(b'"well": "A01"', b'"well": "../x"'), "log line 1 has no valid well"),
        (_first(b'"kind": "cells"', b'"kind": "nuclei"'), "log line 1 repeats mask kind nuclei"),
        (_first(b'"offset": 512', b'"offset": 0'), "log line 1 has invalid bounds for mask nuclei"),
    ],
)
def test_malformed_committed_lines_make_the_store_corrupt(
    tmp_path: Path,
    edit: object,
    reason: str,
) -> None:
    store = _single_store(tmp_path)
    lines = store.log.read_bytes().splitlines(keepends=True)
    edit(lines)  # type: ignore[operator]
    store.log.write_bytes(b"".join(lines))
    with pytest.raises(MaskStoreError, match=f"is corrupt: {reason}$"):
        _plate(MaskReader(), tmp_path)


def test_a_member_beyond_its_records_tar_end_is_corrupt(tmp_path: Path) -> None:
    store = _single_store(tmp_path)
    lines = store.log.read_bytes().splitlines(keepends=True)
    first_end = int(store.records[0]["tar_end"])
    lines[0] = lines[0].replace(f'"tar_end": {first_end}'.encode(), b'"tar_end": 512')
    store.log.write_bytes(b"".join(lines))
    with pytest.raises(MaskStoreError, match="log line 1 places mask nuclei beyond its tar_end"):
        _plate(MaskReader(), tmp_path)


def _write_raw(root: Path, payloads: dict[str, bytes], **names: str) -> MaskReader:
    mask_stores.write_raw_store(
        _model_dir(root),
        "idr0080-A549",
        14610,
        "A01",
        1,
        payloads,
        member_names=names,
    )
    return MaskReader()


def _tiff(array: np.ndarray, **options: object) -> bytes:
    output = io.BytesIO()
    tifffile.imwrite(output, array, photometric="minisblack", metadata=None, **options)  # type: ignore[arg-type]
    return output.getvalue()


def _labels() -> np.ndarray:
    labels = np.zeros(SHAPE, dtype=np.uint16)
    labels[10:20, 10:20] = 1
    labels[30:50, 30:40] = 2
    return labels


def test_a_well_formed_raw_mask_decodes_without_casting(tmp_path: Path) -> None:
    reader = _write_raw(tmp_path, {"nuclei": _tiff(_labels(), compression="zlib")})
    files, table = _plate(reader, tmp_path)
    target = reader.target(files, table, "A01", 1, "nuclei")
    decoded = reader.labels(target, SHAPE)
    assert decoded.count == 2
    assert decoded.labels.dtype == np.uint16
    assert np.array_equal(decoded.labels, _labels())
    assert not decoded.labels.flags.writeable


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (_tiff(_labels()), "mask nuclei: TIFF compression is NONE, expected zlib"),
        (
            _tiff(_labels().astype(np.uint8), compression="zlib"),
            "mask nuclei: TIFF dtype is uint8, expected uint16",
        ),
        (
            _tiff(_labels()[:32], compression="zlib"),
            "mask nuclei: TIFF shape is (32, 64), expected (64, 64)",
        ),
        (
            _tiff(np.stack([_labels(), _labels()]), compression="zlib"),
            "mask nuclei: TIFF has 2 pages, expected 1",
        ),
        (b"not a tiff at all", "mask nuclei could not be decoded"),
    ],
    ids=["uncompressed", "uint8", "shape", "two-pages", "not-a-tiff"],
)
def test_masks_must_be_single_page_zlib_uint16_planes(
    tmp_path: Path,
    payload: bytes,
    reason: str,
) -> None:
    reader = _write_raw(tmp_path, {"nuclei": payload})
    files, table = _plate(reader, tmp_path)
    target = reader.target(files, table, "A01", 1, "nuclei")
    with pytest.raises(MaskStoreError, match="is corrupt: ") as raised:
        reader.labels(target, SHAPE)
    assert reason in str(raised.value)


def test_member_headers_are_verified_before_their_data(tmp_path: Path) -> None:
    reader = _write_raw(
        tmp_path,
        {"nuclei": _tiff(_labels(), compression="zlib")},
        nuclei="idr0080-A549/Plate14610/A01_s2_nuclei.tiff",
    )
    files, table = _plate(reader, tmp_path)
    with pytest.raises(MaskStoreError, match="header before offset 512: member name is"):
        reader.labels(reader.target(files, table, "A01", 1, "nuclei"), SHAPE)

    shutil.rmtree(tmp_path / "masks")
    reader = _write_raw(tmp_path, {"nuclei": _tiff(_labels(), compression="zlib")})
    files, table = _plate(reader, tmp_path)
    tar = bytearray(files.tar.read_bytes())
    tar[257:263] = b"xxxxx\0"
    files.tar.write_bytes(bytes(tar))
    with pytest.raises(MaskStoreError, match="header before offset 512: header does not contain"):
        reader.labels(reader.target(files, table, "A01", 1, "nuclei"), SHAPE)


def test_missing_models_records_and_kinds_are_not_found(packed_root: Path) -> None:
    reader = MaskReader()
    index = _index(packed_root)
    with pytest.raises(MaskNotFoundError, match="Unknown mask model: nope"):
        reader.plate(index, MASKS, "nope", "idr0080-A549", 14610)
    with pytest.raises(MaskNotFoundError, match="does not cover this plate"):
        reader.plate(index, MASKS, mask_stores.STOCK_MODEL, "idr0080-A549", 14611)
    with pytest.raises(MaskNotFoundError, match="Unknown mask model"):
        reader.plate(index, MASKS, mask_stores.OTHER_FORMAT_MODEL, "idr0080-A549", 14610)
    files, table = reader.plate(index, MASKS, mask_stores.DEFAULT_MODEL, "idr0080-A549", 14610)
    with pytest.raises(MaskNotFoundError, match="No masks for C02 site 1"):
        reader.counts(files, table, "C02", 1, SHAPE)
    with pytest.raises(MaskNotFoundError, match="No membranes mask for A01 site 1"):
        reader.target(files, table, "A01", 1, "membranes")


def test_decoded_masks_are_cached_once_for_counts_and_outlines(packed_root: Path) -> None:
    reader = MaskReader()
    files, table = reader.plate(
        _index(packed_root), MASKS, mask_stores.DEFAULT_MODEL, "idr0080-A549", 14610
    )
    reader.counts(files, table, "A01", 1, SHAPE)
    assert reader.cache.entry_count == 3
    assert reader.cache.current_bytes == 3 * SHAPE[0] * SHAPE[1] * 2
    loads: list[str] = []
    original = reader.cache.get_or_load

    def counted(key, loader):  # type: ignore[no-untyped-def]
        value, status = original(key, loader)
        loads.append(status)
        return value, status

    reader.cache.get_or_load = counted  # type: ignore[method-assign]
    reader.labels(reader.target(files, table, "A01", 1, "nuclei"), SHAPE)
    reader.counts(files, table, "A01", 1, SHAPE)
    assert loads == ["hit", "hit", "hit", "hit"]

    small = MaskReader(cache_bytes=2 * SHAPE[0] * SHAPE[1] * 2)
    small.counts(files, table, "A01", 1, SHAPE)
    assert small.cache.entry_count == 2


def test_concurrent_requests_decode_each_mask_once(packed_root: Path, monkeypatch) -> None:
    import timed_dataserver.mask_reader as mask_reader

    reader = MaskReader()
    files, table = reader.plate(
        _index(packed_root), MASKS, mask_stores.DEFAULT_MODEL, "idr0080-A549", 14610
    )
    decodes: list[str] = []
    release = threading.Event()
    original = mask_reader.decode_labels

    def slow(files, kind, payload, shape):  # type: ignore[no-untyped-def]
        decodes.append(kind)
        release.wait(5)
        return original(files, kind, payload, shape)

    monkeypatch.setattr(mask_reader, "decode_labels", slow)
    results: list[dict[str, int]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(reader.counts(files, table, "A02", 2, SHAPE))
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while not decodes and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    assert decodes == ["nuclei"], "followers must wait for the leader's decode"
    release.set()
    for thread in threads:
        thread.join(10)
    assert len(results) == 4 and all(result == results[0] for result in results)
    assert sorted(decodes) == ["cells", "cytoplasm", "nuclei"]


def test_a_differing_index_snapshot_is_logged_and_the_log_wins(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _single_store(tmp_path)
    rows = pq.read_table(store.index).to_pylist()
    rows[0]["offset"] += 512
    pq.write_table(pa.Table.from_pylist(rows), store.index)
    with caplog.at_level(logging.WARNING, logger="timed_dataserver.mask_store"):
        reader = MaskReader()
        files, table = _plate(reader, tmp_path)
    assert "index snapshot differs from its log in 2 entries; serving the log" in caplog.text
    assert reader.counts(files, table, "A01", 1, SHAPE) == _expected(
        mask_stores.DEFAULT_MODEL, 14610, "A01", 1
    )


def test_readers_open_only_manifests_logs_tars_and_snapshots_read_only(
    packed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[str, int]] = []
    real_open = os.open
    real_builtin_open = builtins.open

    def recording_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append((os.fspath(path), flags))
        return real_open(path, flags, *args, **kwargs)

    def recording_builtin_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append((os.fspath(file) if not isinstance(file, int) else str(file), -1))
        return real_builtin_open(file, mode, *args, **kwargs)

    def no_locks(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mask readers must not take file locks")

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(builtins, "open", recording_builtin_open)
    monkeypatch.setattr(io, "open", recording_builtin_open)
    try:
        import fcntl
    except ImportError:
        import msvcrt

        monkeypatch.setattr(msvcrt, "locking", no_locks)
    else:
        monkeypatch.setattr(fcntl, "flock", no_locks)
        monkeypatch.setattr(fcntl, "lockf", no_locks)

    reader = MaskReader()
    index = _index(packed_root)
    for experiment, plate, _cell_line in make_test_data.PACKED_PLATES:
        for entry in reader.models(index, MASKS, experiment, plate):
            if not entry.available:
                continue
            files, table = reader.plate(index, MASKS, entry.model.tag, experiment, plate)
            for well in WELLS:
                for site in SITES:
                    reader.counts(files, table, well, site, SHAPE)

    names = [Path(path).name for path, _flags in opened]
    assert opened, "the readers opened nothing"
    writing = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
    assert all(flags == -1 or not flags & writing for _path, flags in opened)
    assert all(flags != -1 for _path, flags in opened), "every open uses os.open"
    assert {name.split(".", 1)[1] for name in names if name.startswith("Plate")} <= {
        "masks.tar",
        "wal.jsonl",
        "index.parquet",
    }
    assert set(names) - {name for name in names if name.startswith("Plate")} == {
        "manifest.json"
    }
    for path, _flags in opened:
        parts = Path(path).parts
        assert "profiles" not in parts
        assert not path.endswith((".lease", ".qc.parquet", "run_ledger.jsonl"))
