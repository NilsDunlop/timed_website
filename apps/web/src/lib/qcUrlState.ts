export type QcTableMode = "wells" | "sites";

export type QcSortDirection = "asc" | "desc";

export type QcSliderUrlState = {
  lower: number | null;
  upper: number | null;
  flagActive: boolean;
};

export type QcUrlState = {
  collection: string | null;
  plate: number | null;
  channel: string | null;
  tableMode: QcTableMode;
  sortColumn: string | null;
  sortDirection: QcSortDirection;
  sliderSearch: string;
  showDetails: boolean;
  sliders: Record<string, QcSliderUrlState>;
  categories: Record<string, string[]>;
};

export type QcUrlAvailability = {
  sliderIds: Iterable<string>;
  categoryIds: Iterable<string>;
  sortableIds?: Iterable<string>;
  collectionIds?: Iterable<string>;
  channels?: Iterable<string>;
  plates?: Iterable<number>;
};

const SLIDER_PREFIX = "qc.slider.";
const CATEGORY_PREFIX = "qc.category.";

function asSet<T>(values: Iterable<T> | undefined): Set<T> | null {
  return values === undefined ? null : new Set(values);
}

function optionalValue(
  value: string | null,
  available: Set<string> | null,
): string | null {
  if (!value || (available && !available.has(value))) return null;
  return value;
}

function parseFinite(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Object.is(value, -0) ? 0 : value;
}

function parseSlider(value: string): QcSliderUrlState | null {
  let candidate: unknown;
  try {
    candidate = JSON.parse(value);
  } catch {
    return null;
  }
  if (!Array.isArray(candidate) || candidate.length !== 3) return null;
  const [rawLower, rawUpper, rawActive] = candidate;
  const lower = rawLower === null ? null : parseFinite(rawLower);
  const upper = rawUpper === null ? null : parseFinite(rawUpper);
  if (
    (rawLower !== null && lower === null) ||
    (rawUpper !== null && upper === null) ||
    typeof rawActive !== "boolean" ||
    (lower !== null && upper !== null && lower > upper)
  ) {
    return null;
  }
  return { lower, upper, flagActive: rawActive };
}

function canonicalCategoryValues(values: Iterable<string>): string[] {
  return [...new Set([...values].filter((value) => value.length > 0))].sort(
    (left, right) => left.localeCompare(right),
  );
}

export function emptyQcUrlState(): QcUrlState {
  return {
    collection: null,
    plate: null,
    channel: null,
    tableMode: "sites",
    sortColumn: null,
    sortDirection: "asc",
    sliderSearch: "",
    showDetails: false,
    sliders: {},
    categories: {},
  };
}

export function parseQcUrlState(
  search: string | URLSearchParams,
  availability: QcUrlAvailability,
): QcUrlState {
  const query =
    typeof search === "string"
      ? new URLSearchParams(search.startsWith("?") ? search.slice(1) : search)
      : new URLSearchParams(search);
  const sliderIds = new Set(availability.sliderIds);
  const categoryIds = new Set(availability.categoryIds);
  const sortableIds = asSet(availability.sortableIds);
  const collectionIds = asSet(availability.collectionIds);
  const channels = asSet(availability.channels);
  const plates = asSet(availability.plates);
  const state = emptyQcUrlState();

  state.collection = optionalValue(query.get("collection"), collectionIds);
  state.channel = optionalValue(query.get("qc.channel"), channels);
  state.sliderSearch = query.get("qc.search") ?? "";
  state.showDetails = query.get("qc.details") === "1";
  state.tableMode = query.get("qc.table") === "wells" ? "wells" : "sites";

  const rawPlate = query.get("qc.plate");
  if (rawPlate && /^\d+$/.test(rawPlate)) {
    const plate = Number(rawPlate);
    if (Number.isSafeInteger(plate) && plate > 0 && (!plates || plates.has(plate))) {
      state.plate = plate;
    }
  }

  state.sortColumn = optionalValue(query.get("qc.sort"), sortableIds);
  if (state.sortColumn && query.get("qc.direction") === "desc") {
    state.sortDirection = "desc";
  }

  for (const [key, value] of query) {
    if (key.startsWith(SLIDER_PREFIX)) {
      const id = key.slice(SLIDER_PREFIX.length);
      if (sliderIds.has(id) && !(id in state.sliders)) {
        const slider = parseSlider(value);
        if (slider) state.sliders[id] = slider;
      }
      continue;
    }
    if (key.startsWith(CATEGORY_PREFIX)) {
      const id = key.slice(CATEGORY_PREFIX.length);
      if (!categoryIds.has(id) || value.length === 0) continue;
      (state.categories[id] ??= []).push(value);
    }
  }

  for (const [id, values] of Object.entries(state.categories)) {
    state.categories[id] = canonicalCategoryValues(values);
  }
  return state;
}

function canonicalSlider(
  slider: QcSliderUrlState,
): QcSliderUrlState | null {
  const lower = slider.lower === null ? null : parseFinite(slider.lower);
  const upper = slider.upper === null ? null : parseFinite(slider.upper);
  if (
    (slider.lower !== null && lower === null) ||
    (slider.upper !== null && upper === null) ||
    (lower !== null && upper !== null && lower > upper)
  ) {
    return null;
  }
  return { lower, upper, flagActive: Boolean(slider.flagActive) };
}

export function qcUrlSearch(state: QcUrlState): string {
  const query = new URLSearchParams();
  if (state.collection) query.set("collection", state.collection);
  if (state.plate !== null && Number.isSafeInteger(state.plate) && state.plate > 0) {
    query.set("qc.plate", String(state.plate));
  }
  if (state.channel) query.set("qc.channel", state.channel);
  if (state.tableMode === "wells") query.set("qc.table", "wells");
  if (state.sortColumn) {
    query.set("qc.sort", state.sortColumn);
    if (state.sortDirection === "desc") query.set("qc.direction", "desc");
  }
  if (state.sliderSearch) query.set("qc.search", state.sliderSearch);
  if (state.showDetails) query.set("qc.details", "1");

  for (const id of Object.keys(state.sliders).sort()) {
    if (!id) continue;
    const slider = canonicalSlider(state.sliders[id]);
    if (!slider) continue;
    query.set(
      SLIDER_PREFIX + id,
      JSON.stringify([slider.lower, slider.upper, slider.flagActive]),
    );
  }
  for (const id of Object.keys(state.categories).sort()) {
    if (!id) continue;
    for (const value of canonicalCategoryValues(state.categories[id])) {
      query.append(CATEGORY_PREFIX + id, value);
    }
  }

  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}
