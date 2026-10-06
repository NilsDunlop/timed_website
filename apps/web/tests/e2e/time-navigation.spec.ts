import { expect, test } from "@playwright/test";

import {
  moveTimeIndex,
  prefetchTimeIndex,
  timeIntervalHours,
  timeJumpOptions,
} from "../../src/lib/timeNavigation";

test("aligns one-based timepoints to the selected jump", () => {
  expect(moveTimeIndex(0, 1, 8, 48)).toBe(7);
  expect(moveTimeIndex(7, 1, 8, 48)).toBe(15);
  expect(moveTimeIndex(8, 1, 8, 48)).toBe(15);
  expect(moveTimeIndex(8, -1, 8, 48)).toBe(7);
  expect(moveTimeIndex(44, 1, 8, 48)).toBe(47);
  expect(moveTimeIndex(3, -1, 8, 48)).toBe(0);
});

test("prefetches the jump destination and reverses at an endpoint", () => {
  expect(prefetchTimeIndex(0, 1, 8, 48)).toBe(7);
  expect(prefetchTimeIndex(7, 1, 8, 48)).toBe(15);
  expect(prefetchTimeIndex(44, 1, 8, 48)).toBe(47);
  expect(prefetchTimeIndex(47, 1, 8, 48)).toBe(39);
  expect(prefetchTimeIndex(0, -1, 8, 48)).toBe(7);
  expect(prefetchTimeIndex(0, 1, 1, 1)).toBeNull();
});

test("offers preferred hourly jumps that land on real frames", () => {
  expect(timeJumpOptions(48, 1)).toEqual([
    { frames: 1, label: "1 h" },
    { frames: 2, label: "2 h" },
    { frames: 4, label: "4 h" },
    { frames: 6, label: "6 h" },
    { frames: 8, label: "8 h" },
    { frames: 12, label: "12 h" },
    { frames: 24, label: "24 h" },
  ]);
  expect(timeJumpOptions(48, 2)).toEqual([
    { frames: 1, label: "2 h" },
    { frames: 2, label: "4 h" },
    { frames: 3, label: "6 h" },
    { frames: 4, label: "8 h" },
    { frames: 6, label: "12 h" },
    { frames: 12, label: "24 h" },
  ]);
  expect(timeJumpOptions(6, null)).toEqual([
    { frames: 1, label: "1 frame" },
    { frames: 2, label: "2 frames" },
    { frames: 4, label: "4 frames" },
  ]);
});

test("derives the frame interval from OME scale with acquisition fallback", () => {
  const image = {
    axes: [
      { name: "t", unit: "minute" },
      { name: "c" },
      { name: "y" },
      { name: "x" },
    ],
    levels: [
      {
        coordinate_transformations: [
          { type: "scale", scale: [30, 1, 0.325, 0.325] },
        ],
      },
    ],
  };
  expect(timeIntervalHours(image, 60)).toBe(0.5);
  expect(timeIntervalHours({ ...image, levels: [{}] }, 90)).toBe(1.5);
  expect(timeIntervalHours({ ...image, levels: [{}] })).toBeCloseTo(1 / 60);
});
