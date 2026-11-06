import { siteIdFromPath, wellIdFromPath } from "../fieldPath";

export type QcViewerRequest = {
  collectionId: string;
  expectedExport: string;
  plate: number;
  well: string;
  site: number;
  scope: "field" | "well";
  flaggedSites: readonly number[];
};

export function qcRequestFromQuery(
  query: URLSearchParams,
  selectedPath: string,
  sourceCount: number,
): QcViewerRequest | null {
  if (query.get("qc") !== "1") return null;
  const collectionId = query.get("collection")?.trim() ?? "";
  const expectedExport = query.get("qcExport")?.trim() ?? "";
  const plate = Number(query.get("qcPlate"));
  const well = query.get("qcWell")?.trim().toUpperCase() ?? "";
  const site = Number(query.get("qcSite"));
  const rawFlaggedSites = query.get("qcFlaggedSites")?.trim() ?? "";
  const flaggedSites = rawFlaggedSites
    ? rawFlaggedSites.split(",").map(Number)
    : [];
  if (
    !collectionId ||
    !expectedExport ||
    !Number.isInteger(plate) ||
    plate <= 0 ||
    !/^[A-Z]+\d+$/.test(well) ||
    !Number.isInteger(site) ||
    site <= 0 ||
    sourceCount !== 1 ||
    wellIdFromPath(selectedPath) !== well ||
    Number(siteIdFromPath(selectedPath)) !== site ||
    flaggedSites.some((candidate) => !Number.isInteger(candidate) || candidate <= 0) ||
    new Set(flaggedSites).size !== flaggedSites.length
  ) {
    throw new Error("The QC viewer link is invalid");
  }
  return {
    collectionId,
    expectedExport,
    plate,
    well,
    site,
    scope: query.get("qcScope") === "well" ? "well" : "field",
    flaggedSites,
  };
}
