from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from timed_dataserver import packed
from timed_dataserver.packed import (
    PackedCatalogueCache,
    PackedIndexError,
    clear_detection_cache,
    get_catalogue_in_thread,
    is_packed_index,
    resolve_packed_virtual_path,
)
from timed_dataserver.paths import PathForbiddenError, PathNotFoundError, RootGuard


def _row(
    *,
    experiment: str = "idr0080-A549",
    plate: int = 14610,
    well: str | None = "A01",
    site: int = 1,
    channel: int = 1,
    shard: str | None = None,
    offset: int = 512,
    size: int = 128,
) -> dict[str, object]:
    return {
        "relpath": f"{experiment}/Plate{plate}/{well}_s{site}_w{channel}.tif",
        "experiment": experiment,
        "plate": plate,
        "well": well,
        "site": site,
        "channel": channel,
        "offset": offset,
        "size": size,
        "shard": shard or f"plate-{plate}.tar",
    }


def _write_index(
    directory: Path,
    rows: list[dict[str, object]],
    *,
    name: str = "idr0080-raw.index.parquet",
    create_shards: bool = True,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    index = directory / name
    pq.write_table(pa.Table.from_pylist(rows), index)
    if create_shards and "shard" in rows[0]:
        for shard in {row["shard"] for row in rows if row.get("shard") is not None}:
            assert isinstance(shard, str)
            (directory / shard).write_bytes(b"tar-placeholder")
    return index


@pytest.fixture(autouse=True)
def _clear_detection_state() -> None:
    clear_detection_cache()


def test_detection_accepts_shards_and_fallback_but_rejects_decoys(tmp_path: Path) -> None:
    index = _write_index(tmp_path, [_row(), _row(channel=2, offset=1024)])
    assert is_packed_index(index)

    fallback_rows = [{key: value for key, value in _row().items() if key != "shard"}]
    fallback = _write_index(
        tmp_path / "fallback",
        fallback_rows,
        name="single.index.parquet",
        create_shards=False,
    )
    (fallback.parent / "single.tar").write_bytes(b"tar-placeholder")
    assert is_packed_index(fallback)

    mask_row = _row()
    del mask_row["channel"]
    mask_row["kind"] = "nuclei"
    mask = _write_index(tmp_path / "masks", [mask_row])
    assert not is_packed_index(mask)

    fragment_dir = tmp_path / "index_fragments"
    fragment = _write_index(fragment_dir, [_row()], create_shards=False)
    (tmp_path / "plate-14610.tar").write_bytes(b"tar-placeholder")
    assert not is_packed_index(fragment)

    wrong_type = _row()
    wrong_type["plate"] = "14610"
    invalid = _write_index(tmp_path / "invalid", [wrong_type])
    assert not is_packed_index(invalid)


def test_detection_rejects_duplicate_parquet_schema_columns(tmp_path: Path) -> None:
    names = [
        "relpath",
        "experiment",
        "experiment",
        "plate",
        "well",
        "site",
        "channel",
        "offset",
        "size",
        "shard",
    ]
    values = [
        pa.array(["source/image.tiff"]),
        pa.array(["idr0080-A549"]),
        pa.array(["shadow-experiment"]),
        pa.array([14610]),
        pa.array(["A01"]),
        pa.array([1]),
        pa.array([1]),
        pa.array([512]),
        pa.array([128]),
        pa.array(["plate-14610.tar"]),
    ]
    index = tmp_path / "duplicate.index.parquet"
    pq.write_table(pa.Table.from_arrays(values, names=names), index)
    (tmp_path / "plate-14610.tar").write_bytes(b"tar-placeholder")

    assert not is_packed_index(index)


def test_detection_fails_closed_when_schema_lookup_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "broken.index.parquet"
    index.write_bytes(b"candidate")

    class BrokenSchema:
        names = list(packed.REQUIRED_INDEX_COLUMNS)

        def field(self, name: str) -> pa.Field:
            raise KeyError(name)

    class Metadata:
        num_rows = 1

    class BrokenParquet:
        schema_arrow = BrokenSchema()
        metadata = Metadata()

    monkeypatch.setattr(packed.pq, "ParquetFile", lambda _path: BrokenParquet())
    assert not is_packed_index(index)


def test_detection_cache_is_keyed_by_path_mtime_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _write_index(tmp_path, [_row()])
    original = packed._detect_uncached
    calls = 0

    def counted(path: Path) -> bool:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(packed, "_detect_uncached", counted)
    assert is_packed_index(index)
    assert is_packed_index(index)
    assert calls == 1

    stat = index.stat()
    os.utime(index, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert is_packed_index(index)
    assert calls == 2


def test_detection_checks_only_distinct_shard_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        _row(
            channel=index % 5 + 1,
            offset=512 + index * 128,
            shard=f"plate-{14610 + index % 2}.tar",
        )
        for index in range(200)
    ]
    index = _write_index(tmp_path, rows)
    original = packed._tar_layout
    observed: list[list[str | None] | None] = []

    def recording_layout(
        path: Path, shard_values: list[str | None] | None
    ) -> tuple[Path | None, object] | None:
        observed.append(shard_values)
        return original(path, shard_values)

    monkeypatch.setattr(packed, "_tar_layout", recording_layout)
    assert is_packed_index(index)
    assert observed == [["plate-14610.tar", "plate-14611.tar"]]


def test_catalogue_groups_plate_dimensions_and_field_members(tmp_path: Path) -> None:
    rows = [
        _row(channel=1, offset=512),
        _row(channel=2, offset=1024),
        _row(well="C01", site=2, channel=1, offset=1536),
        _row(plate=14611, well="P24", channel=1, offset=512),
        _row(experiment="idr0080-ES2", plate=14613, channel=1, offset=512),
    ]
    index = _write_index(tmp_path, rows)

    catalogue = PackedCatalogueCache().get(index)

    assert catalogue.row_count == 5
    assert catalogue.experiments == ("idr0080-A549", "idr0080-ES2")
    assert catalogue.plates_for("idr0080-A549") == (14610, 14611)
    plate = catalogue.plate("idr0080-A549", 14610)
    assert plate is not None
    assert plate.wells == ("A01", "C01")
    assert plate.sites == (1, 2)
    assert plate.channels == (1, 2)

    field = catalogue.field("idr0080-A549", 14610, "A01", 1)
    assert field is not None
    assert tuple(field) == (1, 2)
    assert field[2].tar == (tmp_path / "plate-14610.tar").resolve()
    assert (field[2].offset, field[2].size) == (1024, 128)
    assert field[2].relpath.endswith("A01_s1_w2.tif")


def test_catalogue_dictionary_encodes_strings_before_arrow_grouping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _write_index(
        tmp_path,
        [
            _row(),
            _row(channel=2, offset=1024),
            _row(well="C01", site=2, offset=1536),
        ],
    )
    original = packed._group_plate_dimensions
    observed: dict[str, bool] = {}

    def inspect_grouping(table: pa.Table) -> dict[packed.PlateKey, packed.PackedPlate]:
        observed.update(
            {
                name: pa.types.is_dictionary(table[name].type)
                for name in ("experiment", "well", "shard")
            }
        )
        return original(table)

    monkeypatch.setattr(packed, "_group_plate_dimensions", inspect_grouping)
    catalogue = PackedCatalogueCache().get(index)

    assert observed == {"experiment": True, "well": True, "shard": True}
    assert catalogue.plate("idr0080-A549", 14610) == packed.PackedPlate(
        wells=("A01", "C01"),
        sites=(1, 2),
        channels=(1, 2),
    )


@pytest.mark.parametrize("failure", ["null", "duplicate"])
def test_catalogue_rejects_null_and_duplicate_keys(tmp_path: Path, failure: str) -> None:
    first = _row()
    second = _row(channel=2)
    if failure == "null":
        second["well"] = None
    else:
        second = dict(first)
    index = _write_index(tmp_path, [first, second])

    with pytest.raises(PackedIndexError, match=failure):
        PackedCatalogueCache().get(index)


@pytest.mark.parametrize(
    "experiment",
    [
        "",
        ".",
        "..",
        "idr0080/A549",
        "idr0080\\A549",
        "idr0080\x00A549",
        "D:evil",
        "NUL",
        "con.txt",
        "COM1",
    ],
)
def test_catalogue_rejects_unsafe_experiment_components(tmp_path: Path, experiment: str) -> None:
    index = _write_index(tmp_path, [_row(experiment=experiment)])

    with pytest.raises(PackedIndexError, match="unsafe experiment component"):
        PackedCatalogueCache().get(index)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("well", "../A01", "unsafe well component"),
        ("well", "D:evil", "unsafe well component"),
        ("well", "LPT9.dat", "unsafe well component"),
        ("relpath", "../outside.tiff", "unsafe relpath"),
    ],
)
def test_catalogue_rejects_unsafe_field_paths(
    tmp_path: Path, column: str, value: str, message: str
) -> None:
    row = _row()
    row[column] = value
    index = _write_index(tmp_path, [row])

    with pytest.raises(PackedIndexError, match=message):
        PackedCatalogueCache().get(index)


def test_catalogue_initialises_once_and_reloads_after_index_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _write_index(tmp_path, [_row()])
    cache = PackedCatalogueCache()
    original = packed._load_catalogue
    call_lock = threading.Lock()
    calls = 0

    def counted(path: Path, signature: packed.IndexSignature) -> packed.PackedCatalogue:
        nonlocal calls
        with call_lock:
            calls += 1
        return original(path, signature)

    monkeypatch.setattr(packed, "_load_catalogue", counted)
    with ThreadPoolExecutor(max_workers=8) as executor:
        catalogues = list(executor.map(lambda _: cache.get(index), range(16)))
    assert calls == 1
    assert all(catalogue is catalogues[0] for catalogue in catalogues)

    previous = index.stat()
    _write_index(tmp_path, [_row(), _row(channel=2, offset=1024)])
    current = index.stat()
    if (current.st_mtime_ns, current.st_size) == (previous.st_mtime_ns, previous.st_size):
        os.utime(index, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000))
    reloaded = cache.get(index)
    assert calls == 2
    assert reloaded is not catalogues[0]
    assert reloaded.row_count == 2


def test_catalogue_async_wrapper_runs_blocking_load_in_worker_thread(tmp_path: Path) -> None:
    index = _write_index(tmp_path, [_row()])
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    class RecordingCache(PackedCatalogueCache):
        def get(self, index_path: Path) -> packed.PackedCatalogue:
            worker_threads.append(threading.get_ident())
            return super().get(index_path)

    catalogue = asyncio.run(get_catalogue_in_thread(index, cache=RecordingCache()))
    assert catalogue.row_count == 1
    assert worker_threads and worker_threads[0] != caller_thread


def test_catalogue_translates_index_disappearance_during_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _write_index(tmp_path, [_row()])
    original = packed._signature
    calls = 0

    def disappearing(path: Path) -> packed.IndexSignature:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise FileNotFoundError(path)
        return original(path)

    monkeypatch.setattr(packed, "_signature", disappearing)
    with pytest.raises(PackedIndexError, match="disappeared while loading"):
        PackedCatalogueCache().get(index)


def test_virtual_paths_resolve_index_experiment_and_plate(tmp_path: Path) -> None:
    root = tmp_path / "root"
    index = _write_index(
        root,
        [
            _row(),
            _row(plate=14611),
            _row(experiment="idr0080-ES2", plate=14613),
        ],
    )
    guard = RootGuard([root])
    cache = PackedCatalogueCache()

    index_path = resolve_packed_virtual_path(str(index), guard, cache=cache)
    assert index_path is not None
    assert index_path.index_path == index.resolve()
    assert index_path.experiment is None

    experiment = resolve_packed_virtual_path(f"{index}{os.sep}idr0080-A549", guard, cache=cache)
    assert experiment is not None
    assert experiment.experiment == "idr0080-A549"
    assert experiment.plate is None

    plate = resolve_packed_virtual_path(
        f"{index}{os.sep}idr0080-A549{os.sep}14610", guard, cache=cache
    )
    assert plate is not None
    assert plate.plate == 14610
    assert plate.registry_key == os.path.join(str(index.resolve()), "idr0080-A549", "14610")

    assert resolve_packed_virtual_path(str(root / "ordinary"), guard, cache=cache) is None
    with pytest.raises(PathNotFoundError, match="experiment"):
        resolve_packed_virtual_path(f"{index}{os.sep}unknown", guard, cache=cache)
    with pytest.raises(PathNotFoundError, match="plate"):
        resolve_packed_virtual_path(f"{index}{os.sep}idr0080-A549{os.sep}99999", guard, cache=cache)
    with pytest.raises(PathForbiddenError):
        resolve_packed_virtual_path(f"{index}{os.sep}..{os.sep}idr0080-A549", guard, cache=cache)


def test_virtual_path_guard_rejects_symlinked_index_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    index = _write_index(outside, [_row()])
    link = root / index.name
    try:
        link.symlink_to(index)
    except OSError as exc:
        pytest.skip(f"Creating a file symlink is unavailable: {exc}")

    with pytest.raises(PathForbiddenError):
        resolve_packed_virtual_path(str(link), RootGuard([root]))


def test_virtual_path_checks_root_before_rejecting_a_deep_tail(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside_index = _write_index(tmp_path / "outside", [_row()])
    raw = os.path.join(str(outside_index), "idr0080-A549", "14610", "extra")

    with pytest.raises(PathForbiddenError):
        resolve_packed_virtual_path(raw, RootGuard([root]))
