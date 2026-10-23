import { expect, test } from "@playwright/test";

import {
  collectionEntryName,
  dataSelectionHref,
  initialCollection,
  pathIsInCollection,
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

test("chooses a valid request before the default and first collection", () => {
  expect(initialCollection(collections, "idr0080")?.id).toBe("idr0080");
  expect(initialCollection(collections, "missing")?.id).toBe("timed");
  expect(initialCollection(collections.map(({ default: _, ...item }) => item), null)?.id).toBe(
    "timed",
  );
  expect(initialCollection([], "timed")).toBeNull();
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
