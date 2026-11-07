"""Outline images of segmentation labels, encoded as 1-bit palette PNGs.

An outline pixel is a non-zero label that differs from any of its four
neighbours. The edges are padded by repeating the border, so the field's own
edge is not drawn. A width from 1 to 4 dilates the outline by width minus one
pixels in every direction, using numpy shifts rather than SciPy.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

MIN_OUTLINE_WIDTH = 1
MAX_OUTLINE_WIDTH = 4
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_COMPRESSION_LEVEL = 1


def outline_mask(labels: np.ndarray, width: int) -> np.ndarray:
    """Boolean outline of a 2-D label image, thickened to ``width`` pixels.

    Comparing each pixel with its next neighbour along both axes marks both
    sides of every label change, which equals comparing every pixel with its
    four neighbours over an edge-padded copy, without making that copy.
    """
    if labels.ndim != 2:
        raise ValueError("Outlines need a 2-D label image")
    if not MIN_OUTLINE_WIDTH <= width <= MAX_OUTLINE_WIDTH:
        raise ValueError(f"Outline width must be {MIN_OUTLINE_WIDTH} to {MAX_OUTLINE_WIDTH}")
    boundary = np.zeros(labels.shape, dtype=bool)
    vertical = labels[1:, :] != labels[:-1, :]
    boundary[1:, :] |= vertical
    boundary[:-1, :] |= vertical
    horizontal = labels[:, 1:] != labels[:, :-1]
    boundary[:, 1:] |= horizontal
    boundary[:, :-1] |= horizontal
    boundary &= labels != 0
    return _dilate(boundary, width - 1)


def _dilate(mask: np.ndarray, steps: int) -> np.ndarray:
    """Dilate by a square of side ``2 * steps + 1``, one axis at a time."""
    if steps == 0:
        return mask
    rows = mask.copy()
    for shift in range(1, steps + 1):
        rows[:, shift:] |= mask[:, :-shift]
        rows[:, :-shift] |= mask[:, shift:]
    dilated = rows.copy()
    for shift in range(1, steps + 1):
        dilated[shift:, :] |= rows[:-shift, :]
        dilated[:-shift, :] |= rows[shift:, :]
    return dilated


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def outline_png(mask: np.ndarray, color: tuple[int, int, int]) -> bytes:
    """A 1-bit indexed PNG: index 0 transparent, index 1 the outline colour.

    Both palette entries hold the outline colour, so texture filtering blends an
    outline towards transparent rather than towards transparent black.
    """
    if mask.ndim != 2 or mask.dtype != np.bool_:
        raise ValueError("Outline PNGs need a 2-D boolean mask")
    height, width = mask.shape
    packed = np.packbits(mask, axis=1)
    scanlines = np.zeros((height, packed.shape[1] + 1), dtype=np.uint8)
    scanlines[:, 1:] = packed
    header = struct.pack(">IIBBBBB", width, height, 1, 3, 0, 0, 0)
    palette = bytes((*color, *color))
    transparency = bytes((0, 255))
    image = zlib.compress(scanlines.tobytes(), _PNG_COMPRESSION_LEVEL)
    return b"".join(
        (
            _PNG_SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"PLTE", palette),
            _chunk(b"tRNS", transparency),
            _chunk(b"IDAT", image),
            _chunk(b"IEND", b""),
        )
    )
