// Temporary refactor snapshot: today's exact stored QC queue string, recorded
// from unchanged code on 2026-11-05. The last commit of the Gate A refactor
// deletes this folder.
import { expect, test } from "@playwright/test";

import {
  QC_QUEUE_STORAGE_KEY,
  saveQcQueue,
  withQcQueueIndex,
  type QcQueueEntry,
  type QcQueueStorage,
} from "../../../src/lib/qcQueue";

class MemoryStorage implements QcQueueStorage {
  values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

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

test("the stored queue string is unchanged", () => {
  const storage = new MemoryStorage();
  const written = saveQcQueue(
    storage,
    {
      source: "session",
      exportId: "fixture-export-v1",
      registryHash: "0123456789abcdef",
      collectionId: "idr0080",
      returnHref: "/qc?collection=idr0080&qc.table=wells",
      entries: [field, well, { ...field, site: 2, channel: "ER", reason: undefined }],
    },
    ["DNA", "ER", "AGP", "RNA", "Mito"],
    () => "queue_snapshot_0001",
  );

  expect(written.ok).toBe(true);
  expect([...storage.values.keys()]).toEqual([QC_QUEUE_STORAGE_KEY]);
  expect(storage.getItem(QC_QUEUE_STORAGE_KEY)).toBe(
    '[1,"queue_snapshot_0001","s","fixture-export-v1","0123456789abcdef","idr0080",' +
      '"/qc?collection=idr0080&qc.table=wells",' +
      '[["f","idr0080-A549",14610,"SQ00014610","A01",1,"DNA",0],' +
      '["w","idr0080-ES2",14613,"SQ00014613","B02",1,"all",null,"1,4,9"],' +
      '["f","idr0080-A549",14610,"SQ00014610","A01",2,"ER"]],' +
      '["Fixture focus · DNA"]]',
  );
});

test("the queue index search is unchanged", () => {
  expect(
    withQcQueueIndex(
      "?dataset=abc&path=A01_s1.zarr&qc=1&qcIndex=0&debug=1",
      { id: "queue_snapshot_0001", entries: [field, well, field] },
      2,
    )?.toString(),
  ).toBe("dataset=abc&path=A01_s1.zarr&qc=1&qcIndex=2&debug=1&qcQueue=queue_snapshot_0001");
});
