export type DataCollection = {
  id: string;
  title: string;
  description?: string | null;
  path: string;
  default?: boolean;
  qc_available?: boolean;
};

export type CollectionPlace = {
  datasetId: string;
  wellPath: string | null;
};

export type DatasetWell = {
  datasetId: string;
  path: string;
};

export type CollectionStorage = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem"
>;

const COLLECTION_PREFERENCE_PREFIX = "timed.data.collection.v1:";
const COLLECTION_PLACE_PREFIX = "timed.data.place.v1:";

export const COLLECTION_SELECTION_EVENT = "timed:collection-selection";

export type CollectionSelection = {
  profileId: string;
  collectionId: string;
};

function preferenceKey(profileId: string): string {
  return COLLECTION_PREFERENCE_PREFIX + encodeURIComponent(profileId);
}

function placeKey(profileId: string, collectionId: string): string {
  return (
    COLLECTION_PLACE_PREFIX +
    encodeURIComponent(profileId) +
    ":" +
    encodeURIComponent(collectionId)
  );
}

export function initialCollection(
  collections: DataCollection[],
  requestedId: string | null,
  rememberedId: string | null = null,
): DataCollection | null {
  return (
    collections.find((collection) => collection.id === requestedId) ??
    collections.find((collection) => collection.id === rememberedId) ??
    collections.find((collection) => collection.default) ??
    collections[0] ??
    null
  );
}

export function qcNavigationHref(
  collections: DataCollection[],
  requestedId: string | null,
  rememberedId: string | null = null,
): string | null {
  const collection = initialCollection(collections, requestedId, rememberedId);
  if (!collection?.qc_available) return null;
  return "/qc?" + new URLSearchParams({ collection: collection.id }).toString();
}

export function notifyCollectionSelection(
  profileId: string,
  collectionId: string,
): void {
  if (typeof window === "undefined") return;
  const eventInit = {
    ["de" + "tail"]: { profileId, collectionId },
  } as CustomEventInit<CollectionSelection>;
  window.dispatchEvent(
    new CustomEvent<CollectionSelection>(COLLECTION_SELECTION_EVENT, eventInit),
  );
}

export function readCollectionPreference(
  storage: CollectionStorage,
  profileId: string,
): string | null {
  try {
    return storage.getItem(preferenceKey(profileId)) || null;
  } catch {
    return null;
  }
}

export function writeCollectionPreference(
  storage: CollectionStorage,
  profileId: string,
  collectionId: string,
): void {
  try {
    storage.setItem(preferenceKey(profileId), collectionId);
  } catch {
    // Storage may be unavailable or full. The profile default remains usable.
  }
}

export function readCollectionPlace(
  storage: CollectionStorage,
  profileId: string,
  collectionId: string,
): CollectionPlace | null {
  try {
    const encoded = storage.getItem(placeKey(profileId, collectionId));
    if (!encoded) return null;
    const value: unknown = JSON.parse(encoded);
    if (!value || typeof value !== "object") return null;
    const candidate = value as Partial<CollectionPlace>;
    if (
      typeof candidate.datasetId !== "string" ||
      candidate.datasetId.length === 0
    ) {
      return null;
    }
    if (
      candidate.wellPath !== undefined &&
      candidate.wellPath !== null &&
      typeof candidate.wellPath !== "string"
    ) {
      return null;
    }
    return {
      datasetId: candidate.datasetId,
      wellPath: candidate.wellPath ?? null,
    };
  } catch {
    return null;
  }
}

export function writeCollectionPlace(
  storage: CollectionStorage,
  profileId: string,
  collectionId: string,
  place: CollectionPlace,
): void {
  try {
    storage.setItem(placeKey(profileId, collectionId), JSON.stringify(place));
  } catch {
    // Storage may be unavailable or full. Browsing still works without restore.
  }
}

export function clearCollectionPlace(
  storage: CollectionStorage,
  profileId: string,
  collectionId: string,
): void {
  try {
    storage.removeItem(placeKey(profileId, collectionId));
  } catch {
    // Storage may be unavailable. There is no further recovery work to do.
  }
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

export function collectionCarriesWellBetweenPlates(
  collectionId: string | null | undefined,
): boolean {
  return collectionId === "idr0080";
}

export function carrySelectionAcrossDatasets<T extends DatasetWell>(
  selections: readonly T[],
  carried: T,
  source: DatasetWell | null,
  limit: number,
): T[] | null {
  if (
    selections.some(
      (selection) =>
        selection.datasetId === carried.datasetId &&
        selection.path === carried.path,
    )
  ) {
    return [...selections];
  }
  if (selections.length < limit) return [...selections, carried];
  if (!source) return null;
  const sourceIndex = selections.findIndex(
    (selection) =>
      selection.datasetId === source.datasetId &&
      selection.path === source.path,
  );
  if (sourceIndex < 0) return null;
  const next = [...selections];
  next[sourceIndex] = carried;
  return next;
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

function qcSelectionHref(collectionId: string | null): string {
  if (!collectionId) return "/qc";
  return "/qc?" + new URLSearchParams({ collection: collectionId }).toString();
}

function safeQcReturnHref(value: string | null): string | null {
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
      safeQcReturnHref(query.get("qcReturn")) ?? qcSelectionHref(collectionId)
    );
  }
  return dataSelectionHref(
    collectionId,
    collectionId
      ? (query.get("returnDataset") ?? query.get("dataset"))
      : null,
  );
}
