import { BitmapLayer } from "@deck.gl/layers";
import { expect, test } from "@playwright/test";
import { DETAIL_VIEW_ID, getVivId, OVERVIEW_VIEW_ID } from "@vivjs/views";

import {
  overlayBounds,
  overlayLayerId,
  viewerOverlayLayers,
} from "../../src/lib/viewerOverlays";
import type { Source } from "../../src/lib/viewerSources";

// A full-resolution level of a five-channel field, 600 pixels wide and 400 high.
const source = { labels: ["t", "c", "y", "x"], shape: [1, 5, 400, 600] } as unknown as Source;

test("overlay layers are drawn only in the detail view", () => {
  expect(overlayLayerId("outline")).toBe("overlay-outline-#detail#");
  expect(overlayLayerId("outline")).toContain(getVivId(DETAIL_VIEW_ID));
  expect(overlayLayerId("outline")).not.toContain(getVivId(OVERVIEW_VIEW_ID));
});

test("overlay bounds cover the full-resolution field", () => {
  expect(overlayBounds(source)).toEqual([0, 400, 600, 0]);
});

test("overlay layers are unpickable deck.gl bitmaps with a shared opacity", () => {
  const layers = viewerOverlayLayers(
    source,
    [
      { kind: "outline", image: "/outline.png" },
      { kind: "centroid", image: "/centroid.png" },
    ],
    0.6,
  );

  expect(layers.every((layer) => layer instanceof BitmapLayer)).toBe(true);
  // deck.gl resolves `image` asynchronously once a layer loads, so it is not compared.
  expect(
    layers.map(({ id, props }) => ({
      id,
      bounds: props.bounds,
      opacity: props.opacity,
      pickable: props.pickable,
    })),
  ).toEqual([
    { id: "overlay-outline-#detail#", bounds: [0, 400, 600, 0], opacity: 0.6, pickable: false },
    { id: "overlay-centroid-#detail#", bounds: [0, 400, 600, 0], opacity: 0.6, pickable: false },
  ]);
  expect(viewerOverlayLayers(source, [], 1)).toEqual([]);
});
