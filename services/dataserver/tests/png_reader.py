"""A minimal reader for the data server's 1-bit palette outline PNGs."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Png:
    width: int
    height: int
    bit_depth: int
    color_type: int
    interlace: int
    palette: bytes
    transparency: bytes
    pixels: np.ndarray
    chunk_names: tuple[bytes, ...]


def read_png(content: bytes) -> Png:
    """Parse a non-interlaced, filter-0, palette PNG and unpack its pixels."""
    assert content[:8] == b"\x89PNG\r\n\x1a\n"
    position = 8
    chunks: list[tuple[bytes, bytes]] = []
    while position < len(content):
        (length,) = struct.unpack(">I", content[position : position + 4])
        kind = content[position + 4 : position + 8]
        data = content[position + 8 : position + 8 + length]
        (checksum,) = struct.unpack(">I", content[position + 8 + length : position + 12 + length])
        assert checksum == zlib.crc32(kind + data) & 0xFFFFFFFF, kind
        chunks.append((kind, data))
        position += 12 + length
    names = tuple(kind for kind, _data in chunks)
    values = dict(chunks)
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", values[b"IHDR"]
    )
    assert (compression, filtering) == (0, 0)
    raw = zlib.decompress(b"".join(data for kind, data in chunks if kind == b"IDAT"))
    stride = (width * bit_depth + 7) // 8 + 1
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(height, stride)
    assert not rows[:, 0].any(), "every scanline uses filter 0"
    pixels = np.unpackbits(rows[:, 1:], axis=1)[:, :width].astype(bool)
    return Png(
        width=width,
        height=height,
        bit_depth=bit_depth,
        color_type=color_type,
        interlace=interlace,
        palette=values[b"PLTE"],
        transparency=values[b"tRNS"],
        pixels=pixels,
        chunk_names=names,
    )
