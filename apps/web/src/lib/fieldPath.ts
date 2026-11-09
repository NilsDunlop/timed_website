// Field paths inside a linked dataset.
//
// TIMED and packed collections name each field `<well>_s<site>.zarr`, for example
// `B02_s1.zarr`; native OME-Zarr plates use `<row>/<column>/<field>`, for example
// `B/2/0`. `fieldPathFor` writes the collection form and `parseFieldPath` reads it
// back. The older readers below keep their exact rules, which differ in letter
// case, padding and anchoring; tests/e2e/field-path.spec.ts pins each difference.

/** The well and site of a collection field path. */
export type CollectionFieldPath = {
  /** Upper-case row letters and a column padded to two digits, such as `B02`. */
  well: string;
  /** The site number as written. */
  site: number;
};

const COLLECTION_FIELD_PATH = /^([A-Za-z]+)(\d+)_s(\d+)\.zarr$/;

export function fieldPathFor(well: string, site: number): string {
  return `${well}_s${site}.zarr`;
}

export function parseFieldPath(path: string): CollectionFieldPath | null {
  const match = COLLECTION_FIELD_PATH.exec(path);
  if (!match) return null;
  return {
    well: match[1].toUpperCase() + match[2].padStart(2, "0"),
    site: Number.parseInt(match[3], 10),
  };
}

/** Collection or native well, padded: the form plate metadata uses. */
export function wellIdFromPath(path: string): string | undefined {
  const collection = parseFieldPath(path);
  if (collection) return collection.well;
  const native = /^([A-Za-z]+)\/(\d+)(?:\/|$)/.exec(path);
  if (native) {
    return native[1].toUpperCase() + native[2].padStart(2, "0");
  }
  return undefined;
}

/** Collection site in any letter case, or a native field's last segment. */
export function siteIdFromPath(path: string): string | undefined {
  const collection = /_s(\d+)\.zarr$/i.exec(path);
  if (collection?.[1]) {
    return String(Number.parseInt(collection[1], 10));
  }
  return /^[A-Za-z]+\/\d+\/([^/]+)(?:\/|$)/.exec(path)?.[1];
}

/** Positive collection site in any letter case, as QC site options read it. */
export function positiveSiteFromPath(path: string): number | null {
  const match = /_s(\d+)\.zarr$/i.exec(path);
  if (!match?.[1]) return null;
  const site = Number.parseInt(match[1], 10);
  return Number.isInteger(site) && site > 0 ? site : null;
}
