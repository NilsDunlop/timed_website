import type { components } from "./api/dataserver-schema";
import { wellIdFromPath } from "./fieldPath";

export type PlateMetadata = components["schemas"]["PlateMetadataResponse"];
export type WellMetadata = components["schemas"]["WellMetadataSummary"];
export type PlateMetadataLabels = components["schemas"]["PlateMetadataLabels"];

const defaultLabels: PlateMetadataLabels = {
  compound: "Compound",
  moa: "MOA",
  control: "Control",
};

export function shown(value: string | number | boolean | null | undefined): string {
  if (value == null || value === "") return "Not specified";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

export function hasValue(value: unknown): boolean {
  return value != null && value !== "";
}

export function metadataLabels(
  metadata: PlateMetadata | null | undefined,
): PlateMetadataLabels {
  return metadata?.labels ?? defaultLabels;
}

export function doseLabel(value: number | null | undefined): string {
  if (value == null) return "Not specified";
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 }) + " µM";
}

export function findWellMetadata(
  metadata: PlateMetadata | null,
  selectedWell: string | undefined,
): WellMetadata | undefined {
  if (!selectedWell) return undefined;
  const canonical = wellIdFromPath(selectedWell) ?? selectedWell.toUpperCase();
  return metadata?.wells?.find((well) => well.well.toUpperCase() === canonical);
}
