import { siteIdFromPath, wellIdFromPath } from "./fieldPath";
import { doseLabel, findWellMetadata, metadataLabels } from "./plateMetadata";
import type { ViewerSourceState } from "./viewerSources";

export type ViewerSourceIdentity = {
  compoundLabel: string;
  compound: string;
  concentration: string | null;
  well: string;
  site: string;
  plate: string;
};

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
