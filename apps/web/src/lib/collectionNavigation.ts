export type DataCollection = {
  id: string;
  title: string;
  description?: string | null;
  path: string;
  default?: boolean;
};

export type CollectionPlace = {
  datasetId: string;
  wellPath: string | null;
};

export type CollectionStorage = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem"
>;

const COLLECTION_PREFERENCE_PREFIX = "timed.data.collection.v1:";
const COLLECTION_PLACE_PREFIX = "timed.data.place.v1:";

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

export function dataSelectionHref(
  collectionId: string | null,
  datasetId: string | null = null,
): string {
  if (!collectionId) return "/data";
  const query = new URLSearchParams({ collection: collectionId });
  if (datasetId) query.set("dataset", datasetId);
  return "/data?" + query.toString();
}
