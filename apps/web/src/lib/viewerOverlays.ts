// The viewer's layer-composition point: images drawn over a field, such as mask
// outlines. VivCanvas passes these layers to Viv through `overlayLayers`.

import { BitmapLayer, type BitmapLayerProps } from "@deck.gl/layers";
import { DETAIL_VIEW_ID, getVivId } from "@vivjs/views";

import { axisSize } from "./viewerChannels";
import type { Source } from "./viewerSources";

export type ViewerOverlay = {
  /** A short name such as `outline`; it becomes part of the layer id. */
  kind: string;
  image: BitmapLayerProps["image"];
};

/** Viv draws a layer only in the views whose id it contains, here the detail view. */
export function overlayLayerId(kind: string): string {
  return `overlay-${kind}${getVivId(DETAIL_VIEW_ID)}`;
}

/** `[left, bottom, right, top]` in full-resolution pixels, with y pointing down. */
export function overlayBounds(source: Source): [number, number, number, number] {
  return [0, axisSize(source, "y"), axisSize(source, "x"), 0];
}

/** One unpickable bitmap per overlay over the whole field, with a shared opacity. */
export function viewerOverlayLayers(
  source: Source,
  overlays: readonly ViewerOverlay[],
  opacity: number,
): BitmapLayer[] {
  const bounds = overlayBounds(source);
  return overlays.map(
    (overlay) =>
      new BitmapLayer({
        id: overlayLayerId(overlay.kind),
        image: overlay.image,
        bounds,
        opacity,
        pickable: false,
      }),
  );
}
