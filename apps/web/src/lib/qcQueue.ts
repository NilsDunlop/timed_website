export const QC_QUEUE_STORAGE_KEY = "timed.qc.review-queue.v1";
export const QC_QUEUE_ID_PARAM = "qcQueue";
export const QC_QUEUE_INDEX_PARAM = "qcIndex";

export type QcQueueSource = "review-list" | "session";
export type QcQueueScope = "field" | "well";

export type QcQueueEntry = {
  scope: QcQueueScope;
  plateGroup: string;
  plate: number;
  barcode: string;
  well: string;
  site: number;
  channel: string;
  reason?: string;
  flaggedSites?: readonly number[];
};

export type QcReviewQueue = {
  schemaVersion: 1;
  id: string;
  source: QcQueueSource;
  exportId: string;
  registryHash: string;
  collectionId: string;
  returnHref: string;
  entries: QcQueueEntry[];
};

export type QcQueueInput = Omit<QcReviewQueue, "schemaVersion" | "id">;

export type QcQueueStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

export type QcQueueWriteResult =
  | { ok: true; queue: QcReviewQueue }
  | { ok: false; reason: "invalid" | "storage" };

export type QcQueueLoadResult =
  | { ok: true; queue: QcReviewQueue }
  | { ok: false; reason: "missing" | "invalid" | "stale" | "storage" };

export type QcQueueExpectation = {
  id: string;
  exportId: string;
  registryHash: string;
  collectionId: string;
  channels: Iterable<string>;
};

export type QcQueueTarget = {
  scope: QcQueueScope;
  plate: number;
  barcode: string;
  well: string;
  site: number;
};

type StoredEntry = [
  scope: "f" | "w",
  plateGroup: string,
  plate: number,
  barcode: string,
  well: string,
  site: number,
  channel: string,
  reasonIndex?: number | null,
  flaggedSites?: string,
];

type StoredQueue = [
  schemaVersion: 1,
  id: string,
  source: "r" | "s",
  exportId: string,
  registryHash: string,
  collectionId: string,
  returnHref: string,
  entries: StoredEntry[],
  reasons: string[],
];

const SAFE_TOKEN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const SAFE_QUEUE_ID = /^[A-Za-z0-9_-]{8,128}$/;
const SAFE_PLATE_GROUP = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SAFE_WELL = /^[A-Z]+\d+$/;
const MAX_QUEUE_ENTRIES = 100_000;

function safeToken(value: unknown): value is string {
  return typeof value === "string" && SAFE_TOKEN.test(value);
}

function safeQueueId(value: unknown): value is string {
  return typeof value === "string" && SAFE_QUEUE_ID.test(value);
}

function safePlateGroup(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value !== "." &&
    value !== ".." &&
    SAFE_PLATE_GROUP.test(value)
  );
}

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

function safeReason(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 500 &&
    !/\p{Cc}/u.test(value)
  );
}

function safeFlaggedSites(value: unknown): value is readonly number[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.length <= 100 &&
    value.every(positiveInteger) &&
    new Set(value).size === value.length
  );
}

// Guards the return href stored with a review queue. The viewer's back link has
// its own, looser check (normalizedQcReturnHref in viewerLink.ts).
export function safeQcReturnHref(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 8192 || value.includes("\\")) {
    return false;
  }
  if (value !== "/qc" && !value.startsWith("/qc?")) return false;
  if (/\p{Cc}/u.test(value)) return false;
  try {
    const parsed = new URL(value, "https://timed.invalid");
    return (
      parsed.origin === "https://timed.invalid" &&
      parsed.pathname === "/qc" &&
      parsed.hash === ""
    );
  } catch {
    return false;
  }
}

function channelSet(channels: Iterable<string>): Set<string> {
  return new Set(
    [...channels].filter(
      (channel) => typeof channel === "string" && channel.length > 0,
    ),
  );
}

function validChannel(value: unknown, channels: Set<string>): value is string {
  return typeof value === "string" && (value === "all" || channels.has(value));
}

function validEntry(
  entry: QcQueueEntry,
  channels: Set<string>,
): boolean {
  return (
    (entry.scope === "field" || entry.scope === "well") &&
    safePlateGroup(entry.plateGroup) &&
    positiveInteger(entry.plate) &&
    safeToken(entry.barcode) &&
    SAFE_WELL.test(entry.well) &&
    positiveInteger(entry.site) &&
    (entry.scope !== "well" || entry.site === 1) &&
    validChannel(entry.channel, channels) &&
    (entry.reason === undefined || safeReason(entry.reason)) &&
    (entry.flaggedSites === undefined ||
      (entry.scope === "well" && safeFlaggedSites(entry.flaggedSites)))
  );
}

function validInput(
  input: QcQueueInput,
  channels: Set<string>,
): boolean {
  return (
    (input.source === "review-list" || input.source === "session") &&
    safeToken(input.exportId) &&
    safeToken(input.registryHash) &&
    safeToken(input.collectionId) &&
    safeQcReturnHref(input.returnHref) &&
    Array.isArray(input.entries) &&
    input.entries.length > 0 &&
    input.entries.length <= MAX_QUEUE_ENTRIES &&
    input.entries.every((entry) => validEntry(entry, channels))
  );
}

function storedEntry(
  entry: QcQueueEntry,
  reasonIndexes: ReadonlyMap<string, number>,
): StoredEntry {
  const stored: StoredEntry = [
    entry.scope === "field" ? "f" : "w",
    entry.plateGroup,
    entry.plate,
    entry.barcode,
    entry.well,
    entry.site,
    entry.channel,
  ];
  const reasonIndex = entry.reason ? reasonIndexes.get(entry.reason) : undefined;
  if (entry.flaggedSites?.length) {
    stored.push(reasonIndex ?? null, entry.flaggedSites.join(","));
  } else if (reasonIndex !== undefined) {
    stored.push(reasonIndex);
  }
  return stored;
}

function storedQueue(queue: QcReviewQueue): StoredQueue {
  const reasons = [...new Set(queue.entries.flatMap((entry) => entry.reason ?? []))];
  const reasonIndexes = new Map(reasons.map((reason, index) => [reason, index]));
  return [
    1,
    queue.id,
    queue.source === "review-list" ? "r" : "s",
    queue.exportId,
    queue.registryHash,
    queue.collectionId,
    queue.returnHref,
    queue.entries.map((entry) => storedEntry(entry, reasonIndexes)),
    reasons,
  ];
}

function queueFromStored(value: unknown, channels: Set<string>): QcReviewQueue | null {
  if (!Array.isArray(value) || (value.length !== 8 && value.length !== 9)) return null;
  const [version, id, source, exportId, registryHash, collectionId, returnHref, rows, rawReasons] =
    value;
  const reasons = rawReasons === undefined ? [] : rawReasons;
  if (
    version !== 1 ||
    !safeQueueId(id) ||
    (source !== "r" && source !== "s") ||
    !safeToken(exportId) ||
    !safeToken(registryHash) ||
    !safeToken(collectionId) ||
    !safeQcReturnHref(returnHref) ||
    !Array.isArray(rows) ||
    !Array.isArray(reasons) ||
    reasons.some((reason) => !safeReason(reason)) ||
    rows.length === 0 ||
    rows.length > MAX_QUEUE_ENTRIES
  ) {
    return null;
  }

  const entries: QcQueueEntry[] = [];
  for (const row of rows) {
    if (!Array.isArray(row) || row.length < 7 || row.length > 9) return null;
    const [scope, plateGroup, plate, barcode, well, site, channel, reasonIndex, rawFlaggedSites] = row;
    if (
      reasonIndex !== undefined &&
      reasonIndex !== null &&
      (!Number.isSafeInteger(reasonIndex) || !safeReason(reasons[Number(reasonIndex)]))
    ) {
      return null;
    }
    const reason =
      reasonIndex === undefined || reasonIndex === null
        ? undefined
        : reasons[Number(reasonIndex)];
    const flaggedSites =
      typeof rawFlaggedSites === "string" && /^\d+(?:,\d+)*$/.test(rawFlaggedSites)
        ? rawFlaggedSites.split(",").map(Number)
        : undefined;
    if (rawFlaggedSites !== undefined && !safeFlaggedSites(flaggedSites)) return null;
    const entry: QcQueueEntry = {
      scope: scope === "f" ? "field" : scope === "w" ? "well" : ("" as QcQueueScope),
      plateGroup: typeof plateGroup === "string" ? plateGroup : "",
      plate: typeof plate === "number" ? plate : Number.NaN,
      barcode: typeof barcode === "string" ? barcode : "",
      well: typeof well === "string" ? well : "",
      site: typeof site === "number" ? site : Number.NaN,
      channel: typeof channel === "string" ? channel : "",
      ...(safeReason(reason) ? { reason } : {}),
      ...(safeFlaggedSites(flaggedSites) ? { flaggedSites } : {}),
    };
    if (!validEntry(entry, channels)) return null;
    entries.push(entry);
  }

  return {
    schemaVersion: 1,
    id,
    source: source === "r" ? "review-list" : "session",
    exportId,
    registryHash,
    collectionId,
    returnHref,
    entries,
  };
}

function randomQueueId(): string {
  return globalThis.crypto.randomUUID().replaceAll("-", "");
}

export function saveQcQueue(
  storage: QcQueueStorage,
  input: QcQueueInput,
  channels: Iterable<string>,
  idFactory: () => string = randomQueueId,
): QcQueueWriteResult {
  const availableChannels = channelSet(channels);
  if (!validInput(input, availableChannels)) return { ok: false, reason: "invalid" };

  let id: string;
  try {
    id = idFactory();
  } catch {
    return { ok: false, reason: "storage" };
  }
  if (!safeQueueId(id)) return { ok: false, reason: "invalid" };

  const queue: QcReviewQueue = {
    schemaVersion: 1,
    id,
    source: input.source,
    exportId: input.exportId,
    registryHash: input.registryHash,
    collectionId: input.collectionId,
    returnHref: input.returnHref,
    entries: input.entries.map((entry) => ({
      ...entry,
      ...(entry.flaggedSites ? { flaggedSites: [...entry.flaggedSites] } : {}),
    })),
  };
  try {
    storage.setItem(QC_QUEUE_STORAGE_KEY, JSON.stringify(storedQueue(queue)));
  } catch {
    return { ok: false, reason: "storage" };
  }
  return { ok: true, queue };
}

export function loadQcQueue(
  storage: QcQueueStorage,
  expectation: QcQueueExpectation,
): QcQueueLoadResult {
  let raw: string | null;
  try {
    raw = storage.getItem(QC_QUEUE_STORAGE_KEY);
  } catch {
    return { ok: false, reason: "storage" };
  }
  if (raw === null) return { ok: false, reason: "missing" };

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, reason: "invalid" };
  }
  const queue = queueFromStored(parsed, channelSet(expectation.channels));
  if (!queue) return { ok: false, reason: "invalid" };
  if (
    queue.id !== expectation.id ||
    queue.exportId !== expectation.exportId ||
    queue.registryHash !== expectation.registryHash ||
    queue.collectionId !== expectation.collectionId
  ) {
    return { ok: false, reason: "stale" };
  }
  return { ok: true, queue };
}

function searchParams(search: string | URLSearchParams): URLSearchParams {
  return typeof search === "string"
    ? new URLSearchParams(search.startsWith("?") ? search.slice(1) : search)
    : new URLSearchParams(search);
}

export function qcQueueIndexFromSearch(
  search: string | URLSearchParams,
  queue: Pick<QcReviewQueue, "id" | "entries">,
): number | null {
  const query = searchParams(search);
  if (query.get(QC_QUEUE_ID_PARAM) !== queue.id) return null;
  const rawIndex = query.get(QC_QUEUE_INDEX_PARAM);
  if (!rawIndex || !/^(0|[1-9]\d*)$/.test(rawIndex)) return null;
  const index = Number(rawIndex);
  return Number.isSafeInteger(index) && index < queue.entries.length ? index : null;
}

export function withQcQueueIndex(
  search: string | URLSearchParams,
  queue: Pick<QcReviewQueue, "id" | "entries">,
  index: number,
): URLSearchParams | null {
  if (!Number.isSafeInteger(index) || index < 0 || index >= queue.entries.length) {
    return null;
  }
  const query = searchParams(search);
  query.set(QC_QUEUE_ID_PARAM, queue.id);
  query.set(QC_QUEUE_INDEX_PARAM, String(index));
  return query;
}

export function qcQueueEntryMatches(
  entry: QcQueueEntry,
  target: QcQueueTarget,
): boolean {
  return (
    entry.scope === target.scope &&
    entry.plate === target.plate &&
    entry.barcode === target.barcode &&
    entry.well === target.well &&
    (entry.scope === "well" || entry.site === target.site)
  );
}
