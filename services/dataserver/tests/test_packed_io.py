from __future__ import annotations

import io
import logging
import os
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest
import tifffile
from numcodecs import Blosc
from timed_dataserver import packed_io, tar_io
from timed_dataserver.packed import (
    IndexSignature,
    PackedCatalogue,
    PackedMember,
    PackedPlate,
)
from timed_dataserver.packed_io import (
    DecodedField,
    PackedChunkKeyError,
    PackedFieldCache,
    PackedReader,
    PackedReadError,
)
from timed_dataserver.packed_profiles import IDR0080_FIXTURE_PROFILE, PackedCodec

EXPERIMENT = "idr0080-A549"
PLATE = 14610
WELL = "A01"


@dataclass(frozen=True)
class _Fixture:
    catalogue: PackedCatalogue
    tar: Path
    arrays: dict[tuple[int, int], np.ndarray]


def _plane(site: int, channel: int, *, shape: tuple[int, int] = (64, 64)) -> np.ndarray:
    values = np.arange(shape[0] * shape[1], dtype=np.uint16).reshape(shape)
    return values + np.uint16(site * 1000 + channel * 100)


def _tiff_bytes(array: np.ndarray) -> bytes:
    output = io.BytesIO()
    tifffile.imwrite(output, array, photometric="minisblack", compression=None, metadata=None)
    return output.getvalue()


def _make_fixture(
    tmp_path: Path,
    *,
    sites: tuple[int, ...] = (1,),
    noncontiguous: bool = False,
    arrays: dict[tuple[int, int], np.ndarray] | None = None,
    invalid_payloads: frozenset[tuple[int, int]] = frozenset(),
    actual_names: dict[tuple[int, int], str] | None = None,
) -> _Fixture:
    tar_path = tmp_path / "packed.tar"
    expected_arrays: dict[tuple[int, int], np.ndarray] = {}
    records: list[tuple[int, int, str, str]] = []
    with tarfile.open(tar_path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for site in sites:
            for channel in range(1, 6):
                key = (site, channel)
                array = (arrays or {}).get(key, _plane(site, channel))
                expected_arrays[key] = array
                relpath = f"source/SQ000{PLATE}/Images/{WELL}_s{site}_ch{channel}.tiff"
                expected_name = f"idr0080/raw/{relpath}"
                actual_name = (actual_names or {}).get(key, expected_name)
                payload = b"not-a-tiff" if key in invalid_payloads else _tiff_bytes(array)
                member = tarfile.TarInfo(actual_name)
                member.size = len(payload)
                member.mtime = 0
                member.mode = 0o444
                archive.addfile(member, io.BytesIO(payload))
                records.append((site, channel, relpath, actual_name))
                if noncontiguous and channel < 5:
                    filler_payload = f"filler-{site}-{channel}".encode()
                    filler = tarfile.TarInfo(f"filler/{site}-{channel}.bin")
                    filler.size = len(filler_payload)
                    filler.mtime = 0
                    archive.addfile(filler, io.BytesIO(filler_payload))

    with tarfile.open(tar_path, "r:") as archive:
        tar_members = {member.name: member for member in archive.getmembers()}

    fields: dict[tuple[str, int, str, int], MappingProxyType[int, PackedMember]] = {}
    for site in sites:
        channels: dict[int, PackedMember] = {}
        for record_site, channel, relpath, actual_name in records:
            if record_site != site:
                continue
            info = tar_members[actual_name]
            channels[channel] = PackedMember(
                tar=tar_path.resolve(),
                offset=info.offset_data,
                size=info.size,
                relpath=relpath,
            )
        fields[(EXPERIMENT, PLATE, WELL, site)] = MappingProxyType(channels)

    index = tmp_path / "fixture.index.parquet"
    index.write_bytes(b"index-signature")
    index_stat = index.stat()
    catalogue = PackedCatalogue(
        index_path=index.resolve(),
        signature=IndexSignature(index_stat.st_mtime_ns, index_stat.st_size),
        row_count=len(sites) * 5,
        plates=MappingProxyType(
            {
                (EXPERIMENT, PLATE): PackedPlate(
                    wells=(WELL,),
                    sites=sites,
                    channels=(1, 2, 3, 4, 5),
                )
            }
        ),
        fields=MappingProxyType(fields),
    )
    return _Fixture(catalogue=catalogue, tar=tar_path.resolve(), arrays=expected_arrays)


def _replace_field_member(
    fixture: _Fixture,
    channel: int,
    **changes: object,
) -> PackedCatalogue:
    key = (EXPERIMENT, PLATE, WELL, 1)
    members = dict(fixture.catalogue.fields[key])
    members[channel] = replace(members[channel], **changes)
    fields = dict(fixture.catalogue.fields)
    fields[key] = MappingProxyType(members)
    return replace(fixture.catalogue, fields=MappingProxyType(fields))


def test_contiguous_field_uses_one_read_then_hits_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    reader = PackedReader()
    original = reader._read_exact_at
    reads: list[tuple[int, int]] = []

    def tracked(descriptor: object, offset: int, size: int) -> bytes:
        reads.append((offset, size))
        return original(descriptor, offset, size)  # type: ignore[arg-type]

    monkeypatch.setattr(reader, "_read_exact_at", tracked)
    field, status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    assert status == "miss"
    assert len(reads) == 1
    for channel, plane in zip(field.channels, field.planes, strict=True):
        np.testing.assert_array_equal(plane, fixture.arrays[(1, channel)])
        assert not plane.flags.writeable
    with pytest.raises(ValueError):
        field.planes[0][0, 0] = 0

    repeated, status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    assert status == "hit"
    assert repeated is field
    assert len(reads) == 1
    reader.close()


def test_profile_dtype_controls_generic_plane_and_chunk_validation(tmp_path: Path) -> None:
    arrays = {
        (1, channel): np.full((64, 64), channel * 10, dtype=np.uint8)
        for channel in range(1, 6)
    }
    fixture = _make_fixture(tmp_path, arrays=arrays)
    profile = replace(
        IDR0080_FIXTURE_PROFILE,
        dtype="uint8",
        codec=replace(IDR0080_FIXTURE_PROFILE.codec, typesize=1),
    )

    with PackedReader() as reader:
        field, _status = reader.get_field(
            fixture.catalogue,
            profile,
            EXPERIMENT,
            PLATE,
            WELL,
            1,
        )
        chunk = reader.get_chunk(
            fixture.catalogue,
            profile,
            EXPERIMENT,
            PLATE,
            WELL,
            1,
            1,
            0,
            0,
        )

    assert all(plane.dtype == np.dtype("uint8") for plane in field.planes)
    decoded = Blosc().decode(chunk.content)
    values = np.frombuffer(decoded, dtype=np.uint8).reshape((1, 32, 32))
    assert np.all(values == 10)


def test_reader_rejects_an_oversized_decoded_field_before_io(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    profile = replace(
        IDR0080_FIXTURE_PROFILE,
        geometry=replace(
            IDR0080_FIXTURE_PROFILE.geometry,
            image_shape=(packed_io.MAX_PACKED_MEMBER_BYTES, 1),
        ),
        dtype="uint8",
        codec=replace(IDR0080_FIXTURE_PROFILE.codec, typesize=1),
    )

    with PackedReader() as reader, pytest.raises(PackedReadError, match="field exceeds"):
        reader.get_field(fixture.catalogue, profile, EXPERIMENT, PLATE, WELL, 1)


def test_field_source_resolution_retries_a_mixed_tar_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    reader = PackedReader()
    original = reader._member_source
    calls = 0

    def mixed_once(channel: int, member: PackedMember) -> object:
        nonlocal calls
        calls += 1
        source = original(channel, member)
        if calls == 1:
            return replace(
                source,
                revision=replace(source.revision, mtime_ns=source.revision.mtime_ns - 1),
            )
        return source

    monkeypatch.setattr(reader, "_member_source", mixed_once)
    field, status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )

    assert status == "miss"
    assert calls == 10
    assert len(set(field.revisions)) == 1
    for channel, plane in zip(field.channels, field.planes, strict=True):
        np.testing.assert_array_equal(plane, fixture.arrays[(1, channel)])
    reader.close()


def test_chunk_slice_blosc_etag_and_cache_status(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    with PackedReader() as reader:
        first = reader.get_chunk(
            fixture.catalogue,
            IDR0080_FIXTURE_PROFILE,
            EXPERIMENT,
            PLATE,
            WELL,
            1,
            1,
            0,
            1,
        )
        repeated = reader.get_chunk(
            fixture.catalogue,
            IDR0080_FIXTURE_PROFILE,
            EXPERIMENT,
            PLATE,
            WELL,
            1,
            1,
            0,
            1,
        )
        other = reader.get_chunk(
            fixture.catalogue,
            IDR0080_FIXTURE_PROFILE,
            EXPERIMENT,
            PLATE,
            WELL,
            1,
            1,
            1,
            1,
        )

        decoded = Blosc().decode(first.content)
        chunk = np.frombuffer(decoded, dtype="<u2").reshape(1, 32, 32)
        np.testing.assert_array_equal(chunk[0], fixture.arrays[(1, 1)][:32, 32:64])
        assert first.cache_status == "miss"
        assert repeated.cache_status == "hit"
        assert first.etag == repeated.etag
        assert first.etag.startswith('"') and first.etag.endswith('"')
        assert len(first.etag) == 66
        assert other.etag != first.etag

        for coordinates in ((0, 0, 0), (6, 0, 0), (1, -1, 0), (1, 2, 0)):
            with pytest.raises(PackedChunkKeyError):
                reader.get_chunk(
                    fixture.catalogue,
                    IDR0080_FIXTURE_PROFILE,
                    EXPERIMENT,
                    PLATE,
                    WELL,
                    1,
                    *coordinates,
                )


def test_chunk_codec_is_constructed_from_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    profile = replace(
        IDR0080_FIXTURE_PROFILE,
        codec=PackedCodec(
            cname="zstd",
            clevel=1,
            shuffle="noshuffle",
            blocksize=0,
            typesize=2,
        ),
    )
    configurations: list[dict[str, object]] = []

    class TrackingBlosc:
        NOSHUFFLE = Blosc.NOSHUFFLE
        SHUFFLE = Blosc.SHUFFLE
        BITSHUFFLE = Blosc.BITSHUFFLE

        def __new__(cls, **configuration: object) -> Blosc:
            configurations.append(configuration)
            return Blosc(**configuration)

    monkeypatch.setattr(packed_io, "Blosc", TrackingBlosc)
    with PackedReader() as reader:
        result = reader.get_chunk(
            fixture.catalogue,
            profile,
            EXPERIMENT,
            PLATE,
            WELL,
            1,
            1,
            0,
            0,
        )

    assert configurations == [
        {
            "cname": "zstd",
            "clevel": 1,
            "shuffle": Blosc.NOSHUFFLE,
            "blocksize": 0,
            "typesize": 2,
        }
    ]
    decoded = Blosc().decode(result.content)
    chunk = np.frombuffer(decoded, dtype="<u2").reshape(1, 32, 32)
    np.testing.assert_array_equal(chunk[0], fixture.arrays[(1, 1)][:32, :32])


def test_exact_positional_reads_loop_and_report_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    reader = PackedReader()
    descriptor = reader._descriptor(fixture.tar)
    expected = fixture.tar.read_bytes()[:23]
    calls = 0

    def short_pread(_fd: int, size: int, offset: int) -> bytes:
        nonlocal calls
        calls += 1
        return expected[offset : offset + min(size, 3)]

    monkeypatch.setattr(tar_io, "_OS_PREAD", short_pread)
    assert reader._read_exact_at(descriptor, 0, len(expected)) == expected
    assert calls > 1

    with pytest.raises(PackedReadError, match="Short packed read"):
        reader._read_exact_at(descriptor, 0, len(expected) + 1)

    monkeypatch.setattr(tar_io, "_OS_PREAD", None)
    assert reader._read_exact_at(descriptor, 0, len(expected)) == expected
    reader.close()


@pytest.mark.parametrize("failure", ["offset", "size", "oversized", "end"])
def test_member_bounds_are_validated_before_io(tmp_path: Path, failure: str) -> None:
    fixture = _make_fixture(tmp_path)
    member = fixture.catalogue.field(EXPERIMENT, PLATE, WELL, 1)[1]  # type: ignore[index]
    if failure == "offset":
        catalogue = _replace_field_member(fixture, 1, offset=511)
        message = "offset"
    elif failure == "size":
        catalogue = _replace_field_member(fixture, 1, size=0)
        message = "size"
    elif failure == "oversized":
        catalogue = _replace_field_member(
            fixture,
            1,
            size=packed_io.MAX_PACKED_MEMBER_BYTES + 1,
        )
        message = "limit"
    else:
        catalogue = _replace_field_member(
            fixture,
            1,
            offset=fixture.tar.stat().st_size - 1,
            size=member.size,
        )
        message = "bounds"

    with PackedReader() as reader, pytest.raises(PackedReadError, match=message):
        reader.get_field(catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)


def test_aggregate_encoded_field_size_is_rejected_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_fixture(tmp_path)
    key = (EXPERIMENT, PLATE, WELL, 1)
    members = {
        channel: replace(member, size=packed_io.MAX_PACKED_MEMBER_BYTES)
        for channel, member in fixture.catalogue.fields[key].items()
    }
    fields = dict(fixture.catalogue.fields)
    fields[key] = MappingProxyType(members)
    catalogue = replace(fixture.catalogue, fields=MappingProxyType(fields))

    with PackedReader() as reader:
        monkeypatch.setattr(
            reader,
            "_field_sources",
            lambda *_args: pytest.fail("aggregate limit must be checked before source I/O"),
        )
        with pytest.raises(PackedReadError, match="encoded limit"):
            reader.get_field(
                catalogue,
                IDR0080_FIXTURE_PROFILE,
                EXPERIMENT,
                PLATE,
                WELL,
                1,
            )


@pytest.mark.parametrize("failure", ["name", "size", "magic", "checksum"])
def test_header_mismatch_refuses_tar_until_restart(
    tmp_path: Path,
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    actual_names = None
    if failure == "name":
        actual_names = {(1, 1): "idr0080/raw/wrong-name.tiff"}
    fixture = _make_fixture(tmp_path, actual_names=actual_names)
    catalogue = fixture.catalogue
    first = catalogue.field(EXPERIMENT, PLATE, WELL, 1)[1]  # type: ignore[index]
    if failure == "size":
        catalogue = _replace_field_member(fixture, 1, size=first.size - 1)
    elif failure in {"magic", "checksum"}:
        with fixture.tar.open("r+b") as handle:
            if failure == "magic":
                handle.seek(first.offset - 512 + 257)
                handle.write(b"broken!!")
            else:
                handle.seek(first.offset - 512)
                original = handle.read(1)
                handle.seek(first.offset - 512)
                handle.write(bytes([original[0] ^ 1]))

    reader = PackedReader()
    with caplog.at_level(logging.ERROR), pytest.raises(PackedReadError, match="refused"):
        reader.get_field(catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    assert reader.refused_tars == (fixture.tar,)
    assert "Refusing packed tar" in caplog.text
    with pytest.raises(PackedReadError, match="refused until restart"):
        reader.get_field(catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    reader.close()


def test_decode_failure_forces_header_reverification_without_poisoning_tar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path, invalid_payloads=frozenset({(1, 2)}))
    reader = PackedReader()
    original = reader._ensure_header
    calls: list[tuple[int, bool]] = []

    def tracked(
        source: object,
        profile: object,
        *,
        force: bool = False,
    ) -> None:
        calls.append((source.channel, force))  # type: ignore[attr-defined]
        original(source, profile, force=force)  # type: ignore[arg-type]

    monkeypatch.setattr(reader, "_ensure_header", tracked)
    with pytest.raises(PackedReadError, match="decode packed TIFF"):
        reader.get_field(fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    assert (2, True) in calls
    assert reader.refused_tars == ()
    assert reader.cache.entry_count == 0
    reader.close()


@pytest.mark.parametrize("failure", ["shape", "dtype"])
def test_decode_contract_failures_are_not_cached_or_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    bad = (
        _plane(1, 2, shape=(32, 64))
        if failure == "shape"
        else np.asarray(_plane(1, 2) % 255, dtype=np.uint8)
    )
    fixture = _make_fixture(tmp_path, arrays={(1, 2): bad})
    reader = PackedReader()
    original = reader._load_field
    loads = 0

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal loads
        loads += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(reader, "_load_field", counted)
    for _ in range(2):
        with pytest.raises(PackedReadError, match=failure):
            reader.get_field(fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    assert loads == 2
    assert reader.cache.entry_count == 0
    assert reader.refused_tars == ()
    reader.close()


@pytest.mark.parametrize("failure", ["compressed", "multipage"])
def test_decode_rejects_compressed_or_multipage_tiffs(
    tmp_path: Path,
    failure: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    member = fixture.catalogue.field(EXPERIMENT, PLATE, WELL, 1)[1]  # type: ignore[index]
    output = io.BytesIO()
    plane = _plane(1, 1)
    if failure == "compressed":
        tifffile.imwrite(
            output,
            plane,
            photometric="minisblack",
            compression="deflate",
            metadata=None,
        )
        message = "uncompressed"
    else:
        with tifffile.TiffWriter(output) as writer:
            writer.write(plane, photometric="minisblack", compression=None, metadata=None)
            writer.write(plane, photometric="minisblack", compression=None, metadata=None)
        message = "single-page"

    with PackedReader() as reader:
        source = reader._member_source(1, member)
        with pytest.raises(PackedReadError, match=message):
            reader._decode_plane(output.getvalue(), source, IDR0080_FIXTURE_PROFILE)


def test_changed_index_mapping_rechecks_the_tar_header(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    reader = PackedReader()
    reader.get_field(fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)

    key = (EXPERIMENT, PLATE, WELL, 1)
    members = dict(fixture.catalogue.fields[key])
    members[1] = replace(members[1], relpath="wrong-member.tiff")
    fields = dict(fixture.catalogue.fields)
    fields[key] = MappingProxyType(members)
    changed = replace(
        fixture.catalogue,
        signature=IndexSignature(
            fixture.catalogue.signature.mtime_ns + 1,
            fixture.catalogue.signature.size,
        ),
        fields=MappingProxyType(fields),
    )

    with pytest.raises(PackedReadError, match="refused"):
        reader.get_field(changed, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    assert reader.refused_tars == (fixture.tar,)
    reader.close()


def test_transient_header_read_failure_does_not_refuse_tar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_fixture(tmp_path, noncontiguous=True)
    reader = PackedReader()
    original = reader._read_exact_at
    fail_header = True

    def flaky(descriptor: object, offset: int, size: int) -> bytes:
        nonlocal fail_header
        if size == 512 and fail_header:
            fail_header = False
            raise PackedReadError("transient header read")
        return original(descriptor, offset, size)  # type: ignore[arg-type]

    monkeypatch.setattr(reader, "_read_exact_at", flaky)
    with pytest.raises(PackedReadError, match="transient header read"):
        reader.get_field(fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    assert reader.refused_tars == ()

    field, status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    assert status == "miss"
    np.testing.assert_array_equal(field.planes[0], fixture.arrays[(1, 1)])
    reader.close()


def test_byte_lru_evicts_oldest_complete_field(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, sites=(1, 2))
    field_bytes = 5 * 64 * 64 * np.dtype("uint16").itemsize
    with PackedReader(cache_bytes=field_bytes) as reader:
        _, first_status = reader.get_field(
            fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
        )
        _, second_status = reader.get_field(
            fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 2
        )
        _, reloaded_status = reader.get_field(
            fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
        )
        assert (first_status, second_status, reloaded_status) == ("miss", "miss", "miss")
        assert reader.cache.entry_count == 1
        assert reader.cache.current_bytes == field_bytes


def test_zero_budget_retains_nothing(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    with PackedReader(cache_bytes=0) as reader:
        first, first_status = reader.get_field(
            fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
        )
        second, second_status = reader.get_field(
            fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
        )
        assert (first_status, second_status) == ("miss", "miss")
        assert first is not second
        assert reader.cache.entry_count == 0
        assert reader.cache.current_bytes == 0
        np.testing.assert_array_equal(first.planes[0], second.planes[0])


def test_follower_receives_the_leader_exception() -> None:
    cache = PackedFieldCache()
    leader_started = threading.Event()
    release_leader = threading.Event()
    follower_loads = 0

    def failing_load() -> DecodedField:
        leader_started.set()
        assert release_leader.wait(5)
        raise PackedReadError("fixture leader failure")

    def follower_load() -> DecodedField:
        nonlocal follower_loads
        follower_loads += 1
        raise AssertionError("a follower must not start its own load")

    with ThreadPoolExecutor(max_workers=2) as executor:
        leader = executor.submit(cache.get_or_load, "field", failing_load)
        assert leader_started.wait(5)
        follower = executor.submit(cache.get_or_load, "field", follower_load)
        time.sleep(0.2)
        release_leader.set()
        for future in (leader, follower):
            with pytest.raises(PackedReadError, match="fixture leader failure"):
                future.result(timeout=5)

    assert follower_loads == 0
    assert cache.entry_count == 0
    assert cache.current_bytes == 0


def test_field_load_is_single_flight_across_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    reader = PackedReader()
    original = reader._load_field
    lock = threading.Lock()
    loads = 0

    def slow(*args: object, **kwargs: object) -> object:
        nonlocal loads
        with lock:
            loads += 1
        time.sleep(0.08)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(reader, "_load_field", slow)

    def get() -> tuple[object, str]:
        return reader.get_field(
            fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: get(), range(8)))
    assert loads == 1
    assert sum(status == "miss" for _, status in results) == 1
    assert len({id(field) for field, _ in results}) == 1
    reader.close()


def test_distinct_field_loads_are_bounded_with_transient_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_fixture(tmp_path, sites=(1, 2, 3, 4, 5, 6))
    reader = PackedReader(max_io=2)
    original = reader._read_field_payloads
    lock = threading.Lock()
    active = 0
    maximum = 0

    def tracked(*args: object, **kwargs: object) -> object:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.03)
            return original(*args, **kwargs)  # type: ignore[arg-type]
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(reader, "_read_field_payloads", tracked)
    with ThreadPoolExecutor(max_workers=6) as executor:
        fields = list(
            executor.map(
                lambda site: reader.get_field(
                    fixture.catalogue,
                    IDR0080_FIXTURE_PROFILE,
                    EXPERIMENT,
                    PLATE,
                    WELL,
                    site,
                ),
                range(1, 7),
            )
        )

    assert len(fields) == 6
    assert maximum == 2
    reader.close()


def test_failed_single_flight_load_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _make_fixture(tmp_path)
    reader = PackedReader()
    original = reader._load_field
    attempts = 0

    def flaky(*args: object, **kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PackedReadError("transient failure")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(reader, "_load_field", flaky)
    with pytest.raises(PackedReadError, match="transient"):
        reader.get_field(fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    _, status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    assert status == "miss"
    assert attempts == 2
    reader.close()


def test_noncontiguous_fallback_is_concurrent_and_bounded_to_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path, noncontiguous=True)
    reader = PackedReader(max_io=4)
    original = reader._read_exact_at_unlimited
    lock = threading.Lock()
    active = 0
    maximum = 0
    calls = 0

    def tracked(descriptor: object, offset: int, size: int) -> bytes:
        nonlocal active, maximum, calls
        with lock:
            active += 1
            calls += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        try:
            return original(descriptor, offset, size)  # type: ignore[arg-type]
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(reader, "_read_exact_at_unlimited", tracked)
    field, status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    assert status == "miss"
    assert 2 <= maximum <= 4
    assert calls >= 6
    np.testing.assert_array_equal(field.planes[4], fixture.arrays[(1, 5)])
    reader.close()


def test_descriptors_are_cached_read_only_and_close_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    original_open = packed_io.os.open
    opens: list[tuple[Path, int]] = []

    def tracked_open(path: Path, flags: int) -> int:
        opens.append((Path(path), flags))
        return original_open(path, flags)

    monkeypatch.setattr(packed_io.os, "open", tracked_open)
    reader = PackedReader()
    reader.get_field(fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    reader.get_field(fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)
    assert len(opens) == 1
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    assert opens[0][1] & write_flags == 0
    if hasattr(os, "O_NOFOLLOW"):
        assert opens[0][1] & os.O_NOFOLLOW
    descriptor = next(iter(reader._descriptors.values()))
    reader.close()
    reader.close()
    assert reader.descriptor_count == 0
    with pytest.raises(OSError):
        os.fstat(descriptor.fd)
    with pytest.raises(PackedReadError, match="closed"):
        reader.get_field(fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1)


def test_in_place_tar_growth_reuses_descriptor_and_invalidates_field_cache(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    reader = PackedReader()
    first, first_status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    descriptor = next(iter(reader._descriptors.values()))
    old_size = first.revisions[0].size

    with fixture.tar.open("ab") as handle:
        handle.write(bytes(512))

    second, second_status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    assert (first_status, second_status) == ("miss", "miss")
    assert next(iter(reader._descriptors.values())) is descriptor
    assert second.revisions[0].size == old_size + 512
    np.testing.assert_array_equal(second.planes[0], first.planes[0])
    reader.close()


@pytest.mark.posix_only
def test_atomic_tar_replacement_reopens_and_does_not_serve_stale_field(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    replacement_root = tmp_path / "replacement"
    replacement_root.mkdir()
    replacement_arrays = {
        (1, channel): np.full((64, 64), 10_000 + channel, dtype=np.uint16)
        for channel in range(1, 6)
    }
    replacement = _make_fixture(replacement_root, arrays=replacement_arrays)
    old_members = fixture.catalogue.field(EXPERIMENT, PLATE, WELL, 1)
    new_members = replacement.catalogue.field(EXPERIMENT, PLATE, WELL, 1)
    assert old_members is not None and new_members is not None
    assert [(member.offset, member.size, member.relpath) for member in old_members.values()] == [
        (member.offset, member.size, member.relpath) for member in new_members.values()
    ]

    reader = PackedReader()
    original, original_status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    original_descriptor = next(iter(reader._descriptors.values()))
    try:
        os.replace(replacement.tar, fixture.tar)
    except PermissionError:
        reader.close()
        pytest.skip("This platform does not allow replacing a file while its descriptor is open")

    current, current_status = reader.get_field(
        fixture.catalogue, IDR0080_FIXTURE_PROFILE, EXPERIMENT, PLATE, WELL, 1
    )
    current_descriptor = next(iter(reader._descriptors.values()))
    assert (original_status, current_status) == ("miss", "miss")
    assert current_descriptor is not original_descriptor
    assert current.revisions[0] != original.revisions[0]
    for channel, plane in zip(current.channels, current.planes, strict=True):
        np.testing.assert_array_equal(plane, replacement_arrays[(1, channel)])
    reader.close()
