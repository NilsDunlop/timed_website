import type { QcQueueEntry, QcQueueScope } from "./qcQueue";

export const QC_ANNOTATION_CONFIDENCES = ["low", "medium", "high"] as const;
export const QC_ANNOTATION_REVIEW_STATES = ["not reviewed", "reviewed"] as const;

export type QcAnnotationLabel = string;
export type QcAnnotationConfidence = (typeof QC_ANNOTATION_CONFIDENCES)[number];
export type QcAnnotationReviewState = (typeof QC_ANNOTATION_REVIEW_STATES)[number];
export type QcAnnotationFilter = QcAnnotationReviewState | QcAnnotationLabel;

export type QcAnnotationLabelDefinition = {
  label: string;
  description: string;
};

export type QcCurrentAnnotation = {
  schemaVersion: 1;
  record: "annotation";
  id: string;
  time: string;
  reviewer: string;
  exportId: string;
  registryHash: string;
  scope: QcQueueScope;
  plate: number;
  barcode: string;
  well: string;
  site: number | null;
  channel: string;
  label: QcAnnotationLabel;
  confidence: QcAnnotationConfidence;
  note: string;
  blinded: boolean;
  appVersion: string;
};

export type QcAnnotationRowTarget = {
  scope: QcQueueScope;
  plate: number;
  barcode: string;
  well: string;
  site: number | null;
};

export type QcAnnotationRowState = {
  reviewed: boolean;
  status: QcAnnotationReviewState;
  labels: QcAnnotationLabel[];
  count: number;
};

export type QcAnnotationParseResult = {
  annotations: QcCurrentAnnotation[];
  invalidRecords: number;
};

export type QcAnnotationLabelParseResult = {
  labels: QcAnnotationLabelDefinition[];
  invalidRecords: number;
};

const SAFE_ID = /^[A-Za-z0-9_-]{1,256}$/;
const SAFE_REVIEWER = /^[A-Za-z0-9_-]+$/;
const SAFE_WELL = /^[A-Z]+\d+$/;
const SAFE_LABEL = /^[a-z][a-z0-9_]*$/;
const CONFIDENCES = new Set<string>(QC_ANNOTATION_CONFIDENCES);

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nonemptyText(value: unknown, maximum = 512): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maximum &&
    !/\p{Cc}/u.test(value)
  );
}

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

function isLabel(value: unknown): value is QcAnnotationLabel {
  return typeof value === "string" && value.length <= 100 && SAFE_LABEL.test(value);
}

function isConfidence(value: unknown): value is QcAnnotationConfidence {
  return typeof value === "string" && CONFIDENCES.has(value);
}

function isUtcTime(value: unknown): value is string {
  return (
    typeof value === "string" &&
    (value.endsWith("Z") || value.endsWith("+00:00")) &&
    Number.isFinite(Date.parse(value))
  );
}

export function parseQcAnnotation(
  value: unknown,
  channels: Iterable<string>,
): QcCurrentAnnotation | null {
  const row = objectRecord(value);
  if (!row) return null;
  const availableChannels = new Set(channels);
  const scope = row.scope;
  const site = row.site;
  const fieldSite = positiveInteger(site) ? site : null;
  if (
    row.schema_version !== 1 ||
    row.record !== "annotation" ||
    typeof row.id !== "string" ||
    !SAFE_ID.test(row.id) ||
    !isUtcTime(row.time) ||
    typeof row.reviewer !== "string" ||
    !SAFE_REVIEWER.test(row.reviewer) ||
    !nonemptyText(row.export_id) ||
    !nonemptyText(row.registry_hash) ||
    (scope !== "field" && scope !== "well") ||
    !positiveInteger(row.plate) ||
    !nonemptyText(row.barcode) ||
    typeof row.well !== "string" ||
    !SAFE_WELL.test(row.well) ||
    (scope === "field" ? fieldSite === null : site !== null) ||
    typeof row.channel !== "string" ||
    (row.channel !== "all" && !availableChannels.has(row.channel)) ||
    !isLabel(row.label) ||
    !isConfidence(row.confidence) ||
    typeof row.note !== "string" ||
    row.note.length > 2000 ||
    typeof row.blinded !== "boolean" ||
    !nonemptyText(row.app_version, 128)
  ) {
    return null;
  }

  return {
    schemaVersion: 1,
    record: "annotation",
    id: row.id,
    time: row.time,
    reviewer: row.reviewer,
    exportId: row.export_id,
    registryHash: row.registry_hash,
    scope,
    plate: row.plate,
    barcode: row.barcode,
    well: row.well,
    site: scope === "field" ? fieldSite : null,
    channel: row.channel,
    label: row.label,
    confidence: row.confidence,
    note: row.note,
    blinded: row.blinded,
    appVersion: row.app_version,
  };
}

export function parseQcAnnotations(
  values: unknown,
  channels: Iterable<string>,
): QcAnnotationParseResult {
  if (!Array.isArray(values)) return { annotations: [], invalidRecords: 1 };
  const availableChannels = [...channels];
  const annotations: QcCurrentAnnotation[] = [];
  let invalidRecords = 0;
  for (const value of values) {
    const annotation = parseQcAnnotation(value, availableChannels);
    if (annotation) annotations.push(annotation);
    else invalidRecords += 1;
  }
  return { annotations, invalidRecords };
}

export function parseQcAnnotationLabels(values: unknown): QcAnnotationLabelParseResult {
  if (!Array.isArray(values)) return { labels: [], invalidRecords: 1 };
  const labels: QcAnnotationLabelDefinition[] = [];
  const seen = new Set<string>();
  let invalidRecords = 0;
  for (const value of values) {
    const row = objectRecord(value);
    const label = row?.label;
    const description = row?.description;
    if (
      !isLabel(label) ||
      !nonemptyText(description, 500) ||
      seen.has(label)
    ) {
      invalidRecords += 1;
      continue;
    }
    seen.add(label);
    labels.push({ label, description });
  }
  if (labels.length === 0) invalidRecords += 1;
  return { labels, invalidRecords };
}

function matchesTarget(
  annotation: QcCurrentAnnotation,
  target: QcAnnotationRowTarget,
): boolean {
  return (
    annotation.scope === target.scope &&
    annotation.plate === target.plate &&
    annotation.barcode === target.barcode &&
    annotation.well === target.well &&
    (target.scope === "well" || annotation.site === target.site)
  );
}

export function qcAnnotationRowState(
  annotations: readonly QcCurrentAnnotation[],
  target: QcAnnotationRowTarget,
  labelDefinitions: readonly QcAnnotationLabelDefinition[] = [],
): QcAnnotationRowState {
  const matching = annotations.filter((annotation) => matchesTarget(annotation, target));
  const labelOrder = new Map(
    labelDefinitions.map((definition, index) => [definition.label, index]),
  );
  const labels = [...new Set(matching.map((annotation) => annotation.label))].sort(
    (left, right) => {
      const leftIndex = labelOrder.get(left);
      const rightIndex = labelOrder.get(right);
      if (leftIndex !== undefined && rightIndex !== undefined) return leftIndex - rightIndex;
      if (leftIndex !== undefined) return -1;
      if (rightIndex !== undefined) return 1;
      return left.localeCompare(right);
    },
  );
  return {
    reviewed: matching.length > 0,
    status: matching.length > 0 ? "reviewed" : "not reviewed",
    labels,
    count: matching.length,
  };
}

export function qcAnnotationFilterMatches(
  state: QcAnnotationRowState,
  filter: QcAnnotationFilter,
): boolean {
  if (filter === "reviewed") return state.reviewed;
  if (filter === "not reviewed") return !state.reviewed;
  return state.labels.includes(filter);
}

export function annotationMatchesQueueEntry(
  annotation: QcCurrentAnnotation,
  entry: QcQueueEntry,
): boolean {
  if (
    !matchesTarget(annotation, {
      scope: entry.scope,
      plate: entry.plate,
      barcode: entry.barcode,
      well: entry.well,
      site: entry.scope === "field" ? entry.site : null,
    })
  ) {
    return false;
  }
  return entry.channel === "all"
    ? annotation.channel === "all"
    : annotation.channel === "all" || annotation.channel === entry.channel;
}

export function countAnnotatedQueueEntries(
  entries: readonly QcQueueEntry[],
  annotations: readonly QcCurrentAnnotation[],
): number {
  return entries.reduce(
    (count, entry) =>
      count +
      (annotations.some((annotation) => annotationMatchesQueueEntry(annotation, entry))
        ? 1
        : 0),
    0,
  );
}
