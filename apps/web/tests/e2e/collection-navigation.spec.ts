import { expect, test } from "@playwright/test";

import {
  clearCollectionPlace,
  collectionEntryName,
  dataSelectionHref,
  initialCollection,
  pathIsInCollection,
  readCollectionPlace,
  readCollectionPreference,
  writeCollectionPlace,
  writeCollectionPreference,
  type DataCollection,
} from "../../src/lib/collectionNavigation";

const collections: DataCollection[] = [
  {
    id: "timed",
    title: "TIMED",
    path: "/data/timed",
    default: true,
  },
  {
    id: "idr0080",
    title: "idr0080",
    path: "/data/idr0080/idr0080-raw.index.parquet",
  },
];

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem(key: string) {
      return values.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      values.set(key, value);
    },
    removeItem(key: string) {
      values.delete(key);
    },
  };
}

test("chooses a request before the remembered, default, and first collection", () => {
  expect(initialCollection(collections, "idr0080", "timed")?.id).toBe(
    "idr0080",
  );
  expect(initialCollection(collections, null, "idr0080")?.id).toBe("idr0080");
  expect(initialCollection(collections, "missing", "idr0080")?.id).toBe(
    "idr0080",
  );
  expect(initialCollection(collections, "missing")?.id).toBe("timed");
  expect(initialCollection(collections.map(({ default: _, ...item }) => item), null)?.id).toBe(
    "timed",
  );
  expect(initialCollection([], "timed")).toBeNull();
});

test("remembers collection choices separately for each profile", () => {
  const storage = memoryStorage();
  writeCollectionPreference(storage, "profile-a", "idr0080");
  writeCollectionPreference(storage, "profile-b", "timed");

  expect(readCollectionPreference(storage, "profile-a")).toBe("idr0080");
  expect(readCollectionPreference(storage, "profile-b")).toBe("timed");
  expect(readCollectionPreference(storage, "profile-c")).toBeNull();
});

test("remembers and clears a plate and well for each collection", () => {
  const storage = memoryStorage();
  writeCollectionPlace(storage, "profile-a", "idr0080", {
    datasetId: "plate-14610",
    wellPath: "A01",
  });
  writeCollectionPlace(storage, "profile-a", "timed", {
    datasetId: "plate-p1",
    wellPath: null,
  });

  expect(readCollectionPlace(storage, "profile-a", "idr0080")).toEqual({
    datasetId: "plate-14610",
    wellPath: "A01",
  });
  expect(readCollectionPlace(storage, "profile-a", "timed")).toEqual({
    datasetId: "plate-p1",
    wellPath: null,
  });

  clearCollectionPlace(storage, "profile-a", "idr0080");
  expect(readCollectionPlace(storage, "profile-a", "idr0080")).toBeNull();
  expect(readCollectionPlace(storage, "profile-a", "timed")).not.toBeNull();
});

test("ignores unavailable storage and malformed remembered places", () => {
  const malformedStorage = {
    getItem: () => '{"datasetId":4,"wellPath":"A01"}',
    setItem: () => undefined,
    removeItem: () => undefined,
  };
  expect(readCollectionPlace(malformedStorage, "profile", "collection")).toBeNull();

  const unavailableStorage = {
    getItem: () => {
      throw new Error("unavailable");
    },
    setItem: () => {
      throw new Error("unavailable");
    },
    removeItem: () => {
      throw new Error("unavailable");
    },
  };
  expect(readCollectionPreference(unavailableStorage, "profile")).toBeNull();
  expect(() =>
    writeCollectionPreference(unavailableStorage, "profile", "idr0080"),
  ).not.toThrow();
  expect(() =>
    clearCollectionPlace(unavailableStorage, "profile", "idr0080"),
  ).not.toThrow();
});

test("matches linked datasets at or below a collection path", () => {
  expect(pathIsInCollection("/data/timed/plate.zarr", "/data/timed")).toBe(true);
  expect(pathIsInCollection("C:\\data\\timed\\plate.zarr", "c:/data/timed")).toBe(
    true,
  );
  expect(pathIsInCollection("/data/timed-old/plate.zarr", "/data/timed")).toBe(
    false,
  );
  expect(pathIsInCollection("/data/TIMED/plate.zarr", "/data/timed")).toBe(false);
  expect(
    pathIsInCollection(
      "/data/idr0080/idr0080-raw.index.parquet/experiment/plate",
      "/data/idr0080/idr0080-raw.index.parquet",
    ),
  ).toBe(true);
});

test("builds collection return links and collection display names", () => {
  expect(collectionEntryName("Plate_01.zarr")).toBe("Plate_01");
  expect(collectionEntryName("folder")).toBe("folder");
  expect(dataSelectionHref("idr0080", "dataset 1")).toBe(
    "/data?collection=idr0080&dataset=dataset+1",
  );
  expect(dataSelectionHref(null, "ignored")).toBe("/data");
});
