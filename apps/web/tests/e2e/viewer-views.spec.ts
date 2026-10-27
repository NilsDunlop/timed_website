import { expect, test } from "@playwright/test";
import { DetailView } from "@hms-dbmi/viv";
import { ScaleBarView } from "@vivjs/views";

import type { components } from "../../src/lib/api/dataserver-schema";
import { createViewerViewConfiguration } from "../../src/lib/viewerViews";

type ImageSummary = components["schemas"]["ImageSummary"];

function imageSummary(calibrated: boolean): ImageSummary {
  return {
    axes: [
      { name: "c", type: "channel", unit: null },
      { name: "y", type: "space", unit: calibrated ? "micrometer" : null },
      { name: "x", type: "space", unit: calibrated ? "micrometer" : null },
    ],
    channels: [],
    kind: "image",
    levels: [
      {
        chunks: [1, 16, 16],
        coordinate_transformations: [
          { type: "scale", scale: calibrated ? [1, 0.3, 0.3] : [1, 1, 1] },
        ],
        dtype: "uint16",
        path: "0",
        shape: [1, 512, 512],
      },
    ],
    name: "test",
    ome_zarr_version: "0.5",
    path: "test.zarr",
    zarr_format: 3,
  };
}

test("single-resolution views include a snapped bottom-right scale bar iff calibrated", () => {
  const loader = [{ shape: [1, 512, 512] }];
  const dimensions = { width: 900, height: 600 };
  const calibrated = createViewerViewConfiguration({
    loader,
    image: imageSummary(true),
    dimensions,
  });
  expect(calibrated.kind).toBe("single-resolution");
  if (calibrated.kind !== "single-resolution") return;
  expect(calibrated.views[0]).toBeInstanceOf(DetailView);
  const scaleBar = calibrated.views.find(
    (view) => view instanceof ScaleBarView,
  );
  expect(scaleBar).toBeInstanceOf(ScaleBarView);
  expect(scaleBar).toMatchObject({
    position: "bottom-right",
    snap: true,
  });

  const uncalibrated = createViewerViewConfiguration({
    loader,
    image: imageSummary(false),
    dimensions,
  });
  expect(uncalibrated.kind).toBe("single-resolution");
  if (uncalibrated.kind !== "single-resolution") return;
  expect(
    uncalibrated.views.some((view) => view instanceof ScaleBarView),
  ).toBe(false);
});

test("multiscale views preserve PictureInPictureViewer with a top-right overview", () => {
  const configuration = createViewerViewConfiguration({
    loader: [
      { shape: [1, 512, 512] },
      { shape: [1, 256, 256] },
    ],
    image: imageSummary(true),
    dimensions: { width: 900, height: 600 },
  });
  expect(configuration).toMatchObject({
    kind: "picture-in-picture",
    hasPhysicalPixelSize: true,
    overview: { position: "top-right" },
    snapScaleBar: true,
  });
});
