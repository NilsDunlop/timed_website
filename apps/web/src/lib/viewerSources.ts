import type { loadOmeZarr } from "@hms-dbmi/viv";

import type { components } from "./api/dataserver-schema";
import type { PlateMetadata } from "./plateMetadata";

export type ImageSummary = components["schemas"]["ImageSummary"];
export type Summary =
  | ImageSummary
  | components["schemas"]["PlateSummary"];
export type Loader = Awaited<ReturnType<typeof loadOmeZarr>>["data"];
export type Source = Loader[number];

export type ViewerSourceState = {
  datasetId: string;
  path: string;
  image: ImageSummary;
  metadata: PlateMetadata | null;
  loader: Loader;
  readyKey: string;
};

export function imageFromSummary(summary: Summary): ImageSummary | null {
  return summary.kind === "image" ? summary : (summary.first_image ?? null);
}

export function encodedZarrPath(datasetId: string, imagePath: string): string {
  const encodedPath = imagePath
    .split("/")
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  const path = (
    "/backend/zarr/" +
    encodeURIComponent(datasetId) +
    "/" +
    encodedPath
  ).replace(/\/$/, "");
  return new URL(path, window.location.origin).href;
}
