import { expect, test } from "@playwright/test";

import {
  QC_QUEUE_STORAGE_KEY,
  loadQcQueue,
  qcQueueEntryMatches,
  qcQueueIndexFromSearch,
  safeQcReturnHref,
  saveQcQueue,
  withQcQueueIndex,
  type QcQueueEntry,
  type QcQueueInput,
  type QcQueueStorage,
} from "../../src/lib/qcQueue";
import {
  QC_ANNOTATION_CONFIDENCES,
  annotationMatchesQueueEntry,
  countAnnotatedQueueEntries,
  parseQcAnnotation,
  parseQcAnnotationLabels,
  parseQcAnnotations,
  qcAnnotationFilterMatches,
  qcAnnotationRowState,
  type QcCurrentAnnotation,
} from "../../src/lib/qcAnnotations";

class MemoryStorage implements QcQueueStorage {
  values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

const channels = ["DNA", "ER", "AGP", "RNA", "Mito"];
const labelDefinitions = [
  "ok",
  "blurred",
  "partial_blur",
  "saturated",
  "debris",
  "bright_biological",
  "dark_or_bubble",
  "uneven_illumination",
  "dim_or_staining",
  "noisy",
  "sparse_biological",
  "known_fixed_shadow",
  "other",
  "unsure",
].map((label) => ({ label, description: `Fixture ${label}` }));
const queueId = "queue_test_0001";

const field: QcQueueEntry = {
  scope: "field",
  plateGroup: "idr0080-A549",
  plate: 14610,
  barcode: "SQ00014610",
  well: "A01",
  site: 1,
  channel: "DNA",
  reason: "Fixture focus · DNA",
};
const well: QcQueueEntry = {
  scope: "well",
  plateGroup: "idr0080-ES2",
  plate: 14613,
  barcode: "SQ00014613",
  well: "B02",
  site: 1,
  channel: "all",
  flaggedSites: [1, 4, 9],
};

function queueInput(entries: QcQueueEntry[] = [field, well]): QcQueueInput {
  return {
    source: "session",
    exportId: "fixture-export-v1",
    registryHash: "0123456789abcdef",
    collectionId: "idr0080",
    returnHref: "/qc?collection=idr0080&qc.table=sites",
    entries,
  };
}

function expectedQueue(id = queueId) {
  return {
    id,
    exportId: "fixture-export-v1",
    registryHash: "0123456789abcdef",
    collectionId: "idr0080",
    channels,
  };
}

function annotationRecord(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    record: "annotation",
    id: "annotation_0001",
    time: "2026-10-29T12:34:56Z",
    reviewer: "fixture_reviewer",
    export_id: "fixture-export-v1",
    registry_hash: "0123456789abcdef",
    scope: "field",
    plate: 14610,
    barcode: "SQ00014610",
    well: "A01",
    site: 1,
    channel: "DNA",
    label: "blurred",
    confidence: "high",
    note: "Fixture note.",
    blinded: false,
    app_version: "0.5.0.dev12",
    ...overrides,
  };
}

test("stores one compact validated queue and preserves file order and duplicates", () => {
  const storage = new MemoryStorage();
  const entries = [field, well, { ...field }, { ...field, site: 2, channel: "ER" }];
  const written = saveQcQueue(storage, queueInput(entries), channels, () => queueId);

  expect(written).toEqual({
    ok: true,
    queue: {
      schemaVersion: 1,
      id: queueId,
      ...queueInput(entries),
    },
  });
  expect([...storage.values.keys()]).toEqual([QC_QUEUE_STORAGE_KEY]);

  const loaded = loadQcQueue(storage, expectedQueue());
  expect(loaded).toEqual(written);
  if (loaded.ok) {
    expect(loaded.queue.entries).toEqual(entries);
    expect(loaded.queue.entries[0]).toEqual(loaded.queue.entries[2]);
  }
});

test("keeps a full 31,104-field session queue within a conservative browser quota", () => {
  const storage = new MemoryStorage();
  const entries = Array.from({ length: 31_104 }, () => ({ ...field }));
  const written = saveQcQueue(storage, queueInput(entries), channels, () => queueId);

  expect(written.ok).toBe(true);
  expect(storage.getItem(QC_QUEUE_STORAGE_KEY)?.length).toBeLessThan(2_000_000);
  const loaded = loadQcQueue(storage, expectedQueue());
  expect(loaded.ok && loaded.queue.entries).toHaveLength(31_104);
});

test("rejects unsafe targets and return paths without writing", () => {
  const invalidEntries: QcQueueEntry[] = [
    { ...field, plateGroup: "../outside" },
    { ...field, well: "a01" },
    { ...field, plate: 0 },
    { ...field, barcode: "../outside" },
    { ...field, site: Number.NaN },
    { ...field, channel: "unknown" },
    { ...well, site: 2 },
    { ...field, flaggedSites: [1] },
    { ...well, flaggedSites: [0] },
    { ...well, flaggedSites: [1, 1] },
  ];
  for (const entry of invalidEntries) {
    const storage = new MemoryStorage();
    expect(saveQcQueue(storage, queueInput([entry]), channels, () => queueId)).toEqual({
      ok: false,
      reason: "invalid",
    });
    expect(storage.getItem(QC_QUEUE_STORAGE_KEY)).toBeNull();
  }

  for (const returnHref of ["//example.test/qc", "/qc#fragment", "/qc\\next", "/data"] ) {
    const storage = new MemoryStorage();
    expect(
      saveQcQueue(storage, { ...queueInput(), returnHref }, channels, () => queueId),
    ).toEqual({ ok: false, reason: "invalid" });
  }
  expect(safeQcReturnHref("/qc?collection=idr0080")).toBe(true);
  expect(safeQcReturnHref("https://example.test/qc")).toBe(false);
});

test("contains storage failures and rejects malformed or stale stored queues", () => {
  const writeFailure: QcQueueStorage = {
    getItem: () => null,
    setItem: () => {
      throw new Error("quota");
    },
  };
  expect(saveQcQueue(writeFailure, queueInput(), channels, () => queueId)).toEqual({
    ok: false,
    reason: "storage",
  });

  const readFailure: QcQueueStorage = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => undefined,
  };
  expect(loadQcQueue(readFailure, expectedQueue())).toEqual({
    ok: false,
    reason: "storage",
  });

  const storage = new MemoryStorage();
  expect(saveQcQueue(storage, queueInput(), channels, () => queueId).ok).toBe(true);
  for (const expectation of [
    { ...expectedQueue(), id: "queue_other_0001" },
    { ...expectedQueue(), exportId: "fixture-export-v2" },
    { ...expectedQueue(), registryHash: "fedcba9876543210" },
    { ...expectedQueue(), collectionId: "other" },
  ]) {
    expect(loadQcQueue(storage, expectation)).toEqual({ ok: false, reason: "stale" });
  }
  expect(loadQcQueue(storage, { ...expectedQueue(), channels: ["ER"] })).toEqual({
    ok: false,
    reason: "invalid",
  });

  storage.setItem(QC_QUEUE_STORAGE_KEY, "not json");
  expect(loadQcQueue(storage, expectedQueue())).toEqual({
    ok: false,
    reason: "invalid",
  });
});

test("round-trips a bounded queue index without disturbing viewer state", () => {
  const storage = new MemoryStorage();
  const written = saveQcQueue(storage, queueInput(), channels, () => queueId);
  expect(written.ok).toBe(true);
  if (!written.ok) return;

  const query = withQcQueueIndex(
    "?dataset=linked&path=A01_s1.zarr&qcIndex=99",
    written.queue,
    1,
  );
  expect(query?.get("dataset")).toBe("linked");
  expect(query?.get("path")).toBe("A01_s1.zarr");
  expect(query?.get("qcQueue")).toBe(queueId);
  expect(query?.get("qcIndex")).toBe("1");
  expect(qcQueueIndexFromSearch(query ?? "", written.queue)).toBe(1);
  expect(qcQueueIndexFromSearch(`?qcQueue=${queueId}&qcIndex=01`, written.queue)).toBeNull();
  expect(qcQueueIndexFromSearch(`?qcQueue=${queueId}&qcIndex=2`, written.queue)).toBeNull();
  expect(qcQueueIndexFromSearch("?qcQueue=queue_other_0001&qcIndex=0", written.queue)).toBeNull();
  expect(withQcQueueIndex("", written.queue, -1)).toBeNull();
});

test("matches the exact field but permits site stepping within a queued well", () => {
  expect(
    qcQueueEntryMatches(field, {
      scope: "field",
      plate: 14610,
      barcode: "SQ00014610",
      well: "A01",
      site: 1,
    }),
  ).toBe(true);
  expect(
    qcQueueEntryMatches(field, {
      scope: "field",
      plate: 14610,
      barcode: "SQ00014610",
      well: "A01",
      site: 2,
    }),
  ).toBe(false);
  expect(
    qcQueueEntryMatches(well, {
      scope: "well",
      plate: 14613,
      barcode: "SQ00014613",
      well: "B02",
      site: 9,
    }),
  ).toBe(true);
  expect(
    qcQueueEntryMatches(field, {
      scope: "field",
      plate: 14610,
      barcode: "SQ00099999",
      well: "A01",
      site: 1,
    }),
  ).toBe(false);
});

test("parses current annotation records from supplied channel names", () => {
  const parsed = parseQcAnnotation(annotationRecord(), channels);
  expect(parsed).toEqual({
    schemaVersion: 1,
    record: "annotation",
    id: "annotation_0001",
    time: "2026-10-29T12:34:56Z",
    reviewer: "fixture_reviewer",
    exportId: "fixture-export-v1",
    registryHash: "0123456789abcdef",
    scope: "field",
    plate: 14610,
    barcode: "SQ00014610",
    well: "A01",
    site: 1,
    channel: "DNA",
    label: "blurred",
    confidence: "high",
    note: "Fixture note.",
    blinded: false,
    appVersion: "0.5.0.dev12",
  });
  expect(parseQcAnnotation(annotationRecord({ channel: "new-stain" }), ["new-stain"])).not.toBeNull();
  expect(parseQcAnnotation(annotationRecord({ channel: "new-stain" }), channels)).toBeNull();

  const records = parseQcAnnotations(
    [annotationRecord(), annotationRecord({ id: "bad", confidence: "certain" })],
    channels,
  );
  expect(records.annotations).toHaveLength(1);
  expect(records.invalidRecords).toBe(1);
  const labels = parseQcAnnotationLabels(labelDefinitions);
  expect(labels.invalidRecords).toBe(0);
  expect(labels.labels).toEqual(labelDefinitions);
  expect(labels.labels.map((definition) => definition.label)).toContain(
    "bright_biological",
  );
  expect(labels.labels.map((definition) => definition.label)).not.toContain(
    "misregistered",
  );
  expect(QC_ANNOTATION_CONFIDENCES).toEqual(["low", "medium", "high"]);
});

test("derives field and well annotation status without crossing row scopes", () => {
  const raw = [
    annotationRecord({ id: "field_noisy", label: "noisy" }),
    annotationRecord({ id: "field_ok", label: "ok", channel: "ER" }),
    annotationRecord({
      id: "well_debris",
      scope: "well",
      site: null,
      channel: "all",
      label: "debris",
    }),
    annotationRecord({ id: "other_site", site: 2, label: "saturated" }),
  ];
  const parsed = parseQcAnnotations(raw, channels).annotations;
  const fieldState = qcAnnotationRowState(parsed, {
    scope: "field",
    plate: 14610,
    barcode: "SQ00014610",
    well: "A01",
    site: 1,
  }, labelDefinitions);
  expect(fieldState).toEqual({
    reviewed: true,
    status: "reviewed",
    labels: ["ok", "noisy"],
    count: 2,
  });
  expect(qcAnnotationFilterMatches(fieldState, "reviewed")).toBe(true);
  expect(qcAnnotationFilterMatches(fieldState, "noisy")).toBe(true);
  expect(qcAnnotationFilterMatches(fieldState, "debris")).toBe(false);

  const wellState = qcAnnotationRowState(parsed, {
    scope: "well",
    plate: 14610,
    barcode: "SQ00014610",
    well: "A01",
    site: null,
  }, labelDefinitions);
  expect(wellState.labels).toEqual(["debris"]);
  expect(wellState.count).toBe(1);
  const empty = qcAnnotationRowState(parsed, {
    scope: "field",
    plate: 14610,
    barcode: "SQ00014610",
    well: "B01",
    site: 1,
  }, labelDefinitions);
  expect(empty.status).toBe("not reviewed");
  expect(qcAnnotationFilterMatches(empty, "not reviewed")).toBe(true);
  const reusedPlate = qcAnnotationRowState(parsed, {
    scope: "field",
    plate: 14610,
    barcode: "SQ00099999",
    well: "A01",
    site: 1,
  }, labelDefinitions);
  expect(reusedPlate.status).toBe("not reviewed");
});

test("counts duplicate queue entries and applies all-channel annotations deliberately", () => {
  const parsed = parseQcAnnotations(
    [
      annotationRecord({ id: "field_dna", channel: "DNA" }),
      annotationRecord({
        id: "well_all",
        scope: "well",
        plate: 14613,
        barcode: "SQ00014613",
        well: "B02",
        site: null,
        channel: "all",
      }),
    ],
    channels,
  ).annotations;
  const entries: QcQueueEntry[] = [
    field,
    { ...field },
    { ...field, channel: "ER" },
    { ...field, channel: "all" },
    { ...well, channel: "AGP" },
    well,
  ];

  expect(countAnnotatedQueueEntries(entries, parsed)).toBe(4);
  expect(annotationMatchesQueueEntry(parsed[0] as QcCurrentAnnotation, field)).toBe(true);
  expect(
    annotationMatchesQueueEntry(
      parsed[0] as QcCurrentAnnotation,
      { ...field, channel: "all" },
    ),
  ).toBe(false);
  expect(
    annotationMatchesQueueEntry(
      parsed[1] as QcCurrentAnnotation,
      { ...well, channel: "AGP" },
    ),
  ).toBe(true);
});
