import {
  DETAIL_VIEW_ID,
  DetailView,
} from "@hms-dbmi/viv";
import { SCALEBAR_VIEW_ID, ScaleBarView } from "@vivjs/views";

import type { components } from "./api/dataserver-schema";
import { imageHasPhysicalPixelSize } from "./imageSummary";

type ImageSummary = components["schemas"]["ImageSummary"];

type ViewerDimensions = {
  width: number;
  height: number;
};

type ViewerViewOptions = {
  loader: unknown[];
  image: ImageSummary;
  dimensions: ViewerDimensions;
};

export type ViewerViewConfiguration =
  | {
      kind: "picture-in-picture";
      hasPhysicalPixelSize: boolean;
      overview: { position: "top-right" };
      snapScaleBar: true;
    }
  | {
      kind: "single-resolution";
      hasPhysicalPixelSize: boolean;
      views: Array<DetailView | ScaleBarView>;
    };

export function createViewerViewConfiguration({
  loader,
  image,
  dimensions,
}: ViewerViewOptions): ViewerViewConfiguration {
  const hasPhysicalPixelSize = imageHasPhysicalPixelSize(image);
  if (loader.length > 1) {
    return {
      kind: "picture-in-picture",
      hasPhysicalPixelSize,
      overview: { position: "top-right" },
      snapScaleBar: true,
    };
  }

  const views: Array<DetailView | ScaleBarView> = [
    new DetailView({
      id: DETAIL_VIEW_ID,
      height: dimensions.height,
      width: dimensions.width,
    }),
  ];
  if (hasPhysicalPixelSize) {
    views.push(
      new ScaleBarView({
        id: SCALEBAR_VIEW_ID,
        height: dimensions.height,
        width: dimensions.width,
        loader,
        imageViewId: DETAIL_VIEW_ID,
        position: "bottom-right",
        snap: true,
      }),
    );
  }
  return {
    kind: "single-resolution",
    hasPhysicalPixelSize,
    views,
  };
}
