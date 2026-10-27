import { expect, test } from "@playwright/test";

import type { components } from "../../src/lib/api/dataserver-schema";
import {
  channelLabelsMatch,
  imageChannelLabels,
  imageHasPhysicalPixelSize,
} from "../../src/lib/imageSummary";

type ImageSummary = components["schemas"]["ImageSummary"];

function imageWithChannels(
  channelCount: number,
  names: string[],
): ImageSummary {
  return {
    axes: [
      { name: "c", type: "channel", unit: null },
      { name: "y", type: "space", unit: null },
      { name: "x", type: "space", unit: null },
    ],
    channels: names.map((name) => ({
      name,
      color: null,
      window: null,
      active: null,
    })),
    kind: "image",
    levels: [
      {
        chunks: [1, 16, 16],
        dtype: "uint16",
        path: "0",
        shape: [channelCount, 16, 16],
      },
    ],
    name: "test",
    ome_zarr_version: "0.5",
    path: "test.zarr",
    zarr_format: 3,
  };
}

test("derives comparable labels from channel axes and metadata", () => {
  expect(imageChannelLabels(imageWithChannels(3, []))).toEqual([
    "Channel 1",
    "Channel 2",
    "Channel 3",
  ]);
  expect(
    imageChannelLabels(imageWithChannels(3, ["DNA", "ER", "RNA"])),
  ).toEqual(["DNA", "ER", "RNA"]);
});

test("requires the effective channel labels to match in order", () => {
  expect(
    channelLabelsMatch(
      imageChannelLabels(imageWithChannels(3, [])),
      imageChannelLabels(imageWithChannels(3, [
        "Channel 1",
        "Channel 2",
        "Channel 3",
      ])),
    ),
  ).toBe(true);
  expect(
    channelLabelsMatch(
      imageChannelLabels(imageWithChannels(3, [])),
      imageChannelLabels(imageWithChannels(2, [])),
    ),
  ).toBe(false);
  expect(
    channelLabelsMatch(
      ["DNA", "ER", "RNA"],
      ["DNA", "RNA", "ER"],
    ),
  ).toBe(false);
});

test("requires a spatial unit and positive scale for a physical pixel size", () => {
  const calibrated = imageWithChannels(3, ["DNA", "ER", "AGP"]);
  calibrated.axes[2] = { name: "x", type: "space", unit: "micrometer" };
  calibrated.levels[0].coordinate_transformations = [
    { type: "scale", scale: [1, 0.3, 0.3] },
  ];
  expect(imageHasPhysicalPixelSize(calibrated)).toBe(true);

  const uncalibrated = imageWithChannels(3, ["DNA", "ER", "AGP"]);
  uncalibrated.levels[0].coordinate_transformations = [
    { type: "scale", scale: [1, 1, 1] },
  ];
  expect(imageHasPhysicalPixelSize(uncalibrated)).toBe(false);

  calibrated.levels[0].coordinate_transformations = [
    { type: "scale", scale: [1, 0.3, 0] },
  ];
  expect(imageHasPhysicalPixelSize(calibrated)).toBe(false);
});
