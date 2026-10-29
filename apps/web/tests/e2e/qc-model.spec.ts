import { expect, test } from "@playwright/test";

import {
  aggregateQcWells,
  applyQcFilters,
  buildQcModel,
  defaultQcFilterState,
  sortQcRows,
  sortQcWells,
  uniqueQcValues,
  type QcDictionaryRow,
  type QcFieldsInput,
  type QcFilterState,
} from "../../src/lib/qcModel";

const dictionary: QcDictionaryRow[] = [
  {
    file: "site_metrics.csv",
    column: "sharpness__DNA__score",
    label: "Sharpness",
    metric: "sharpness",
    channel: "DNA",
    scale: "score",
    worse: "low",
    layer: "site_review",
    gate: "automatic",
    status: "registered",
    question: "Is the field sharp?",
    primary: true,
    markers: '[{"value":3,"label":"registered"}]',
  },
  {
    file: "site_metrics.csv",
    column: "sharpness__DNA__raw",
    metric: "sharpness",
    channel: "DNA",
    scale: "raw",
    worse: "low",
    layer: "site_review",
    gate: "automatic",
    status: "registered",
    primary: false,
  },
  {
    file: "site_metrics.csv",
    column: "texture__AGP__score",
    metric: "texture",
    channel: "AGP",
    scale: "score",
    worse: "high",
    layer: "site_review",
    gate: "review",
    status: "pending_review",
    question: "Is texture excessive?",
    primary: true,
    default_flag_hi: 8,
  },
  {
    file: "site_metrics.csv",
    column: "texture__AGP__raw",
    metric: "texture",
    channel: "AGP",
    scale: "raw",
    worse: "high",
    layer: "site_review",
    gate: "review",
    status: "kept",
    primary: false,
  },
  {
    file: "site_metrics.csv",
    column: "balance__site__score",
    metric: "balance",
    channel: "site",
    scale: "score",
    worse: "both",
    layer: "site_review",
    gate: "review",
    status: "kept",
    primary: true,
    default_flag_lo: -2,
    default_flag_hi: 2,
  },
  {
    file: "site_metrics.csv",
    column: "brightness__RNA__score",
    metric: "brightness",
    channel: "RNA",
    scale: "score",
    worse: "high",
    layer: "site_review",
    gate: "none",
    status: "kept",
    primary: true,
  },
  {
    file: "site_metrics.csv",
    column: "fixture_context",
    label: "Fixture context",
    layer: "context",
    status: "kept",
  },
  {
    file: "site_metrics.csv",
    column: "retired_metric",
    metric: "retired",
    channel: "DNA",
    scale: "score",
    worse: "high",
    layer: "retired",
    gate: "review",
    status: "kept",
    primary: true,
  },
  {
    file: "site_metrics.csv",
    column: "unknown_metric",
    metric: "unknown",
    channel: "DNA",
    scale: "score",
    worse: "high",
    layer: "site_review",
    gate: "review",
    status: "future_status",
    primary: true,
  },
];

const fields: QcFieldsInput = {
  row_count: 6,
  columns: {
    experiment: ["A", "A", "A", "B", "B", "B"],
    plate: [1, 1, 1, 2, 2, 2],
    well: ["A01", "A01", "A02", "B01", "B01", "B02"],
    site: [1, 2, 1, 1, 2, 1],
    sharpness__DNA__score: [1, 5, 6, 7, 8, 9],
    sharpness__DNA__raw: [10, 50, 60, 70, 80, 90],
    texture__AGP__score: [2, 3, 9, 5, 10, 1],
    texture__AGP__raw: [20, 30, 90, 50, 100, 10],
    balance__site__score: [-3, 0, 0, 4, 0, 0],
    brightness__RNA__score: [2, 3, 4, 5, 6, 7],
    fixture_context: ["x", "x", "y", "y", "y", "z"],
    fixture_guard: [false, false, true, false, true, false],
    retired_metric: [1, 1, 1, 1, 1, 1],
    unknown_metric: [1, 1, 1, 1, 1, 1],
  },
  well_metadata: {
    gene: ["G1", "G1", "G2", "G3", "G3", "G4"],
    well_type: ["query", "query", "query", "control", "control", "query"],
    is_control: [false, false, false, true, true, false],
  },
};

test("derives primary sliders and raw companions entirely from the dictionary", () => {
  const model = buildQcModel(fields, dictionary, { protectionColumn: "fixture_guard" });

  expect(model.sliders.map((slider) => slider.column)).toEqual([
    "sharpness__DNA__score",
    "texture__AGP__score",
    "balance__site__score",
    "brightness__RNA__score",
  ]);
  expect(model.sliders[0]).toMatchObject({
    label: "Sharpness",
    rawColumn: "sharpness__DNA__raw",
    gate: "automatic",
    direction: "low",
    markers: [{ value: 3, label: "registered" }],
    minimum: 1,
    maximum: 9,
  });
  expect(defaultQcFilterState(model).sliders).toMatchObject({
    sharpness__DNA__score: {
      filterLo: 1,
      filterHi: 9,
      flagActive: false,
    },
    texture__AGP__score: {
      flagLo: null,
      flagHi: 8,
      flagActive: true,
    },
    balance__site__score: {
      flagLo: -2,
      flagHi: 2,
      flagActive: true,
    },
    brightness__RNA__score: {
      filterLo: 2,
      filterHi: 7,
      flagActive: false,
    },
  });
  expect(model.dictionary.some((item) => item.column === "retired_metric")).toBe(false);
  expect(model.dictionary.some((item) => item.column === "unknown_metric")).toBe(false);
});

test("combines filter ranges with AND and active review flags with OR", () => {
  const model = buildQcModel(fields, dictionary, { protectionColumn: "fixture_guard" });
  const defaults = defaultQcFilterState(model);
  const sliders = {
    ...defaults.sliders,
    sharpness__DNA__score: {
      ...defaults.sliders?.sharpness__DNA__score,
      filterLo: 4,
    },
  };
  const result = applyQcFilters(model, { ...defaults, sliders });

  expect(result.baseRowIndices).toEqual([1, 2, 3, 4, 5]);
  expect(result.rowIndices).toEqual([2, 3, 4]);
  expect(result.flaggedRowIndices).toEqual([2, 3, 4]);
  expect(result.flaggedBySlider).toEqual({
    texture__AGP__score: 2,
    balance__site__score: 1,
  });
  expect(result.counts).toEqual({
    fields: 3,
    wells: 2,
    controls: 2,
    fieldsUnderProtection: 2,
  });
  expect(result.countsBySlider.texture__AGP__score).toEqual({
    fields: 2,
    wells: 2,
    controls: 1,
    fieldsUnderProtection: 2,
  });

  const filteredAgain = applyQcFilters(model, {
    ...defaults,
    sliders: {
      ...sliders,
      brightness__RNA__score: {
        ...defaults.sliders?.brightness__RNA__score,
        filterHi: 5,
      },
    },
  });
  expect(filteredAgain.baseRowIndices).toEqual([1, 2, 3]);
  expect(filteredAgain.rowIndices).toEqual([2, 3]);
});

test("categorical and channel scopes remain type-safe and automatic gates never flag", () => {
  const model = buildQcModel(fields, dictionary, { protectionColumn: "fixture_guard" });
  const defaults = defaultQcFilterState(model);
  const result = applyQcFilters(model, {
    ...defaults,
    channel: "DNA",
    categories: { plate: [1] },
  });

  expect(result.baseRowIndices).toEqual([0, 1, 2]);
  expect(result.rowIndices).toEqual([0, 1, 2]);
  expect(result.flaggedRowIndices).toEqual([]);
  expect(result.flaggedBySlider).toEqual({});
  expect(uniqueQcValues(model, "plate")).toEqual([1, 2]);
});

test("multi-valued categorical state filters annotation labels without flattening them", () => {
  const model = buildQcModel(fields, dictionary, {
    protectionColumn: "fixture_guard",
    categoryColumns: {
      annotation_status: [
        ["reviewed", "blurred"],
        ["not reviewed"],
        ["reviewed", "ok", "noisy"],
        ["not reviewed"],
        ["reviewed", "ok"],
        ["not reviewed"],
      ],
    },
  });
  const defaults = defaultQcFilterState(model);

  expect(uniqueQcValues(model, "annotation_status")).toEqual([
    "blurred",
    "noisy",
    "not reviewed",
    "ok",
    "reviewed",
  ]);
  expect(
    applyQcFilters(model, {
      ...defaults,
      categories: { annotation_status: ["ok"] },
    }).baseRowIndices,
  ).toEqual([2, 4]);
  expect(
    applyQcFilters(model, {
      ...defaults,
      categories: { annotation_status: ["not reviewed"] },
    }).baseRowIndices,
  ).toEqual([1, 3, 5]);
});

test("aggregates every in-range site for retained wells and sorts stably", () => {
  const model = buildQcModel(fields, dictionary, { protectionColumn: "fixture_guard" });
  const defaults = defaultQcFilterState(model);
  const result = applyQcFilters(model, defaults);
  const wells = aggregateQcWells(model, result, defaults, ["gene", "well_type"]);

  expect(wells.map((well) => well.keyValues.well)).toEqual(["A01", "A02", "B01"]);
  expect(wells.find((well) => well.keyValues.well === "A01")).toMatchObject({
    siteCount: 2,
    flaggedSiteCount: 1,
    values: { gene: "G1", well_type: "query" },
    worstValues: {
      texture__AGP__score: 3,
      balance__site__score: -3,
    },
  });
  expect(wells.find((well) => well.keyValues.well === "B01")).toMatchObject({
    siteCount: 2,
    flaggedSiteCount: 2,
    values: { gene: "G3", well_type: "control" },
    worstValues: {
      texture__AGP__score: 10,
      balance__site__score: 4,
    },
  });
  expect(
    sortQcWells(wells, { column: "flaggedSiteCount", direction: "desc" })[0].keyValues
      .well,
  ).toBe("B01");
  expect(
    sortQcRows(model, result.rowIndices, { column: "site", direction: "desc" }),
  ).toEqual([4, 0, 2, 3]);
});

test("validation errors never echo input column identifiers", () => {
  const inputColumnToken = "fixture-sensitive-column-token";
  let message = "";
  try {
    buildQcModel(
      {
        row_count: 2,
        columns: {
          experiment: ["A", "A"],
          plate: [1, 1],
          well: ["A01", "A01"],
          [inputColumnToken]: [1],
        },
      },
      [],
    );
  } catch (cause) {
    message = cause instanceof Error ? cause.message : String(cause);
  }
  expect(message).toContain("does not match the QC row count");
  expect(message).not.toContain(inputColumnToken);
});

test("filters 31,104 fields in under 100 ms after warmup", () => {
  const rowCount = 31_104;
  const extraMetricRows: QcDictionaryRow[] = Array.from({ length: 15 }, (_, index) => ({
    file: "site_metrics.csv",
    column: `fixture_metric_${index}__site__score`,
    metric: `fixture_metric_${index}`,
    channel: "site",
    scale: "score",
    worse: "high",
    layer: "site_review",
    gate: "none",
    status: "kept",
    primary: true,
  }));
  const extraMetricColumns = Object.fromEntries(
    extraMetricRows.map((row, metricIndex) => [
      String(row.column),
      Array.from({ length: rowCount }, (_, rowIndex) => (rowIndex + metricIndex) % 23),
    ]),
  );
  const largeFields: QcFieldsInput = {
    row_count: rowCount,
    columns: {
      experiment: Array.from({ length: rowCount }, (_, index) => `C${index % 3}`),
      plate: Array.from({ length: rowCount }, (_, index) => Math.floor(index / 3456) + 1),
      well: Array.from(
        { length: rowCount },
        (_, index) =>
          `${String.fromCharCode(65 + (Math.floor(index / 216) % 16))}${String((Math.floor(index / 9) % 24) + 1).padStart(2, "0")}`,
      ),
      site: Array.from({ length: rowCount }, (_, index) => (index % 9) + 1),
      sharpness__DNA__score: Array.from({ length: rowCount }, (_, index) => index % 20),
      sharpness__DNA__raw: Array.from({ length: rowCount }, (_, index) => index % 200),
      texture__AGP__score: Array.from({ length: rowCount }, (_, index) => index % 12),
      texture__AGP__raw: Array.from({ length: rowCount }, (_, index) => index % 120),
      balance__site__score: Array.from({ length: rowCount }, (_, index) => (index % 11) - 5),
      brightness__RNA__score: Array.from({ length: rowCount }, (_, index) => index % 16),
      fixture_context: Array.from({ length: rowCount }, (_, index) => `group-${index % 4}`),
      fixture_guard: Array.from({ length: rowCount }, (_, index) => index % 7 === 0),
      retired_metric: Array(rowCount).fill(1),
      unknown_metric: Array(rowCount).fill(1),
      ...extraMetricColumns,
    },
    well_metadata: {
      gene: Array.from({ length: rowCount }, (_, index) => `G${index % 64}`),
      well_type: Array.from({ length: rowCount }, (_, index) =>
        index % 8 === 0 ? "control" : "query",
      ),
      is_control: Array.from({ length: rowCount }, (_, index) => index % 8 === 0),
    },
  };
  const model = buildQcModel(largeFields, [...dictionary, ...extraMetricRows], {
    protectionColumn: "fixture_guard",
  });
  expect(model.sliders).toHaveLength(19);
  const defaults = defaultQcFilterState(model);
  const state: QcFilterState = {
    ...defaults,
    categories: { experiment: ["C1", "C2"] },
    sliders: {
      ...defaults.sliders,
      sharpness__DNA__score: {
        ...defaults.sliders?.sharpness__DNA__score,
        filterLo: 2,
        filterHi: 18,
      },
      brightness__RNA__score: {
        ...defaults.sliders?.brightness__RNA__score,
        filterLo: 1,
        filterHi: 14,
      },
    },
  };

  for (let warmup = 0; warmup < 3; warmup += 1) applyQcFilters(model, state);
  const timings = Array.from({ length: 5 }, () => {
    const started = performance.now();
    const result = applyQcFilters(model, state);
    expect(result.baseRowIndices.length).toBeGreaterThan(0);
    return performance.now() - started;
  }).sort((left, right) => left - right);

  expect(timings[Math.floor(timings.length / 2)]).toBeLessThan(100);
});
