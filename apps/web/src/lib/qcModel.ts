export type QcScalar = string | number | boolean | null;

export interface QcFieldsInput {
  row_count: number;
  columns: Record<string, unknown[]>;
  well_metadata?: Record<string, unknown[]> | null;
}

export type QcDictionaryRow = Record<string, unknown>;
export type QcGate = "automatic" | "review" | "none";
export type QcDirection = "low" | "high" | "both";

export interface QcColumnDefinition {
  column: string;
  file: string;
  label: string;
  metric: string;
  channel: string;
  scale: string;
  layer: string;
  gate: string;
  status: string;
  question: string;
  direction: string;
  primary: boolean;
  row: QcDictionaryRow;
}

export interface QcMarker {
  value: number;
  label: string;
}

export interface QcSliderDefinition extends QcColumnDefinition {
  gate: QcGate;
  direction: QcDirection;
  rawColumn: string | null;
  minimum: number;
  maximum: number;
  defaultFlagLo: number | null;
  defaultFlagHi: number | null;
  markers: readonly QcMarker[];
}

export interface QcModelOptions {
  controlColumn?: string | null;
  protectionColumn?: string | null;
  protectionColumns?: readonly string[];
  isControl?: (value: QcScalar, rowIndex: number) => boolean;
  isProtected?: (value: QcScalar, rowIndex: number) => boolean;
  wellKeyColumns?: readonly string[];
  categoryColumns?: Readonly<
    Record<string, readonly (QcScalar | readonly QcScalar[])[]>
  >;
}

export interface QcModel {
  rowCount: number;
  columns: Readonly<Record<string, readonly QcScalar[]>>;
  dictionary: readonly QcColumnDefinition[];
  sliders: readonly QcSliderDefinition[];
  sliderByColumn: Readonly<Record<string, QcSliderDefinition>>;
  controlColumn: string | null;
  protectionColumns: readonly string[];
  isControl: (value: QcScalar, rowIndex: number) => boolean;
  isProtected: (value: QcScalar, rowIndex: number) => boolean;
  wellKeyColumns: readonly string[];
  categoryColumns: Readonly<Record<string, readonly (readonly QcScalar[])[]>>;
}

export interface QcSliderState {
  filterLo?: number | null;
  filterHi?: number | null;
  flagLo?: number | null;
  flagHi?: number | null;
  flagActive?: boolean;
}

export interface QcFilterState {
  sliders?: Readonly<Record<string, QcSliderState>>;
  categories?: Readonly<Record<string, readonly QcScalar[]>>;
  channel?: string | null;
}

export interface QcCounts {
  fields: number;
  wells: number;
  controls: number;
  fieldsUnderProtection: number;
}

export interface QcFilterResult {
  baseRowIndices: readonly number[];
  rowIndices: readonly number[];
  flaggedRowIndices: readonly number[];
  flaggedBySlider: Readonly<Record<string, number>>;
  counts: QcCounts;
  countsBySlider: Readonly<Record<string, QcCounts>>;
}

export interface QcWellSummary {
  key: string;
  keyValues: Readonly<Record<string, QcScalar>>;
  rowIndices: readonly number[];
  siteCount: number;
  flaggedSiteCount: number;
  values: Readonly<Record<string, QcScalar | readonly QcScalar[]>>;
  worstValues: Readonly<Record<string, number | null>>;
}

export interface QcSort {
  column: string;
  direction?: "asc" | "desc";
}

const VISIBLE_STATUSES = new Set(["", "registered", "kept", "pending_review"]);
const SLIDER_GATES = new Set<QcGate>(["automatic", "review", "none"]);
const SLIDER_DIRECTIONS = new Set<QcDirection>(["low", "high", "both"]);

export class QcModelError extends Error {}

function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value).trim();
}

function scalar(value: unknown): QcScalar {
  if (value === null || value === undefined) return null;
  if (
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return value;
  }
  return String(value);
}

function bool(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  return ["1", "true", "yes", "on"].includes(text(value).toLowerCase());
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function marker(value: unknown): QcMarker | null {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const numeric = finiteNumber(record.value);
    return numeric === null ? null : { value: numeric, label: text(record.label) };
  }
  const numeric = finiteNumber(value);
  return numeric === null ? null : { value: numeric, label: "" };
}

function markers(value: unknown): readonly QcMarker[] {
  if (Array.isArray(value)) {
    return value.map(marker).filter((item): item is QcMarker => item !== null);
  }
  const direct = finiteNumber(value);
  if (direct !== null) return [{ value: direct, label: "" }];
  if (typeof value !== "string" || value.trim() === "") return [];
  try {
    const decoded: unknown = JSON.parse(value);
    if (Array.isArray(decoded)) {
      return decoded.map(marker).filter((item): item is QcMarker => item !== null);
    }
  } catch {
    // Delimited marker strings remain a valid compact CSV representation.
  }
  return value
    .split(/[\s,;|]+/)
    .map(marker)
    .filter((item): item is QcMarker => item !== null);
}

function defaultFlag(value: unknown): { lo: number | null; hi: number | null } {
  if (typeof value !== "string") return { lo: null, hi: null };
  const match = value.trim().match(/^([<>])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?)$/i);
  if (!match) return { lo: null, hi: null };
  const threshold = Number(match[2]);
  if (!Number.isFinite(threshold)) return { lo: null, hi: null };
  return match[1] === "<" ? { lo: threshold, hi: null } : { lo: null, hi: threshold };
}

function applicableDictionaryRow(row: QcDictionaryRow): boolean {
  const status = text(row.status).toLowerCase();
  const layer = text(row.layer).toLowerCase();
  return VISIBLE_STATUSES.has(status) && layer !== "retired";
}

function columnDefinition(row: QcDictionaryRow): QcColumnDefinition | null {
  if (!applicableDictionaryRow(row)) return null;
  const column = text(row.column);
  if (!column) return null;
  const metric = text(row.metric);
  return {
    column,
    file: text(row.file),
    label: text(row.label) || metric || column,
    metric,
    channel: text(row.channel),
    scale: text(row.scale),
    layer: text(row.layer).toLowerCase(),
    gate: text(row.gate).toLowerCase(),
    status: text(row.status).toLowerCase(),
    question: text(row.question),
    direction: text(row.worse).toLowerCase(),
    primary: bool(row.primary),
    row,
  };
}

function numericExtent(values: readonly QcScalar[]): [number, number] {
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    const numeric = finiteNumber(value);
    if (numeric === null) continue;
    minimum = Math.min(minimum, numeric);
    maximum = Math.max(maximum, numeric);
  }
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) {
    throw new QcModelError("A QC slider column contains no finite values");
  }
  return [minimum, maximum];
}

function defaultCategoryPredicate(value: QcScalar): boolean {
  return value === true || value === 1;
}

export function buildQcModel(
  fields: QcFieldsInput,
  dictionaryRows: readonly QcDictionaryRow[],
  options: QcModelOptions = {},
): QcModel {
  if (!Number.isInteger(fields.row_count) || fields.row_count < 0) {
    throw new QcModelError("QC row count must be a non-negative integer");
  }

  const columns: Record<string, readonly QcScalar[]> = {};
  const addColumns = (source: Record<string, unknown[]>, sourceName: string) => {
    for (const [name, values] of Object.entries(source)) {
      if (!Array.isArray(values) || values.length !== fields.row_count) {
        throw new QcModelError(`${sourceName} data does not match the QC row count`);
      }
      if (name in columns) {
        throw new QcModelError("A QC column is present in more than one namespace");
      }
      columns[name] = values.map(scalar);
    }
  };
  addColumns(fields.columns, "export");
  if (fields.well_metadata) addColumns(fields.well_metadata, "well metadata");

  const dictionary = dictionaryRows
    .map(columnDefinition)
    .filter((definition): definition is QcColumnDefinition => definition !== null);
  const sliders: QcSliderDefinition[] = [];
  const categoryColumns: Record<string, readonly (readonly QcScalar[])[]> = {};
  for (const [name, values] of Object.entries(options.categoryColumns ?? {})) {
    if (!Array.isArray(values) || values.length !== fields.row_count) {
      throw new QcModelError("Category data does not match the QC row count");
    }
    categoryColumns[name] = values.map((value) =>
      (Array.isArray(value) ? value : [value]).map(scalar),
    );
  }

  for (const definition of dictionary) {
    if (definition.layer !== "site_review" || !definition.primary) continue;
    if (!(definition.column in columns)) continue;
    if (!SLIDER_GATES.has(definition.gate as QcGate)) {
      throw new QcModelError("A QC slider has an unsupported gate");
    }
    if (!SLIDER_DIRECTIONS.has(definition.direction as QcDirection)) {
      throw new QcModelError("A QC slider has an unsupported direction");
    }
    const [minimum, maximum] = numericExtent(columns[definition.column]);
    const raw = dictionary.find(
      (candidate) =>
        candidate.layer === "site_review" &&
        candidate.metric === definition.metric &&
        candidate.channel === definition.channel &&
        candidate.scale === "raw" &&
        candidate.column in columns,
    );
    const initialFlag = defaultFlag(definition.row.default_flag);
    sliders.push({
      ...definition,
      gate: definition.gate as QcGate,
      direction: definition.direction as QcDirection,
      rawColumn: definition.scale === "raw" ? definition.column : (raw?.column ?? null),
      minimum,
      maximum,
      defaultFlagLo: initialFlag.lo,
      defaultFlagHi: initialFlag.hi,
      markers: markers(definition.row.markers),
    });
  }

  const sliderByColumn = Object.fromEntries(sliders.map((item) => [item.column, item]));
  const requestedWellKeys = options.wellKeyColumns ?? ["experiment", "plate", "well"];
  const wellKeyColumns = requestedWellKeys.filter((column) => column in columns);
  if (wellKeyColumns.length === 0) {
    throw new QcModelError("QC fields do not contain a well key");
  }

  return {
    rowCount: fields.row_count,
    columns,
    dictionary,
    sliders,
    sliderByColumn,
    controlColumn:
      options.controlColumn === undefined
        ? ("is_control" in columns ? "is_control" : null)
        : options.controlColumn,
    protectionColumns:
      options.protectionColumns?.filter((column) => column in columns) ??
      (options.protectionColumn && options.protectionColumn in columns
        ? [options.protectionColumn]
        : []),
    isControl: options.isControl ?? defaultCategoryPredicate,
    isProtected: options.isProtected ?? defaultCategoryPredicate,
    wellKeyColumns,
    categoryColumns,
  };
}

export function defaultQcFilterState(model: QcModel): QcFilterState {
  return {
    sliders: Object.fromEntries(
      model.sliders.map((slider) => [
        slider.column,
        {
          filterLo: slider.minimum,
          filterHi: slider.maximum,
          flagLo: slider.defaultFlagLo,
          flagHi: slider.defaultFlagHi,
          flagActive:
            slider.gate === "review" &&
            (slider.defaultFlagLo !== null || slider.defaultFlagHi !== null),
        },
      ]),
    ),
    categories: {},
    channel: null,
  };
}

interface ResolvedSliderState {
  filterLo: number;
  filterHi: number;
  flagLo: number | null;
  flagHi: number | null;
  flagActive: boolean;
}

function optionalBound(
  state: QcSliderState,
  name: "flagLo" | "flagHi",
  fallback: number | null,
): number | null {
  if (!(name in state)) return fallback;
  return finiteNumber(state[name]);
}

function resolvedSliderState(
  definition: QcSliderDefinition,
  state: QcSliderState | undefined,
): ResolvedSliderState {
  const override = state ?? {};
  const requestedLo = finiteNumber(override.filterLo) ?? definition.minimum;
  const requestedHi = finiteNumber(override.filterHi) ?? definition.maximum;
  const defaultActive =
    definition.gate === "review" &&
    (definition.defaultFlagLo !== null || definition.defaultFlagHi !== null);
  return {
    filterLo: Math.min(requestedLo, requestedHi),
    filterHi: Math.max(requestedLo, requestedHi),
    flagLo: optionalBound(override, "flagLo", definition.defaultFlagLo),
    flagHi: optionalBound(override, "flagHi", definition.defaultFlagHi),
    flagActive: override.flagActive ?? defaultActive,
  };
}

function numberAt(model: QcModel, column: string, rowIndex: number): number | null {
  return finiteNumber(model.columns[column]?.[rowIndex]);
}

function sliderFlags(
  value: number,
  definition: QcSliderDefinition,
  state: ResolvedSliderState,
): boolean {
  if (definition.gate !== "review" || !state.flagActive) return false;
  if (definition.direction === "low") {
    const threshold = state.flagLo ?? state.flagHi;
    return threshold !== null && value < threshold;
  }
  if (definition.direction === "high") {
    const threshold = state.flagHi ?? state.flagLo;
    return threshold !== null && value > threshold;
  }
  return (
    (state.flagLo !== null && value < state.flagLo) ||
    (state.flagHi !== null && value > state.flagHi)
  );
}

function qcValueKey(value: QcScalar): string {
  if (value === null) return "null:";
  return `${typeof value}:${String(value)}`;
}

function rowMatchesCategories(
  model: QcModel,
  rowIndex: number,
  categories: QcFilterState["categories"],
): boolean {
  for (const [column, selected] of Object.entries(categories ?? {})) {
    if (selected.length === 0) continue;
    const values =
      model.categoryColumns[column]?.[rowIndex] ??
      [model.columns[column]?.[rowIndex] ?? null];
    const keys = new Set(values.map(qcValueKey));
    if (!selected.some((candidate) => keys.has(qcValueKey(candidate)))) return false;
  }
  return true;
}

function relevantSliders(model: QcModel, channel: string | null | undefined) {
  return channel
    ? model.sliders.filter((slider) => slider.channel === channel)
    : model.sliders;
}

function wellKey(model: QcModel, rowIndex: number): string {
  return JSON.stringify(
    model.wellKeyColumns.map((column) => model.columns[column]?.[rowIndex] ?? null),
  );
}

function countsForRows(model: QcModel, rowIndices: readonly number[]): QcCounts {
  const wells = new Set<string>();
  let controls = 0;
  let fieldsUnderProtection = 0;
  for (const rowIndex of rowIndices) {
    wells.add(wellKey(model, rowIndex));
    if (
      model.controlColumn &&
      model.isControl(model.columns[model.controlColumn]?.[rowIndex] ?? null, rowIndex)
    ) {
      controls += 1;
    }
    if (
      model.protectionColumns.some((column) =>
        model.isProtected(model.columns[column]?.[rowIndex] ?? null, rowIndex),
      )
    ) {
      fieldsUnderProtection += 1;
    }
  }
  return { fields: rowIndices.length, wells: wells.size, controls, fieldsUnderProtection };
}

export function qcFlaggedSlidersForRow(
  model: QcModel,
  state: QcFilterState,
  rowIndex: number,
): readonly QcSliderDefinition[] {
  if (!Number.isInteger(rowIndex) || rowIndex < 0 || rowIndex >= model.rowCount) {
    return [];
  }
  return relevantSliders(model, state.channel).filter((slider) => {
    if (slider.gate !== "review") return false;
    const resolved = resolvedSliderState(slider, state.sliders?.[slider.column]);
    if (!resolved.flagActive || (resolved.flagLo === null && resolved.flagHi === null)) {
      return false;
    }
    const value = numberAt(model, slider.column, rowIndex);
    return value !== null && sliderFlags(value, slider, resolved);
  });
}

export function applyQcFilters(
  model: QcModel,
  state: QcFilterState = {},
): QcFilterResult {
  const sliders = relevantSliders(model, state.channel);
  const resolved = new Map(
    sliders.map((slider) => [
      slider.column,
      resolvedSliderState(slider, state.sliders?.[slider.column]),
    ]),
  );
  const activeFlags = sliders.filter((slider) => {
    const sliderState = resolved.get(slider.column);
    return (
      slider.gate === "review" &&
      sliderState?.flagActive === true &&
      (sliderState.flagLo !== null || sliderState.flagHi !== null)
    );
  });
  const flaggedRows = Object.fromEntries(
    activeFlags.map((slider) => [slider.column, [] as number[]]),
  );
  const baseRowIndices: number[] = [];
  const rowIndices: number[] = [];
  const flaggedRowIndices: number[] = [];

  for (let rowIndex = 0; rowIndex < model.rowCount; rowIndex += 1) {
    if (!rowMatchesCategories(model, rowIndex, state.categories)) continue;
    let insideFilters = true;
    for (const slider of sliders) {
      const value = numberAt(model, slider.column, rowIndex);
      const sliderState = resolved.get(slider.column);
      if (
        value === null ||
        sliderState === undefined ||
        value < sliderState.filterLo ||
        value > sliderState.filterHi
      ) {
        insideFilters = false;
        break;
      }
    }
    if (!insideFilters) continue;
    baseRowIndices.push(rowIndex);

    let flagged = false;
    for (const slider of activeFlags) {
      const value = numberAt(model, slider.column, rowIndex);
      const sliderState = resolved.get(slider.column);
      if (value !== null && sliderState && sliderFlags(value, slider, sliderState)) {
        flaggedRows[slider.column].push(rowIndex);
        flagged = true;
      }
    }
    if (flagged) flaggedRowIndices.push(rowIndex);
    if (activeFlags.length === 0 || flagged) rowIndices.push(rowIndex);
  }

  return {
    baseRowIndices,
    rowIndices,
    flaggedRowIndices,
    flaggedBySlider: Object.fromEntries(
      Object.entries(flaggedRows).map(([column, rows]) => [column, rows.length]),
    ),
    counts: countsForRows(model, rowIndices),
    countsBySlider: Object.fromEntries(
      Object.entries(flaggedRows).map(([column, rows]) => [column, countsForRows(model, rows)]),
    ),
  };
}

export function uniqueQcValues(model: QcModel, column: string): readonly QcScalar[] {
  const unique = new Map<string, QcScalar>();
  const categoryValues = model.categoryColumns[column];
  if (categoryValues) {
    for (const values of categoryValues) {
      for (const value of values) unique.set(qcValueKey(value), value);
    }
  } else {
    for (const value of model.columns[column] ?? []) unique.set(qcValueKey(value), value);
  }
  return [...unique.values()].sort(compareQcValues);
}

function compareQcValues(left: QcScalar, right: QcScalar): number {
  if (left === null) return right === null ? 0 : 1;
  if (right === null) return -1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  if (typeof left === "boolean" && typeof right === "boolean") {
    return Number(left) - Number(right);
  }
  return String(left).localeCompare(String(right), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

export function sortQcRows(
  model: QcModel,
  rowIndices: readonly number[],
  sort: QcSort,
): readonly number[] {
  const direction = sort.direction === "desc" ? -1 : 1;
  const values = model.columns[sort.column] ?? [];
  return [...rowIndices].sort((left, right) => {
    const compared = compareQcValues(values[left] ?? null, values[right] ?? null);
    return compared === 0 ? left - right : compared * direction;
  });
}

function uniqueSummary(values: readonly QcScalar[]): QcScalar | readonly QcScalar[] {
  const unique = new Map<string, QcScalar>();
  for (const value of values) unique.set(qcValueKey(value), value);
  const result = [...unique.values()].sort(compareQcValues);
  return result.length === 1 ? result[0] : result;
}

function worstValue(
  values: readonly number[],
  definition: QcSliderDefinition,
  state: ResolvedSliderState,
): number | null {
  if (values.length === 0) return null;
  if (definition.direction === "low") return Math.min(...values);
  if (definition.direction === "high") return Math.max(...values);
  let selected = values[0];
  let selectedSeverity = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    const severity = Math.max(
      state.flagLo === null ? 0 : state.flagLo - value,
      state.flagHi === null ? 0 : value - state.flagHi,
      0,
    );
    if (severity > selectedSeverity) {
      selected = value;
      selectedSeverity = severity;
    }
  }
  return selected;
}

export function aggregateQcWells(
  model: QcModel,
  result: QcFilterResult,
  state: QcFilterState = {},
  summaryColumns: readonly string[] = [],
): readonly QcWellSummary[] {
  const retainedKeys = new Set(
    result.rowIndices.map((rowIndex) => wellKey(model, rowIndex)),
  );
  const grouped = new Map<string, number[]>();
  for (const rowIndex of result.baseRowIndices) {
    const key = wellKey(model, rowIndex);
    if (!retainedKeys.has(key)) continue;
    const rows = grouped.get(key);
    if (rows) rows.push(rowIndex);
    else grouped.set(key, [rowIndex]);
  }
  const flagged = new Set(result.flaggedRowIndices);
  const sliders = relevantSliders(model, state.channel).filter((slider) => {
    const resolved = resolvedSliderState(slider, state.sliders?.[slider.column]);
    return slider.gate === "review" && resolved.flagActive;
  });

  return [...grouped.entries()].map(([key, rowIndices]) => {
    const first = rowIndices[0];
    const keyValues = Object.fromEntries(
      model.wellKeyColumns.map((column) => [column, model.columns[column]?.[first] ?? null]),
    );
    const values = Object.fromEntries(
      summaryColumns.map((column) => [
        column,
        uniqueSummary(rowIndices.map((rowIndex) => model.columns[column]?.[rowIndex] ?? null)),
      ]),
    );
    const worstValues = Object.fromEntries(
      sliders.map((slider) => {
        const numeric = rowIndices
          .map((rowIndex) => numberAt(model, slider.column, rowIndex))
          .filter((value): value is number => value !== null);
        return [
          slider.column,
          worstValue(
            numeric,
            slider,
            resolvedSliderState(slider, state.sliders?.[slider.column]),
          ),
        ];
      }),
    );
    return {
      key,
      keyValues,
      rowIndices,
      siteCount: rowIndices.length,
      flaggedSiteCount: rowIndices.filter((rowIndex) => flagged.has(rowIndex)).length,
      values,
      worstValues,
    };
  });
}

function wellSortValue(well: QcWellSummary, column: string): QcScalar {
  if (column === "siteCount") return well.siteCount;
  if (column === "flaggedSiteCount") return well.flaggedSiteCount;
  const value = well.worstValues[column] ?? well.values[column] ?? well.keyValues[column] ?? null;
  return Array.isArray(value) ? value.map(String).join(", ") : (value as QcScalar);
}

export function sortQcWells(
  wells: readonly QcWellSummary[],
  sort: QcSort,
): readonly QcWellSummary[] {
  const direction = sort.direction === "desc" ? -1 : 1;
  return [...wells].sort((left, right) => {
    const compared = compareQcValues(
      wellSortValue(left, sort.column),
      wellSortValue(right, sort.column),
    );
    return compared === 0 ? left.key.localeCompare(right.key) : compared * direction;
  });
}
