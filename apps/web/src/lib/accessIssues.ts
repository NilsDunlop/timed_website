// What the connected account can't read. The data server checks each
// configured path once at startup and reports it in the handshake; this turns
// that report into plain messages and tells which datasets and features are
// unavailable. Mask and QC folders are never named: profiles keep them private.
import type { components } from "./api/dataserver-schema";

export type AccessIssue = components["schemas"]["AccessIssue"];

type TitledCollection = { path: string; title: string };

const KINDS: ReadonlySet<string> = new Set<AccessIssue["kind"]>([
  "data_root",
  "metadata",
  "collection",
  "masks",
  "segmentation",
  "qc_export",
  "qc_annotations",
]);

function optionalText(value: unknown): boolean {
  return value === undefined || value === null || typeof value === "string";
}

function isAccessIssue(value: unknown): value is AccessIssue {
  if (!value || typeof value !== "object") return false;
  const issue = value as Record<string, unknown>;
  return (
    typeof issue.kind === "string" &&
    KINDS.has(issue.kind) &&
    typeof issue.reason === "string" &&
    optionalText(issue.path) &&
    optionalText(issue.collection)
  );
}

/** The access report in a session's handshake, ignoring anything malformed. */
export function accessIssues(handshake: unknown): AccessIssue[] {
  if (!handshake || typeof handshake !== "object") return [];
  const issues = (handshake as { access_issues?: unknown }).access_issues;
  return Array.isArray(issues) ? issues.filter(isAccessIssue) : [];
}

/** The issue that makes a whole dataset unavailable, if there is one. */
export function collectionIssue(
  issues: readonly AccessIssue[],
  collectionPath: string,
): AccessIssue | null {
  return (
    issues.find(
      (issue) => issue.kind === "collection" && issue.collection === collectionPath,
    ) ?? null
  );
}

/** The issue that keeps a dataset's QC review from loading, if there is one. */
export function qcIssue(
  issues: readonly AccessIssue[],
  collectionPath: string,
): AccessIssue | null {
  return (
    collectionIssue(issues, collectionPath) ??
    issues.find(
      (issue) => issue.kind === "qc_export" && issue.collection === collectionPath,
    ) ??
    null
  );
}

/** One plain sentence per issue, naming a dataset by its title when the profile has it. */
export function accessMessage(
  issue: AccessIssue,
  collections: readonly TitledCollection[],
): string {
  const title = collections.find((collection) => collection.path === issue.collection)?.title;
  const owner = title ? `${title} ` : "";
  switch (issue.kind) {
    case "data_root":
      return `Your account can't read the data folder ${issue.path ?? ""}.`;
    case "metadata":
      return (
        `Your account can't read the plate metadata folder ${issue.path ?? ""}, ` +
        "so well details are unavailable."
      );
    case "collection":
      return title
        ? `Your account can't read ${title} (${issue.path ?? issue.collection ?? ""}).`
        : `Your account can't read ${issue.path ?? issue.collection ?? "a dataset"}.`;
    case "masks":
    case "segmentation":
      return `Your account can't read the ${owner}segmentation, so its outlines are unavailable.`;
    case "qc_export":
      return `Your account can't read the ${owner}QC export, so QC review is unavailable.`;
    case "qc_annotations":
      return `Your account can't write the ${owner}QC annotations, so QC is read-only.`;
  }
}
