import type { Layer } from "@deck.gl/core";
import {
  DETAIL_VIEW_ID,
  getDefaultInitialViewState,
  PictureInPictureViewer,
  VivViewer,
} from "@hms-dbmi/viv";
import { SCALEBAR_VIEW_ID } from "@vivjs/views";
import { memo, useEffect, useRef, useState } from "react";

import type { ChannelState } from "../../lib/viewerChannels";
import type { ImageSummary, Loader } from "../../lib/viewerSources";
import { createViewerViewConfiguration } from "../../lib/viewerViews";

export type Dimensions = { width: number; height: number };
export type CameraViewState = {
  id: string;
  target: number[];
  zoom: number;
  [key: string]: unknown;
};
export type VivViewStateChangeEvent = {
  viewId: string;
  viewState: object;
  oldViewState: object;
};
type VivViewStateChange = (event: VivViewStateChangeEvent) => object;

type VivCanvasProps = {
  loader: Loader;
  image: ImageSummary;
  channels: ChannelState[];
  selections: Record<string, number>[];
  viewState: CameraViewState | null;
  onViewStateChange: VivViewStateChange;
  onReady: () => void;
  onResize?: (dimensions: Dimensions) => void;
  overlayLayers?: readonly Layer[];
};

const VivCanvas = memo(function VivCanvas({
  loader,
  image,
  channels,
  selections,
  viewState,
  onViewStateChange,
  onReady,
  onResize,
  overlayLayers,
}: VivCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState<Dimensions>({
    width: 900,
    height: 600,
  });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const update = () => {
      const next = {
        width: Math.max(320, Math.floor(container.clientWidth)),
        height: Math.max(320, Math.floor(container.clientHeight)),
      };
      setDimensions(next);
      onResize?.(next);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(container);
    return () => observer.disconnect();
  }, [onResize]);

  const viewConfiguration = createViewerViewConfiguration({
    loader,
    image,
    dimensions,
  });
  const singleResolutionLayerConfig = {
    loader,
    contrastLimits: channels.map((channel) => channel.contrast),
    colors: channels.map((channel) => channel.color),
    channelsVisible: channels.map((channel) => channel.visible),
    selections,
    onViewportLoad: onReady,
  };
  // Without overlays both viewers get exactly the props they had before.
  const deckProps = overlayLayers?.length ? { layers: overlayLayers } : undefined;
  const viewer =
    viewConfiguration.kind === "picture-in-picture" ? (
      <PictureInPictureViewer
        loader={loader}
        contrastLimits={channels.map((channel) => channel.contrast)}
        colors={channels.map((channel) => channel.color)}
        channelsVisible={channels.map((channel) => channel.visible)}
        selections={selections}
        overview={viewConfiguration.overview}
        overviewOn
        snapScaleBar={viewConfiguration.snapScaleBar}
        height={dimensions.height}
        width={dimensions.width}
        viewStates={viewState ? [viewState] : undefined}
        onViewStateChange={onViewStateChange}
        onViewportLoad={onReady}
        deckProps={deckProps}
      />
    ) : (
      <VivViewer
        views={viewConfiguration.views}
        layerProps={viewConfiguration.views.map(
          () => singleResolutionLayerConfig,
        )}
        viewStates={[
          viewState ?? {
            ...getDefaultInitialViewState(loader, dimensions, 0.5),
            id: DETAIL_VIEW_ID,
          },
          ...(viewConfiguration.hasPhysicalPixelSize
            ? [
                {
                  id: SCALEBAR_VIEW_ID,
                  zoom: 0,
                  target: [dimensions.width / 2, dimensions.height / 2, 0],
                },
              ]
            : []),
        ]}
        onViewStateChange={onViewStateChange}
        deckProps={deckProps}
      />
    );

  return (
    <div
      className="canvas-frame"
      data-scale-bar={
        viewConfiguration.hasPhysicalPixelSize ? "true" : "false"
      }
      ref={containerRef}
    >
      {viewer}
    </div>
  );
});

export default VivCanvas;
