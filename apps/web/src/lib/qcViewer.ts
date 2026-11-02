export type QcViewerScalar = string | number | boolean | null;

export type QcFieldPayloadLike = {
  export_id: string;
  registry_hash: string;
  blinded: boolean;
  field: Record<string, unknown>;
  dictionary: readonly Record<string, unknown>[];
  well_metadata?: Record<string, unknown> | null;
};

export type QcFieldMetric = {
  column: string;
  label: string;
  metric: string;
  channel: string;
  scale: string;
  direction: string;
  question: string;
  value: QcViewerScalar;
  rawColumn: string | null;
  rawValue: QcViewerScalar;
};

export type QcMetricGroup = {
  channel: string;
  metrics: readonly QcFieldMetric[];
};

export type QcFieldDetail = {
  column: string;
  label: string;
  question: string;
  value: QcViewerScalar;
};

export type QcFieldViewModel = {
  metricGroups: readonly QcMetricGroup[];
  context: readonly QcFieldDetail[];
  attributes: readonly QcFieldDetail[];
};

export type QcDisplayRange = {
  plate: number;
  plateBarcode: string;
  channel: string;
  low: number;
  high: number;
  displayMode: string;
  quantileLow: number;
  quantileHigh: number;
  referenceFields: number;
};

export type QcContrastChannel = {
  name: string;
  contrast: [number, number];
};

export type QcSiteOption = {
  site: number;
  path: string;
};

export type QcSiteStepper = {
  options: readonly QcSiteOption[];
  index: number;
  current: QcSiteOption;
  previous: QcSiteOption | null;
  next: QcSiteOption | null;
};

const VISIBLE_STATUSES = new Set(["", "registered", "kept", "pending_review"]);

function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value).trim();
}

function bool(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  return ["1", "true", "yes", "on"].includes(text(value).toLocaleLowerCase());
}

function scalar(value: unknown): QcViewerScalar {
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

function finite(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function qcDisplayLabel(value: string): string {
  const label = value
    .replace(/__/g, " · ")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toLocaleUpperCase());
  if (/^D62:\s*Share Excluded Automatically$/i.test(label)) {
    return "Automatic exclusion";
  }
  if (/^D62:\s*Share Flagged For Review$/i.test(label)) {
    return "Flagged for review";
  }
  return label.replace(/^D62\b\s*:?\s*/i, "") || label;
}

export function qcMetricQuestionColumns(
  metrics: readonly QcFieldMetric[],
): ReadonlySet<string> {
  const seen = new Set<string>();
  const columns = new Set<string>();
  for (const metric of metrics) {
    const identity = metric.metric || qcDisplayLabel(metric.label);
    if (!metric.question || seen.has(identity)) continue;
    seen.add(identity);
    columns.add(metric.column);
  }
  return columns;
}

function applicable(row: Record<string, unknown>): boolean {
  const status = text(row.status).toLocaleLowerCase();
  const layer = text(row.layer).toLocaleLowerCase();
  return VISIBLE_STATUSES.has(status) && layer !== "retired";
}

function fieldDetail(
  row: Record<string, unknown>,
  field: Record<string, unknown>,
): QcFieldDetail | null {
  const column = text(row.column);
  if (!column || !(column in field)) return null;
  const metric = text(row.metric);
  return {
    column,
    label: text(row.label) || metric || column,
    question: text(row.question),
    value: scalar(field[column]),
  };
}

export function buildQcFieldView(payload: QcFieldPayloadLike): QcFieldViewModel {
  const dictionary = payload.dictionary.filter(applicable);
  const metricRows = dictionary.filter(
    (row) =>
      text(row.layer).toLocaleLowerCase() === "site_review" && bool(row.primary),
  );
  const grouped = new Map<string, QcFieldMetric[]>();

  for (const row of metricRows) {
    const column = text(row.column);
    if (!column || !(column in payload.field)) continue;
    const metric = text(row.metric);
    const sourceChannel = text(row.channel);
    const channel =
      !sourceChannel || sourceChannel.toLocaleLowerCase() === "site"
        ? "Site"
        : sourceChannel;
    const scale = text(row.scale);
    const raw = dictionary.find(
      (candidate) =>
        candidate !== row &&
        text(candidate.layer).toLocaleLowerCase() === "site_review" &&
        text(candidate.metric) === metric &&
        text(candidate.channel) === sourceChannel &&
        text(candidate.scale).toLocaleLowerCase() === "raw" &&
        text(candidate.column) in payload.field,
    );
    const rawColumn = raw ? text(raw.column) : null;
    const item: QcFieldMetric = {
      column,
      label: text(row.label) || metric || column,
      metric,
      channel,
      scale,
      direction: text(row.worse).toLocaleLowerCase(),
      question: text(row.question),
      value: scalar(payload.field[column]),
      rawColumn,
      rawValue: rawColumn ? scalar(payload.field[rawColumn]) : null,
    };
    const entries = grouped.get(channel);
    if (entries) entries.push(item);
    else grouped.set(channel, [item]);
  }

  const context = dictionary.flatMap((row) => {
    if (text(row.layer).toLocaleLowerCase() !== "context") return [];
    const entry = fieldDetail(row, payload.field);
    return entry ? [entry] : [];
  });
  const attributes = Object.entries(payload.well_metadata ?? {}).map(
    ([column, value]): QcFieldDetail => ({
      column,
      label: column,
      question: "",
      value: scalar(value),
    }),
  );

  return {
    metricGroups: [...grouped.entries()].map(([channel, metrics]) => ({
      channel,
      metrics,
    })),
    context,
    attributes,
  };
}

function displayRange(row: Record<string, unknown>): QcDisplayRange | null {
  const plate = finite(row.plate);
  const low = finite(row.display_lo);
  const high = finite(row.display_hi);
  const quantileLow = finite(row.quantile_lo);
  const quantileHigh = finite(row.quantile_hi);
  const referenceFields = finite(row.reference_fields);
  const channel = text(row.channel);
  const displayMode = text(row.display_mode);
  if (
    plate === null ||
    !Number.isInteger(plate) ||
    !channel ||
    low === null ||
    high === null ||
    low >= high ||
    quantileLow === null ||
    quantileHigh === null ||
    quantileLow < 0 ||
    quantileLow >= quantileHigh ||
    quantileHigh > 1 ||
    referenceFields === null ||
    !Number.isInteger(referenceFields) ||
    referenceFields <= 0 ||
    displayMode !== "fixed per plate x channel"
  ) {
    return null;
  }
  return {
    plate,
    plateBarcode: text(row.plate_barcode),
    channel,
    low,
    high,
    displayMode,
    quantileLow,
    quantileHigh,
    referenceFields,
  };
}

export function selectQcDisplayRanges(
  rows: readonly Record<string, unknown>[],
  plate: number,
  channels: readonly string[],
): QcDisplayRange[] {
  const requested = new Set(channels);
  const matches = rows.flatMap((row) => {
    const parsed = displayRange(row);
    return parsed && parsed.plate === plate && requested.has(parsed.channel)
      ? [parsed]
      : [];
  });
  const byChannel = new Map<string, QcDisplayRange>();
  for (const match of matches) {
    if (byChannel.has(match.channel)) {
      throw new Error("The QC display ranges contain a duplicate channel");
    }
    byChannel.set(match.channel, match);
  }
  const selected = channels.map((channel) => byChannel.get(channel));
  if (selected.some((item) => item === undefined)) {
    throw new Error("The QC display ranges do not cover every image channel");
  }
  return selected as QcDisplayRange[];
}

export function copyQcContrastChannels<T extends QcContrastChannel>(
  channels: readonly T[],
): T[] {
  return channels.map((channel) => ({
    ...channel,
    contrast: [...channel.contrast] as [number, number],
  }));
}

export function applyQcDisplayRanges<T extends QcContrastChannel>(
  channels: readonly T[],
  ranges: readonly QcDisplayRange[],
): T[] {
  const byChannel = new Map(ranges.map((range) => [range.channel, range]));
  return channels.map((channel) => {
    const range = byChannel.get(channel.name);
    if (!range) throw new Error("The QC display ranges do not cover every image channel");
    return { ...channel, contrast: [range.low, range.high] };
  });
}

export function restoreDatasetContrasts<T extends QcContrastChannel>(
  channels: readonly T[],
  defaults: readonly QcContrastChannel[],
): T[] {
  const byChannel = new Map(defaults.map((channel) => [channel.name, channel.contrast]));
  return channels.map((channel) => {
    const contrast = byChannel.get(channel.name);
    if (!contrast) throw new Error("Dataset-default contrast is missing for an image channel");
    return { ...channel, contrast: [...contrast] as [number, number] };
  });
}

function siteFromPath(path: string): number | null {
  const match = /_s(\d+)\.zarr$/i.exec(path);
  if (!match?.[1]) return null;
  const site = Number.parseInt(match[1], 10);
  return Number.isInteger(site) && site > 0 ? site : null;
}

export function qcSiteOptions(
  fields: readonly { path: string; acquisition?: number | string | null }[],
): QcSiteOption[] {
  const bySite = new Map<number, QcSiteOption>();
  for (const field of fields) {
    const fromPath = siteFromPath(field.path);
    const fromAcquisition = finite(field.acquisition);
    const site =
      fromPath ??
      (fromAcquisition !== null && Number.isInteger(fromAcquisition) && fromAcquisition > 0
        ? fromAcquisition
        : null);
    if (site === null || bySite.has(site)) continue;
    bySite.set(site, { site, path: field.path });
  }
  return [...bySite.values()].sort((left, right) => left.site - right.site);
}

export function buildQcSiteStepper(
  options: readonly QcSiteOption[],
  currentSite: number,
): QcSiteStepper | null {
  const index = options.findIndex((option) => option.site === currentSite);
  const current = options[index];
  if (index < 0 || !current) return null;
  return {
    options,
    index,
    current,
    previous: options[index - 1] ?? null,
    next: options[index + 1] ?? null,
  };
}

export function qcSiteHref(search: string, option: QcSiteOption): string {
  const query = new URLSearchParams(search);
  query.set("path", option.path);
  query.set("qcSite", String(option.site));
  return `/view?${query.toString()}`;
}
