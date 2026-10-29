import { expect, test } from "@playwright/test";

import {
  emptyQcUrlState,
  parseQcUrlState,
  qcUrlSearch,
  type QcUrlAvailability,
  type QcUrlState,
} from "../../src/lib/qcUrlState";

const availability: QcUrlAvailability = {
  sliderIds: ["focus__DNA__ratio", "signal__ER__raw"],
  categoryIds: ["cell_line", "well_type"],
  sortableIds: ["plate", "well", "signal__ER__raw"],
  collectionIds: ["idr0080"],
  channels: ["DNA", "ER"],
  plates: [14610, 14611],
};

test("round-trips every shareable QC page choice", () => {
  const state: QcUrlState = {
    collection: "idr0080",
    plate: 14611,
    channel: "ER",
    tableMode: "wells",
    sortColumn: "signal__ER__raw",
    sortDirection: "desc",
    sliderSearch: "edge signal",
    sliders: {
      signal__ER__raw: { lower: null, upper: 7200, flagActive: true },
      focus__DNA__ratio: { lower: 0.25, upper: 1.5, flagActive: false },
    },
    categories: {
      well_type: ["perturbation"],
      cell_line: ["ES2", "A549"],
    },
  };

  const search = qcUrlSearch(state);
  expect(parseQcUrlState(search, availability)).toEqual({
    ...state,
    sliders: {
      focus__DNA__ratio: { lower: 0.25, upper: 1.5, flagActive: false },
      signal__ER__raw: { lower: null, upper: 7200, flagActive: true },
    },
    categories: {
      cell_line: ["A549", "ES2"],
      well_type: ["perturbation"],
    },
  });
});

test("serializes records and repeated category values deterministically", () => {
  const first: QcUrlState = {
    ...emptyQcUrlState(),
    sliders: {
      signal__ER__raw: { lower: 2, upper: 8, flagActive: true },
      focus__DNA__ratio: { lower: -1, upper: 1, flagActive: false },
    },
    categories: {
      well_type: ["reference", "perturbation", "reference"],
      cell_line: ["ES2", "A549"],
    },
  };
  const second: QcUrlState = {
    ...emptyQcUrlState(),
    sliders: {
      focus__DNA__ratio: { lower: -1, upper: 1, flagActive: false },
      signal__ER__raw: { lower: 2, upper: 8, flagActive: true },
    },
    categories: {
      cell_line: ["A549", "ES2", "A549"],
      well_type: ["perturbation", "reference"],
    },
  };

  expect(qcUrlSearch(first)).toBe(qcUrlSearch(second));
  expect(parseQcUrlState(qcUrlSearch(first), availability).categories).toEqual({
    cell_line: ["A549", "ES2"],
    well_type: ["perturbation", "reference"],
  });
});

test("ignores stale dictionary ids and malformed state safely", () => {
  const query = new URLSearchParams();
  query.set("collection", "removed-collection");
  query.set("qc.plate", "14699");
  query.set("qc.channel", "removed-channel");
  query.set("qc.table", "cards");
  query.set("qc.sort", "removed-column");
  query.set("qc.direction", "desc");
  query.set("qc.search", "nuclei");
  query.set("qc.slider.removed-metric", JSON.stringify([0, 1, true]));
  query.set("qc.slider.focus__DNA__ratio", JSON.stringify([2, 1, true]));
  query.append("qc.category.removed-attribute", "value");
  query.append("qc.category.cell_line", "");
  query.append("qc.category.cell_line", "ES2");
  query.append("qc.category.cell_line", "ES2");

  expect(parseQcUrlState(query, availability)).toEqual({
    ...emptyQcUrlState(),
    sliderSearch: "nuclei",
    categories: { cell_line: ["ES2"] },
  });
});

test("normalizes negative zero and rejects non-finite slider bounds", () => {
  const state: QcUrlState = {
    ...emptyQcUrlState(),
    sliders: {
      focus__DNA__ratio: { lower: -0, upper: 2, flagActive: true },
      signal__ER__raw: {
        lower: Number.NEGATIVE_INFINITY,
        upper: 5,
        flagActive: false,
      },
    },
  };

  expect(parseQcUrlState(qcUrlSearch(state), availability).sliders).toEqual({
    focus__DNA__ratio: { lower: 0, upper: 2, flagActive: true },
  });
});
