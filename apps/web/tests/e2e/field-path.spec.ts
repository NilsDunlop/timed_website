import { expect, test } from "@playwright/test";

import {
  fieldPathFor,
  parseFieldPath,
  positiveSiteFromPath,
  siteIdFromPath,
  wellIdFromPath,
} from "../../src/lib/fieldPath";

type Row = {
  path: string;
  well: string | undefined;
  site: string | undefined;
  positiveSite: number | null;
  parsed: { well: string; site: number } | null;
};

// Each reader's output today. They differ deliberately in letter case, padding
// and anchoring, and every difference is a row here.
const rows: Row[] = [
  { path: "A1_s1.zarr", well: "A01", site: "1", positiveSite: 1, parsed: { well: "A01", site: 1 } },
  { path: "A01_s1.zarr", well: "A01", site: "1", positiveSite: 1, parsed: { well: "A01", site: 1 } },
  { path: "a1_s1.zarr", well: "A01", site: "1", positiveSite: 1, parsed: { well: "A01", site: 1 } },
  { path: "A1_S1.zarr", well: undefined, site: "1", positiveSite: 1, parsed: null },
  { path: "A01_s1.ZARR", well: undefined, site: "1", positiveSite: 1, parsed: null },
  { path: "B12_s03.zarr", well: "B12", site: "3", positiveSite: 3, parsed: { well: "B12", site: 3 } },
  { path: "A1_s0.zarr", well: "A01", site: "0", positiveSite: null, parsed: { well: "A01", site: 0 } },
  { path: "plate/A01_s2.zarr", well: undefined, site: "2", positiveSite: 2, parsed: null },
  { path: "A/1", well: "A01", site: undefined, positiveSite: null, parsed: null },
  { path: "a/1/0", well: "A01", site: "0", positiveSite: null, parsed: null },
  { path: "B/12/field", well: "B12", site: "field", positiveSite: null, parsed: null },
  { path: "A01.zarr", well: undefined, site: undefined, positiveSite: null, parsed: null },
  { path: "", well: undefined, site: undefined, positiveSite: null, parsed: null },
];

test("each field-path reader keeps its case, padding and anchoring rules", () => {
  for (const row of rows) {
    const label = JSON.stringify(row.path);
    expect(wellIdFromPath(row.path), `wellIdFromPath(${label})`).toBe(row.well);
    expect(siteIdFromPath(row.path), `siteIdFromPath(${label})`).toBe(row.site);
    expect(positiveSiteFromPath(row.path), `positiveSiteFromPath(${label})`).toBe(
      row.positiveSite,
    );
    expect(parseFieldPath(row.path), `parseFieldPath(${label})`).toEqual(row.parsed);
  }
});

test("fieldPathFor writes the collection form that parseFieldPath reads", () => {
  expect(fieldPathFor("B02", 3)).toBe("B02_s3.zarr");
  expect(parseFieldPath(fieldPathFor("B02", 3))).toEqual({ well: "B02", site: 3 });
});
