import { expect, test } from "@playwright/test";

import {
  type CacheableTileSource,
  DecodedTileCache,
  prefetchTimepoint,
} from "../../src/lib/viewerTileCache";

function tile(bytes = 8) {
  return {
    data: new Uint8Array(bytes),
    width: 2,
    height: 2,
  };
}

test("shares an in-flight tile without letting one consumer cancel another", async () => {
  const cache = new DecodedTileCache(1024);
  const firstConsumer = new AbortController();
  const secondConsumer = new AbortController();
  let loads = 0;
  let underlyingAborted = false;
  let resolveLoad: ((value: ReturnType<typeof tile>) => void) | undefined;

  const load = (signal: AbortSignal) => {
    loads += 1;
    signal.addEventListener("abort", () => {
      underlyingAborted = true;
    });
    return new Promise<ReturnType<typeof tile>>((resolve) => {
      resolveLoad = resolve;
    });
  };

  const first = cache.getOrLoad("shared", load, firstConsumer.signal);
  const firstResult = first.then(
    () => "resolved",
    (reason: unknown) =>
      reason instanceof DOMException ? reason.name : String(reason),
  );
  const second = cache.getOrLoad("shared", load, secondConsumer.signal);
  await Promise.resolve();

  firstConsumer.abort();
  expect(await firstResult).toBe("AbortError");
  expect(underlyingAborted).toBe(false);

  resolveLoad?.(tile());
  await expect(second).resolves.toEqual(tile());
  expect(loads).toBe(1);

  await expect(
    cache.getOrLoad("shared", () => {
      throw new Error("completed tiles must come from the cache");
    }),
  ).resolves.toEqual(tile());
});

test("evicts least-recently-used decoded tiles within its byte budget", async () => {
  const cache = new DecodedTileCache(16);
  let loads = 0;
  const load = async () => {
    loads += 1;
    return tile(8);
  };

  await cache.getOrLoad("first", load);
  await cache.getOrLoad("second", load);
  await cache.getOrLoad("first", load);
  await cache.getOrLoad("third", load);
  expect(loads).toBe(3);
  expect(cache.byteLength).toBe(16);
  expect(cache.size).toBe(2);

  await cache.getOrLoad("second", load);
  expect(loads).toBe(4);
});

test("prefetches every channel tile with bounded concurrency", async () => {
  let active = 0;
  let maximumActive = 0;
  let loads = 0;
  const source: CacheableTileSource = {
    shape: [3, 4, 4],
    labels: ["c", "y", "x"],
    tileSize: 2,
    async getTile() {
      loads += 1;
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await new Promise((resolve) => setTimeout(resolve, 1));
      active -= 1;
      return tile();
    },
  };

  await prefetchTimepoint(
    [source],
    [{ c: 0 }, { c: 1 }, { c: 2 }],
    new AbortController().signal,
    2,
  );

  expect(loads).toBe(12);
  expect(maximumActive).toBe(2);
});
