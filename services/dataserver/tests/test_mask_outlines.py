"""Outlines are label changes between 4-neighbours, thickened and encoded as 1-bit PNGs."""

from __future__ import annotations

import numpy as np
import pytest
from png_reader import read_png
from timed_dataserver.mask_outlines import outline_mask, outline_png


def reference_outline(labels: np.ndarray, width: int) -> np.ndarray:
    """The contract's definition, pixel by pixel, over an edge-padded copy."""
    height, columns = labels.shape
    padded = np.pad(labels, 1, mode="edge")
    boundary = np.zeros(labels.shape, dtype=bool)
    for y in range(height):
        for x in range(columns):
            value = padded[y + 1, x + 1]
            neighbours = (
                padded[y, x + 1],
                padded[y + 2, x + 1],
                padded[y + 1, x],
                padded[y + 1, x + 2],
            )
            boundary[y, x] = value != 0 and any(other != value for other in neighbours)
    reach = width - 1
    thick = np.zeros(labels.shape, dtype=bool)
    for y, x in zip(*np.nonzero(boundary), strict=True):
        thick[max(0, y - reach) : y + reach + 1, max(0, x - reach) : x + reach + 1] = True
    return thick


def _square() -> np.ndarray:
    labels = np.zeros((12, 12), dtype=np.uint16)
    labels[3:9, 3:9] = 1
    return labels


def test_a_square_is_outlined_by_its_inner_ring() -> None:
    expected = np.zeros((12, 12), dtype=bool)
    expected[3:9, 3:9] = True
    expected[4:8, 4:8] = False
    assert np.array_equal(outline_mask(_square(), 1), expected)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_width_dilates_the_outline_by_width_minus_one_pixels(width: int) -> None:
    ring = outline_mask(_square(), 1)
    expected = np.zeros_like(ring)
    reach = width - 1
    for y, x in zip(*np.nonzero(ring), strict=True):
        expected[max(0, y - reach) : y + reach + 1, max(0, x - reach) : x + reach + 1] = True
    assert np.array_equal(outline_mask(_square(), width), expected)


def test_a_disc_and_touching_labels_follow_the_reference() -> None:
    y, x = np.indices((40, 40))
    labels = np.zeros((40, 40), dtype=np.uint16)
    labels[(y - 20) ** 2 + (x - 14) ** 2 <= 64] = 1
    labels[10:30, 22:34] = 2
    for width in (1, 2, 3, 4):
        outline = outline_mask(labels, width)
        assert np.array_equal(outline, reference_outline(labels, width))
    touching = outline_mask(labels, 1)
    # Where label 2 meets label 1, both sides of the change are outline pixels.
    assert touching[20, 21] and touching[20, 22]
    assert labels[20, 21] == 1 and labels[20, 22] == 2


def test_the_field_edge_is_not_an_outline() -> None:
    labels = np.zeros((10, 10), dtype=np.uint16)
    labels[:, :5] = 1
    outline = outline_mask(labels, 1)
    assert not outline[0, 1:4].any() and not outline[9, 1:4].any()
    assert not outline[1:9, 0].any()
    assert outline[:, 4].all()
    assert not outline_mask(np.zeros((6, 6), dtype=np.uint16), 4).any()
    assert not outline_mask(np.ones((6, 6), dtype=np.uint16), 4).any()


def test_random_labels_match_the_reference_at_every_width() -> None:
    generator = np.random.default_rng(80)
    for _trial in range(5):
        labels = generator.integers(0, 4, size=(17, 23), dtype=np.uint16)
        labels[generator.random(labels.shape) < 0.5] = 0
        for width in (1, 2, 3, 4):
            assert np.array_equal(outline_mask(labels, width), reference_outline(labels, width))


@pytest.mark.parametrize("width", [0, 5, -1])
def test_widths_outside_one_to_four_are_refused(width: int) -> None:
    with pytest.raises(ValueError, match="Outline width must be 1 to 4"):
        outline_mask(_square(), width)
    with pytest.raises(ValueError, match="2-D"):
        outline_mask(np.zeros((2, 3, 4), dtype=np.uint16), 1)


def test_the_png_is_a_one_bit_palette_image_with_a_transparent_background() -> None:
    mask = outline_mask(_square(), 2)
    png = read_png(outline_png(mask, (255, 212, 0)))
    assert png.chunk_names == (b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND")
    assert (png.width, png.height, png.bit_depth, png.color_type, png.interlace) == (
        12,
        12,
        1,
        3,
        0,
    )
    assert png.palette == bytes((255, 212, 0, 255, 212, 0))
    assert png.transparency == bytes((0, 255))
    assert np.array_equal(png.pixels, mask)


@pytest.mark.parametrize("shape", [(1, 1), (3, 9), (64, 64), (7, 17)])
def test_rows_are_packed_with_padding_bits(shape: tuple[int, int]) -> None:
    generator = np.random.default_rng(shape[0] * 100 + shape[1])
    mask = generator.random(shape) < 0.3
    png = read_png(outline_png(mask, (1, 2, 3)))
    assert (png.height, png.width) == shape
    assert np.array_equal(png.pixels, mask)


def test_png_input_must_be_a_two_dimensional_boolean_mask() -> None:
    with pytest.raises(ValueError, match="boolean"):
        outline_png(np.zeros((4, 4), dtype=np.uint8), (0, 0, 0))
