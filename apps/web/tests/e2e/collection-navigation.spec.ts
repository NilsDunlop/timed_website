import { expect, test } from "@playwright/test";

import {
  carrySelectionAcrossDatasets,
  clearCollectionPlace,
  collectionCarriesWellBetweenPlates,
  collectionEntryName,
  dataSelectionHref,
  initialCollection,
  pathIsInCollection,
  qcNavigationHref,
  readCollectionPlace,
  readCollectionPreference,
  viewerReturnHref,
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
    qc_available: false,
  },
  {
    id: "idr0080",
    title: "idr0080",
    path: "/data/idr0080/idr0080-raw.index.parquet",
    qc_available: true,
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

test("shows QC navigation only for the active collection that advertises it", () => {
  expect(qcNavigationHref(collections, "idr0080", "timed")).toBe(
    "/qc?collection=idr0080",
  );
  expect(qcNavigationHref(collections, "timed", "idr0080")).toBeNull();
  expect(qcNavigationHref(collections, null, null)).toBeNull();
  expect(
    qcNavigationHref(
      collections.map(({ qc_available: _, ...collection }) => collection),
      "idr0080",
    ),
  ).toBeNull();
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

test("returns from a comparison to the dataset that launched it", () => {
  expect(
    viewerReturnHref(
      "?collection=idr0080&dataset=primary&returnDataset=active",
    ),
  ).toBe("/data?collection=idr0080&dataset=active");
  expect(viewerReturnHref("?collection=idr0080&dataset=primary")).toBe(
    "/data?collection=idr0080&dataset=primary",
  );
  expect(viewerReturnHref("?dataset=local")).toBe("/data");
});

test("carries coordinates only between Cell Health plates", () => {
  expect(collectionCarriesWellBetweenPlates("idr0080")).toBe(true);
  expect(collectionCarriesWellBetweenPlates("timed")).toBe(false);
  expect(collectionCarriesWellBetweenPlates("another-collection")).toBe(false);
  expect(collectionCarriesWellBetweenPlates(null)).toBe(false);
});

test("replaces the plate being left when a carried coordinate fills the tray", () => {
  const selections = [
    { datasetId: "plate-a", path: "C01", marker: "leaving" },
    { datasetId: "plate-b", path: "A01", marker: "keep-b" },
    { datasetId: "plate-c", path: "B01", marker: "keep-c" },
    { datasetId: "plate-d", path: "P24", marker: "keep-d" },
  ];
  const carried = { datasetId: "plate-e", path: "C01", marker: "carried" };

  expect(
    carrySelectionAcrossDatasets(
      selections,
      carried,
      { datasetId: "plate-a", path: "C01" },
      4,
    ),
  ).toEqual([carried, selections[1], selections[2], selections[3]]);
  expect(
    carrySelectionAcrossDatasets(
      selections,
      carried,
      { datasetId: "missing", path: "C01" },
      4,
    ),
  ).toBeNull();
});

test("appends or deduplicates a carried coordinate while the tray has room", () => {
  const source = { datasetId: "plate-a", path: "C01" };
  const carried = { datasetId: "plate-b", path: "C01" };
  expect(carrySelectionAcrossDatasets([source], carried, source, 4)).toEqual([
    source,
    carried,
  ]);
  expect(
    carrySelectionAcrossDatasets([source, carried], carried, source, 4),
  ).toEqual([source, carried]);
});
