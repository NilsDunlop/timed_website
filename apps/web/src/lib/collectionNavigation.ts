export type DataCollection = {
  id: string;
  title: string;
  description?: string | null;
  path: string;
  default?: boolean;
};

export function initialCollection(
  collections: DataCollection[],
  requestedId: string | null,
): DataCollection | null {
  return (
    collections.find((collection) => collection.id === requestedId) ??
    collections.find((collection) => collection.default) ??
    collections[0] ??
    null
  );
}

function normalizedPath(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return /^[a-z]:\//i.test(normalized) ? normalized.toLowerCase() : normalized;
}

export function pathIsInCollection(path: string, collectionPath: string): boolean {
  const candidate = normalizedPath(path);
  const root = normalizedPath(collectionPath);
  return candidate === root || candidate.startsWith(root + "/");
}

export function collectionEntryName(name: string): string {
  return name.replace(/\.zarr$/i, "");
}

export function dataSelectionHref(
  collectionId: string | null,
  datasetId: string | null = null,
): string {
  if (!collectionId) return "/data";
  const query = new URLSearchParams({ collection: collectionId });
  if (datasetId) query.set("dataset", datasetId);
  return "/data?" + query.toString();
}
