import { expect, test } from "@playwright/test";

import { writeQcViewerLink, type QcViewerLink } from "../../src/lib/qc/qcViewerLink";
import type { QcQueueEntry } from "../../src/lib/qcQueue";
import {
  parseViewerSearch,
  viewerQueryHref,
  writeViewerLink,
} from "../../src/lib/viewerLink";

function href(query: URLSearchParams | null): string {
  expect(query).not.toBeNull();
  return query ? viewerQueryHref(query) : "";
}

const primary = { datasetId: "d1", path: "A01_s1.zarr" };

test("writeViewerLink writes Data page links in today's order", () => {
  expect(href(writeViewerLink(new URLSearchParams(), { primary }))).toBe(
    "/view?dataset=d1&path=A01_s1.zarr",
  );
  expect(
    href(
      writeViewerLink(new URLSearchParams(), {
        primary,
        comparisons: [
          { datasetId: "d2", path: "B02_s1.zarr" },
          { datasetId: "d1", path: "A/1/0" },
        ],
        collectionId: "idr0080",
        returnDatasetId: "d0",
      }),
    ),
  ).toBe(
    "/view?dataset=d1&path=A01_s1.zarr&compare=B02_s1.zarr&compareDataset=d2" +
      "&compare=A%2F1%2F0&compareDataset=d1&collection=idr0080&returnDataset=d0",
  );
  // A return dataset is written only together with its collection.
  expect(href(writeViewerLink(new URLSearchParams(), { primary, returnDatasetId: "d0" }))).toBe(
    "/view?dataset=d1&path=A01_s1.zarr",
  );
});

const qcLink: QcViewerLink = {
  datasetId: "d1",
  path: "A01_s1.zarr",
  collectionId: "idr0080",
  scope: "well",
  plate: 14610,
  well: "A01",
  site: 1,
  exportId: "fixture-qc-primary-20261029",
  returnHref: "/qc?collection=idr0080&qc.sort=plate",
};

const entry: QcQueueEntry = {
  scope: "field",
  plateGroup: "idr0080-A549",
  plate: 14610,
  barcode: "SQ00014610",
  well: "A02",
  site: 1,
  channel: "DNA",
};
const queue = { id: "queue-1", entries: [entry, entry] };

test("writeQcViewerLink writes a fresh QC link in today's order", () => {
  expect(href(writeQcViewerLink(new URLSearchParams(), qcLink))).toBe(
    "/view?dataset=d1&path=A01_s1.zarr&collection=idr0080&qc=1&qcScope=well&qcPlate=14610" +
      "&qcWell=A01&qcSite=1&qcExport=fixture-qc-primary-20261029" +
      "&qcReturn=%2Fqc%3Fcollection%3Didr0080%26qc.sort%3Dplate",
  );
  expect(
    href(
      writeQcViewerLink(
        new URLSearchParams(),
        { ...qcLink, flaggedSites: [1, 2] },
        { queue, index: 0 },
      ),
    ),
  ).toBe(
    "/view?dataset=d1&path=A01_s1.zarr&collection=idr0080&qc=1&qcScope=well&qcPlate=14610" +
      "&qcWell=A01&qcSite=1&qcExport=fixture-qc-primary-20261029" +
      "&qcReturn=%2Fqc%3Fcollection%3Didr0080%26qc.sort%3Dplate" +
      "&qcQueue=queue-1&qcIndex=0&qcFlaggedSites=1%2C2",
  );
});

test("writeQcViewerLink edits the current link in place for a queue move", () => {
  const current = new URLSearchParams(
    "debug=1&dataset=d0&path=A01_s1.zarr&collection=idr0080&qc=1&qcScope=well" +
      "&qcPlate=14610&qcWell=A01&qcSite=1&qcExport=e1&qcReturn=%2Fqc" +
      "&qcQueue=queue-1&qcIndex=0&qcFlaggedSites=1%2C2",
  );
  const moved = writeQcViewerLink(
    current,
    {
      ...qcLink,
      datasetId: "d2",
      path: "A02_s1.zarr",
      scope: "field",
      well: "A02",
      exportId: "e1",
      returnHref: "/qc",
    },
    { queue, index: 1 },
  );

  // debug keeps its place, and the flagged sites of the previous well go.
  expect(href(moved)).toBe(
    "/view?debug=1&dataset=d2&path=A02_s1.zarr&collection=idr0080&qc=1&qcScope=field" +
      "&qcPlate=14610&qcWell=A02&qcSite=1&qcExport=e1&qcReturn=%2Fqc" +
      "&qcQueue=queue-1&qcIndex=1",
  );
  expect(writeQcViewerLink(new URLSearchParams(), qcLink, { queue, index: 2 })).toBeNull();
});

test("parseViewerSearch reads the field, three distinct comparisons and the queue", () => {
  const search = parseViewerSearch(
    "?dataset=d1&path=A01_s1.zarr&compare=B02_s1.zarr&compareDataset=d2" +
      "&compare=A01_s1.zarr&compare=C03_s1.zarr&compareDataset=d1&compare=D04_s1.zarr" +
      "&qcQueue=%20queue-1%20",
  );

  expect(search.primary).toEqual(primary);
  expect(search.queueId).toBe("queue-1");
  // Comparisons are cut to three, borrow the primary dataset when they name
  // none, and repeat no source.
  expect(search.sources).toEqual([
    primary,
    { datasetId: "d2", path: "B02_s1.zarr" },
    { datasetId: "d1", path: "C03_s1.zarr" },
  ]);
  expect(search.query.get("compareDataset")).toBe("d2");
  expect(parseViewerSearch("?dataset=d1&path=").sources).toEqual([{ datasetId: "d1", path: "" }]);
  expect(parseViewerSearch("?dataset=d1&path=A01_s1.zarr").queueId).toBe("");
  for (const missing of ["?path=A01_s1.zarr", "?dataset=d1", "?dataset=&path=A01_s1.zarr"]) {
    expect(() => parseViewerSearch(missing), missing).toThrow(
      "Choose an image field from the Data page first.",
    );
  }
});
