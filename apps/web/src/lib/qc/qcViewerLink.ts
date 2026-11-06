// The QC part of a `/view` link, written in the order every QC builder has used:
// dataset, path, collection, qc, qcScope, qcPlate, qcWell, qcSite, qcExport and
// qcReturn, then qcQueue and qcIndex, then qcFlaggedSites.

import { withQcQueueIndex, type QcReviewQueue } from "../qcQueue";

export type QcViewerLink = {
  datasetId: string;
  path: string;
  collectionId: string;
  scope: "field" | "well";
  plate: number;
  well: string;
  site: number;
  exportId: string;
  returnHref: string;
  flaggedSites?: readonly number[];
};

export type QcQueuePosition = {
  queue: Pick<QcReviewQueue, "id" | "entries">;
  index: number;
};

/**
 * Write a QC link into a fresh query or the current one. Returns null when the
 * queue position is outside the queue.
 */
export function writeQcViewerLink(
  query: URLSearchParams,
  link: QcViewerLink,
  position?: QcQueuePosition,
): URLSearchParams | null {
  query.set("dataset", link.datasetId);
  query.set("path", link.path);
  query.set("collection", link.collectionId);
  query.set("qc", "1");
  query.set("qcScope", link.scope);
  query.set("qcPlate", String(link.plate));
  query.set("qcWell", link.well);
  query.set("qcSite", String(link.site));
  query.set("qcExport", link.exportId);
  query.set("qcReturn", link.returnHref);
  const positioned = position
    ? withQcQueueIndex(query, position.queue, position.index)
    : query;
  if (!positioned) return null;
  if (link.flaggedSites?.length) {
    positioned.set("qcFlaggedSites", link.flaggedSites.join(","));
  } else {
    positioned.delete("qcFlaggedSites");
  }
  return positioned;
}

/** Point the current QC link at another site of the same well. */
export function setQcViewerSite(
  query: URLSearchParams,
  path: string,
  site: number,
): URLSearchParams {
  query.set("path", path);
  query.set("qcSite", String(site));
  return query;
}
