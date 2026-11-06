import { siteIdFromPath, wellIdFromPath } from "./fieldPath";
import { doseLabel, findWellMetadata, metadataLabels } from "./plateMetadata";
import type { FieldSummary, ViewerSourceState } from "./viewerSources";

export type ViewerSourceIdentity = {
  compoundLabel: string;
  compound: string;
  concentration: string | null;
  well: string;
  site: string;
  plate: string;
};

export function sitePositionFixNote(
  fix: FieldSummary["site_position_fix"],
): string | null {
  if (!fix) return null;
  const label = fix.repaired_timepoints.length === 1 ? "timepoint" : "timepoints";
  return (
    "Site position repaired at " +
    label +
    " " +
    fix.repaired_timepoints.join(", ") +
    ": this field holds grid position x = " +
    String(fix.grid_x) +
    " for the whole run."
  );
}

export function viewerSourceIdentity(source: ViewerSourceState): ViewerSourceIdentity {
  const wellId = wellIdFromPath(source.path);
  const well = findWellMetadata(source.metadata, wellId);
  const labels = metadataLabels(source.metadata);
  return {
    compoundLabel: labels.compound,
    compound:
      well?.compound?.trim() ||
      (well?.is_control ? labels.control : "Not specified"),
    concentration:
      source.metadata?.labels && well?.dose_um == null
        ? null
        : doseLabel(well?.dose_um),
    well: wellId ?? "Not specified",
    site: siteIdFromPath(source.path) ?? "Not specified",
    plate: source.metadata?.plate?.plate_id?.trim() || "Not specified",
  };
}
