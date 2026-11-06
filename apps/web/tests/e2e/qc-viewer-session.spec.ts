import { expect, test } from "@playwright/test";

import { qcRequestFromQuery } from "../../src/lib/qc/qcViewerSession";

const validLink: Record<string, string> = {
  qc: "1",
  collection: " idr0080 ",
  qcExport: "fixture-qc-primary-20261029",
  qcPlate: "14610",
  qcWell: " a01 ",
  qcSite: "1",
  qcScope: "well",
  qcFlaggedSites: "1,2",
};

function qcQuery(changes: Record<string, string | null> = {}): URLSearchParams {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...validLink, ...changes })) {
    if (value !== null) query.set(key, value);
  }
  return query;
}

test("qcRequestFromQuery reads a valid QC link", () => {
  expect(qcRequestFromQuery(qcQuery(), "A01_s1.zarr", 1)).toEqual({
    collectionId: "idr0080",
    expectedExport: "fixture-qc-primary-20261029",
    plate: 14610,
    well: "A01",
    site: 1,
    scope: "well",
    flaggedSites: [1, 2],
  });
  expect(
    qcRequestFromQuery(qcQuery({ qcScope: null, qcFlaggedSites: null }), "A01_s1.zarr", 1),
  ).toEqual({
    collectionId: "idr0080",
    expectedExport: "fixture-qc-primary-20261029",
    plate: 14610,
    well: "A01",
    site: 1,
    scope: "field",
    flaggedSites: [],
  });
  expect(qcRequestFromQuery(qcQuery({ qcScope: "plate" }), "A01_s1.zarr", 1)?.scope).toBe(
    "field",
  );
});

test("qcRequestFromQuery ignores a link that is not a QC link", () => {
  expect(qcRequestFromQuery(qcQuery({ qc: null }), "A01_s1.zarr", 1)).toBeNull();
  expect(qcRequestFromQuery(qcQuery({ qc: "true" }), "A01_s1.zarr", 1)).toBeNull();
});

test("qcRequestFromQuery rejects each invalid part of a QC link", () => {
  const invalid: [string, Record<string, string | null>, string?, number?][] = [
    ["missing collection", { collection: null }],
    ["blank collection", { collection: "  " }],
    ["missing export", { qcExport: null }],
    ["missing plate", { qcPlate: null }],
    ["plate zero", { qcPlate: "0" }],
    ["fractional plate", { qcPlate: "1.5" }],
    ["non-numeric plate", { qcPlate: "plate" }],
    ["missing well", { qcWell: null }],
    ["malformed well", { qcWell: "1A" }],
    ["missing site", { qcSite: null }],
    ["fractional site", { qcSite: "1.5" }],
    ["open comparison", {}, "A01_s1.zarr", 2],
    ["field in another well", {}, "A02_s1.zarr"],
    ["field at another site", {}, "A01_s2.zarr"],
    ["non-integer flagged site", { qcFlaggedSites: "1,x" }],
    ["non-positive flagged site", { qcFlaggedSites: "0" }],
    ["repeated flagged site", { qcFlaggedSites: "1,1" }],
  ];
  for (const [label, changes, path = "A01_s1.zarr", sourceCount = 1] of invalid) {
    expect(() => qcRequestFromQuery(qcQuery(changes), path, sourceCount), label).toThrow(
      "The QC viewer link is invalid",
    );
  }
});
