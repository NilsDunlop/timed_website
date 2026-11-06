// The `/view` link: the viewer's reader, the Data page's writer and the back link.
//
// Hrefs depend on parameter insertion order, so a writer calls `set()` and
// `append()` in the order its callers have always used. A writer fills either a
// fresh query or the current one; editing the current query keeps parameters the
// writer does not know, such as `debug`. lib/qc/qcViewerLink.ts writes QC links.

import { dataSelectionHref } from "./collectionNavigation";
import { QC_QUEUE_ID_PARAM } from "./qcQueue";

const MAX_VIEWER_SOURCES = 4;

export type ViewerSourceSpec = { datasetId: string; path: string };

/** A Data page link: one field, up to three comparisons, and where to return. */
export type ViewerLink = {
  primary: ViewerSourceSpec;
  comparisons?: readonly ViewerSourceSpec[];
  collectionId?: string | null;
  returnDatasetId?: string | null;
};

export type ViewerSearch = {
  query: URLSearchParams;
  /** The trimmed QC queue id, or "" when the link has none. */
  queueId: string;
  primary: ViewerSourceSpec;
  /** The primary source, then the comparisons, without repeats. */
  sources: ViewerSourceSpec[];
};

export function viewerQueryHref(query: URLSearchParams): string {
  return `/view?${query.toString()}`;
}

export function writeViewerLink(query: URLSearchParams, link: ViewerLink): URLSearchParams {
  query.set("dataset", link.primary.datasetId);
  query.set("path", link.primary.path);
  for (const comparison of link.comparisons ?? []) {
    query.append("compare", comparison.path);
    query.append("compareDataset", comparison.datasetId);
  }
  if (link.collectionId) {
    query.set("collection", link.collectionId);
    if (link.returnDatasetId) query.set("returnDataset", link.returnDatasetId);
  }
  return query;
}

export function parseViewerSearch(search: string): ViewerSearch {
  const query = new URLSearchParams(search);
  const queueId = query.get(QC_QUEUE_ID_PARAM)?.trim() ?? "";
  const primaryDatasetId = query.get("dataset");
  const selectedPath = query.get("path");
  if (!primaryDatasetId || selectedPath == null) {
    throw new Error("Choose an image field from the Data page first.");
  }
  const comparisonPaths = query
    .getAll("compare")
    .slice(0, MAX_VIEWER_SOURCES - 1);
  const comparisonDatasetIds = query.getAll("compareDataset");
  const primary = { datasetId: primaryDatasetId, path: selectedPath };
  const requestedSources = [
    primary,
    ...comparisonPaths.map((path, index) => ({
      datasetId: comparisonDatasetIds[index] ?? primaryDatasetId,
      path,
    })),
  ];
  const seenSources = new Set<string>();
  const sources = requestedSources.filter((source) => {
    const key = source.datasetId + "\u0000" + source.path;
    if (seenSources.has(key)) return false;
    seenSources.add(key);
    return true;
  });
  return { query, queueId, primary, sources };
}

function qcSelectionHref(collectionId: string | null): string {
  if (!collectionId) return "/qc";
  return "/qc?" + new URLSearchParams({ collection: collectionId }).toString();
}

// The viewer's check of `qcReturn` before it links back. It is deliberately not
// qcQueue's safeQcReturnHref, which guards stored queues more strictly (length,
// backslashes and control characters) and returns no normalised href. Merge the
// two only after one shared case table shows that they agree.
function normalizedQcReturnHref(value: string | null): string | null {
  if (value === "/qc") return value;
  if (!value?.startsWith("/qc?")) return null;
  try {
    const parsed = new URL(value, "https://timed.invalid");
    if (
      parsed.origin !== "https://timed.invalid" ||
      parsed.pathname !== "/qc" ||
      parsed.hash
    ) {
      return null;
    }
    return parsed.pathname + parsed.search;
  } catch {
    return null;
  }
}

export function viewerReturnHref(search: string): string {
  const query = new URLSearchParams(search);
  const collectionId = query.get("collection");
  if (query.get("qc") === "1") {
    return (
      normalizedQcReturnHref(query.get("qcReturn")) ?? qcSelectionHref(collectionId)
    );
  }
  return dataSelectionHref(
    collectionId,
    collectionId
      ? (query.get("returnDataset") ?? query.get("dataset"))
      : null,
  );
}
