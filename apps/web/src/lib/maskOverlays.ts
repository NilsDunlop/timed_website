// Segmentation outlines: kind colours and labels, the count line, the model
// choice shared by every viewer source, and outline image URLs.
//
// Outlines are a sanity check on the masks. They never take part in the
// viewer's frame readiness, which describes image pixels only.

import type { components } from "./api/dataserver-schema";

export type MaskModel = components["schemas"]["MaskModelSummary"];
export type MaskCounts = components["schemas"]["MaskFieldResponse"]["counts"];

export const DEFAULT_OUTLINE_WIDTH = 2;
export const MIN_OUTLINE_WIDTH = 1;
export const MAX_OUTLINE_WIDTH = 4;
export const DEFAULT_OUTLINE_OPACITY = 1;

const KIND_COLORS: Readonly<Record<string, string>> = {
  nuclei: "FFD400",
  cells: "FFFFFF",
  cytoplasm: "FF8C00",
};
const FALLBACK_KIND_COLOR = "00E5FF";
const KIND_LABELS: Readonly<Record<string, string>> = {
  nuclei: "Nuclei",
  cells: "Cells",
  cytoplasm: "Cytoplasm",
};
const KIND_NOUNS: Readonly<Record<string, readonly [string, string]>> = {
  nuclei: ["nucleus", "nuclei"],
  cells: ["cell", "cells"],
  cytoplasm: ["cytoplasm", "cytoplasm"],
};

/** The outline colour of a kind as `RRGGBB`, without `#`. */
export function maskKindColor(kind: string): string {
  return KIND_COLORS[kind] ?? FALLBACK_KIND_COLOR;
}

export function maskKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.charAt(0).toUpperCase() + kind.slice(1);
}

/** "731 nuclei · 616 cells · 616 cytoplasm", in the model's kind order. */
export function formatMaskCounts(
  kinds: readonly string[],
  counts: MaskCounts,
): string {
  const ordered = [
    ...kinds.filter((kind) => kind in counts),
    ...Object.keys(counts).filter((kind) => !kinds.includes(kind)),
  ];
  return ordered
    .map((kind) => {
      const count = counts[kind] ?? 0;
      const [singular, plural] = KIND_NOUNS[kind] ?? [kind, kind];
      return `${count.toLocaleString("en-US")} ${count === 1 ? singular : plural}`;
    })
    .join(" · ");
}

/**
 * The models every source's plate offers, the declared default first.
 * `null` for a source means its listing has not arrived yet.
 */
export function sharedMaskModels(
  catalogues: readonly (readonly MaskModel[] | null)[],
): MaskModel[] {
  if (catalogues.length === 0 || catalogues.some((models) => models == null)) return [];
  const [first, ...others] = catalogues as readonly (readonly MaskModel[])[];
  const shared = first.filter(
    (model) =>
      model.available &&
      others.every((models) =>
        models.some((other) => other.model_tag === model.model_tag && other.available),
      ),
  );
  return [...shared].sort(
    (left, right) =>
      Number(right.default) - Number(left.default) ||
      left.model_tag.localeCompare(right.model_tag),
  );
}

/** The requested model when offered, else the default, else the first offered. */
export function chooseMaskModel(
  models: readonly MaskModel[],
  requested: string | null,
): MaskModel | null {
  return (
    models.find((model) => model.model_tag === requested) ??
    models.find((model) => model.default) ??
    models[0] ??
    null
  );
}

export type MaskField = { well: string; site: number };

/** Same-origin outline image of one kind; Cell Health has no timepoint. */
export function maskOutlineUrl(
  datasetId: string,
  model: string,
  field: MaskField,
  kind: string,
  width: number,
): string {
  const path = [
    "backend",
    "api",
    "datasets",
    datasetId,
    "masks",
    model,
    field.well,
    String(field.site),
    `${kind}.png`,
  ]
    .map(encodeURIComponent)
    .join("/");
  const query = new URLSearchParams({
    color: maskKindColor(kind),
    width: String(width),
  });
  return `/${path}?${query.toString()}`;
}
