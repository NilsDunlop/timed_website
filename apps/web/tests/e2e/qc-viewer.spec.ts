import { expect, test } from "@playwright/test";

import {
  applyQcDisplayRanges,
  buildQcFieldView,
  buildQcSiteStepper,
  copyQcContrastChannels,
  qcDisplayLabel,
  qcDisplayQuestion,
  qcMetricQuestionColumns,
  qcSiteHref,
  qcSiteOptions,
  restoreDatasetContrasts,
  selectQcDisplayRanges,
  type QcFieldPayloadLike,
} from "../../src/lib/qcViewer";

test("uses operator-facing labels for automatic exclusion measures", () => {
  expect(qcDisplayLabel("control_z")).toBe("Relative to controls");
  expect(qcDisplayLabel("D62: share excluded automatically")).toBe(
    "Automatic exclusion",
  );
  expect(qcDisplayLabel("D62: share flagged for review")).toBe(
    "Flagged for review",
  );
  expect(qcDisplayLabel("D62: Focus Score Normvar")).toBe("Focus Score Normvar");
  expect(
    qcDisplayQuestion(
      "Is the field's DNA normalised variance below the D62 share of the plate's clean-EMPTY median?",
    ),
  ).toBe(
    "Is the field's DNA normalised variance below the configured share of the plate's clean-EMPTY median?",
  );
});

test("shows one explanation for repeated channel instances of a metric", () => {
  const metric = (column: string, channel: string, metricName: string) => ({
    column,
    label: metricName,
    metric: metricName,
    channel,
    scale: "control_z",
    direction: "low",
    question: `How should ${metricName} be interpreted?`,
    value: 0,
    rawColumn: null,
    rawValue: null,
  });
  expect(
    [...qcMetricQuestionColumns([
      metric("focus_dna", "DNA", "focus"),
      metric("focus_er", "ER", "focus"),
      metric("brightness_site", "Site", "brightness"),
    ])],
  ).toEqual(["focus_dna", "brightness_site"]);
});

const fieldPayload: QcFieldPayloadLike = {
  export_id: "fixture-export",
  registry_hash: "fixture-registry",
  blinded: false,
  field: {
    plate: 14610,
    well: "A01",
    site: 1,
    focus__DNA__raw: 12,
    focus__DNA__control_z: null,
    automatic__site__raw: 0.75,
    context_count: 21,
    retired_value: 99,
    future_value: 88,
  },
  dictionary: [
    {
      column: "focus__DNA__raw",
      metric: "focus",
      channel: "DNA",
      scale: "raw",
      worse: "low",
      layer: "site_review",
      gate: "review",
      status: "pending_review",
      question: "Is the field focused?",
      primary: false,
    },
    {
      column: "focus__DNA__control_z",
      label: "Focus",
      metric: "focus",
      channel: "DNA",
      scale: "control_z",
      worse: "low",
      layer: "site_review",
      gate: "review",
      status: "pending_review",
      question: "Is the normalized focus too low?",
      primary: true,
    },
    {
      column: "automatic__site__raw",
      label: "Automatic state",
      metric: "automatic",
      channel: "site",
      scale: "raw",
      worse: "high",
      layer: "site_review",
      gate: "automatic",
      status: "registered",
      question: "Did the upstream rule select this field?",
      primary: true,
    },
    {
      column: "context_count",
      label: "Context count",
      layer: "context",
      status: "kept",
      question: "How many objects were counted?",
    },
    {
      column: "retired_value",
      layer: "retired",
      status: "retired",
      question: "Retired.",
    },
    {
      column: "future_value",
      layer: "context",
      status: "future_status",
      question: "Unknown.",
    },
  ],
  well_metadata: {
    gene: "EMPTY_control",
    is_control: true,
    optional_attribute: null,
  },
};

const rangeRows = [
  {
    plate: 14610,
    plate_barcode: "SQ00014610",
    channel: "DNA",
    display_lo: 101,
    display_hi: 5250,
    display_mode: "fixed per plate x channel",
    quantile_lo: 0.01,
    quantile_hi: 0.995,
    reference_fields: 10,
  },
  {
    plate: 14610,
    plate_barcode: "SQ00014610",
    channel: "AGP",
    display_lo: 103,
    display_hi: 5750,
    display_mode: "fixed per plate x channel",
    quantile_lo: 0.01,
    quantile_hi: 0.995,
    reference_fields: 10,
  },
  {
    plate: 14611,
    plate_barcode: "SQ00014611",
    channel: "DNA",
    display_lo: 201,
    display_hi: 6250,
    display_mode: "fixed per plate x channel",
    quantile_lo: 0.01,
    quantile_hi: 0.995,
    reference_fields: 10,
  },
];

test("builds dictionary-driven metric groups, context, and every supplied attribute", () => {
  const view = buildQcFieldView(fieldPayload);

  expect(view.metricGroups).toEqual([
    {
      channel: "DNA",
      metrics: [
        {
          column: "focus__DNA__control_z",
          label: "Focus",
          metric: "focus",
          channel: "DNA",
          scale: "control_z",
          direction: "low",
          question: "Is the normalized focus too low?",
          value: null,
          rawColumn: "focus__DNA__raw",
          rawValue: 12,
        },
      ],
    },
    {
      channel: "Site",
      metrics: [
        {
          column: "automatic__site__raw",
          label: "Automatic state",
          metric: "automatic",
          channel: "Site",
          scale: "raw",
          direction: "high",
          question: "Did the upstream rule select this field?",
          value: 0.75,
          rawColumn: null,
          rawValue: null,
        },
      ],
    },
  ]);
  expect(view.context).toEqual([
    {
      column: "context_count",
      label: "Context count",
      question: "How many objects were counted?",
      value: 21,
    },
  ]);
  expect(view.attributes.map((item) => [item.column, item.value])).toEqual([
    ["gene", "EMPTY_control"],
    ["is_control", true],
    ["optional_attribute", null],
  ]);
});

test("selects exact plate and channel ranges and preserves non-contrast channel state", () => {
  const selected = selectQcDisplayRanges(rangeRows, 14610, ["DNA", "AGP"]);
  expect(selected.map((range) => [range.channel, range.low, range.high])).toEqual([
    ["DNA", 101, 5250],
    ["AGP", 103, 5750],
  ]);
  expect(selected[0]).toMatchObject({
    plateBarcode: "SQ00014610",
    displayMode: "fixed per plate x channel",
    quantileLow: 0.01,
    quantileHigh: 0.995,
    referenceFields: 10,
  });

  const original = [
    { name: "DNA", contrast: [292, 11552] as [number, number], visible: true, color: [1, 2, 3] },
    { name: "AGP", contrast: [554, 9283] as [number, number], visible: false, color: [4, 5, 6] },
  ];
  const defaults = copyQcContrastChannels(original);
  const qc = applyQcDisplayRanges(original, selected);
  expect(qc).toEqual([
    { ...original[0], contrast: [101, 5250] },
    { ...original[1], contrast: [103, 5750] },
  ]);
  const changedVisibility = qc.map((channel, index) => ({
    ...channel,
    visible: index === 0 ? false : true,
  }));
  expect(restoreDatasetContrasts(changedVisibility, defaults)).toEqual([
    { ...original[0], visible: false },
    { ...original[1], visible: true },
  ]);
  expect(defaults).toEqual(original);
  expect(defaults).not.toBe(original);
});

test("rejects incomplete, duplicate, and malformed display ranges", () => {
  expect(() => selectQcDisplayRanges(rangeRows, 14610, ["DNA", "RNA"])).toThrow(
    /cover every image channel/,
  );
  expect(() =>
    selectQcDisplayRanges([...rangeRows, { ...rangeRows[0] }], 14610, ["DNA"]),
  ).toThrow(/duplicate channel/);
  expect(() =>
    selectQcDisplayRanges(
      [{ ...rangeRows[0], display_lo: Number.NaN }],
      14610,
      ["DNA"],
    ),
  ).toThrow(/cover every image channel/);
  expect(() =>
    selectQcDisplayRanges(
      [{ ...rangeRows[0], quantile_lo: 0.995, quantile_hi: 0.01 }],
      14610,
      ["DNA"],
    ),
  ).toThrow(/cover every image channel/);
  expect(() =>
    selectQcDisplayRanges(
      [{ ...rangeRows[0], display_mode: "adaptive" }],
      14610,
      ["DNA"],
    ),
  ).toThrow(/cover every image channel/);
});

test("derives an ordered site stepper from real field paths and rewrites only site state", () => {
  const options = qcSiteOptions([
    { path: "A01_s2.zarr", acquisition: 99 },
    { path: "A01_s1.zarr", acquisition: 98 },
    { path: "fallback-field.zarr", acquisition: 3 },
    { path: "duplicate_s2.zarr", acquisition: 2 },
  ]);
  expect(options).toEqual([
    { site: 1, path: "A01_s1.zarr" },
    { site: 2, path: "A01_s2.zarr" },
    { site: 3, path: "fallback-field.zarr" },
  ]);
  expect(buildQcSiteStepper(options, 1)).toMatchObject({
    index: 0,
    previous: null,
    next: { site: 2, path: "A01_s2.zarr" },
  });
  expect(buildQcSiteStepper(options, 3)).toMatchObject({
    index: 2,
    previous: { site: 2, path: "A01_s2.zarr" },
    next: null,
  });
  expect(buildQcSiteStepper(options, 9)).toBeNull();

  const href = qcSiteHref(
    "?dataset=linked&path=A01_s1.zarr&collection=idr0080&qc=1&qcSite=1&qcReturn=%2Fqc%3Fcollection%3Didr0080",
    options[1],
  );
  const url = new URL(href, "http://timed.test");
  expect(url.pathname).toBe("/view");
  expect(url.searchParams.get("dataset")).toBe("linked");
  expect(url.searchParams.get("collection")).toBe("idr0080");
  expect(url.searchParams.get("qc")).toBe("1");
  expect(url.searchParams.get("qcReturn")).toBe("/qc?collection=idr0080");
  expect(url.searchParams.get("path")).toBe("A01_s2.zarr");
  expect(url.searchParams.get("qcSite")).toBe("2");
});
