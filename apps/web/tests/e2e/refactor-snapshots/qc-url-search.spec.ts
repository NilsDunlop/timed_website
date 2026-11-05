// Temporary refactor snapshot: today's exact QC page URL strings, recorded from
// unchanged code on 2026-11-05. The last commit of the Gate A refactor deletes
// this folder.
import { expect, test } from "@playwright/test";

import { emptyQcUrlState, qcUrlSearch, type QcUrlState } from "../../../src/lib/qcUrlState";

const full: QcUrlState = {
  collection: "idr0080",
  plate: 14611,
  channel: "ER",
  tableMode: "wells",
  sortColumn: "signal__ER__raw",
  sortDirection: "desc",
  sliderSearch: "edge signal · µm",
  showDetails: true,
  sliders: {
    signal__ER__raw: { lower: null, upper: 7200, flagActive: true },
    focus__DNA__ratio: { lower: -0, upper: 1.5, flagActive: false },
  },
  categories: {
    well_type: ["perturbation"],
    cell_line: ["ES2", "A549"],
  },
};

const cases: [string, QcUrlState, string][] = [
  ["empty", emptyQcUrlState(), ""],
  ["collection only", { ...emptyQcUrlState(), collection: "idr0080" }, "?collection=idr0080"],
  [
    "ascending sort",
    { ...emptyQcUrlState(), sortColumn: "plate", sortDirection: "asc" },
    "?qc.sort=plate",
  ],
  [
    "every choice",
    full,
    "?collection=idr0080&qc.plate=14611&qc.channel=ER&qc.table=wells" +
      "&qc.sort=signal__ER__raw&qc.direction=desc&qc.search=edge+signal+%C2%B7+%C2%B5m" +
      "&qc.details=1&qc.slider.focus__DNA__ratio=%5B0%2C1.5%2Cfalse%5D" +
      "&qc.slider.signal__ER__raw=%5Bnull%2C7200%2Ctrue%5D" +
      "&qc.category.cell_line=A549&qc.category.cell_line=ES2" +
      "&qc.category.well_type=perturbation",
  ],
];

for (const [name, state, expected] of cases) {
  test(`qcUrlSearch is unchanged: ${name}`, () => {
    expect(qcUrlSearch(state)).toBe(expected);
  });
}
