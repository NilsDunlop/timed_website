import { expect, test } from "@playwright/test";

import {
  cacheImageLoader,
  type CacheableImageSource,
  DecodedPixelCache,
  prefetchTimepoint,
  prefetchTimepoints,
  VIEWER_PIXEL_CACHE_BYTES,
} from "../../src/lib/viewerTileCache";

function pixels(bytes = 8) {
  return {
    data: new Uint8Array(bytes),
    width: 2,
    height: 2,
  };
}

test("shares an in-flight read without letting one consumer cancel another", async () => {
  const cache = new DecodedPixelCache(1024);
  const firstConsumer = new AbortController();
  const secondConsumer = new AbortController();
  let loads = 0;
  let underlyingAborted = false;
  let resolveLoad: ((value: ReturnType<typeof pixels>) => void) | undefined;

  const load = (signal: AbortSignal) => {
    loads += 1;
    signal.addEventListener("abort", () => {
      underlyingAborted = true;
    });
    return new Promise<ReturnType<typeof pixels>>((resolve) => {
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

  resolveLoad?.(pixels());
  await expect(second).resolves.toEqual(pixels());
  expect(loads).toBe(1);

  await expect(
    cache.getOrLoad("shared", () => {
      throw new Error("completed pixels must come from the cache");
    }),
  ).resolves.toEqual(pixels());
});

test("evicts least-recently-used decoded pixels within its byte budget", async () => {
  const cache = new DecodedPixelCache(16);
  let loads = 0;
  const load = async () => {
    loads += 1;
    return pixels(8);
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

test("prefetches single-resolution channel rasters with bounded concurrency", async () => {
  let active = 0;
  let maximumActive = 0;
  let rasterLoads = 0;
  let tileLoads = 0;
  const source: CacheableImageSource = {
    shape: [3, 4, 4],
    labels: ["c", "y", "x"],
    tileSize: 2,
    async getRaster() {
      rasterLoads += 1;
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await new Promise((resolve) => setTimeout(resolve, 1));
      active -= 1;
      return pixels();
    },
    async getTile() {
      tileLoads += 1;
      return pixels();
    },
  };

  await prefetchTimepoint(
    [source],
    [{ c: 0 }, { c: 1 }, { c: 2 }],
    new AbortController().signal,
    2,
  );

  expect(rasterLoads).toBe(3);
  expect(tileLoads).toBe(0);
  expect(maximumActive).toBe(2);
});

test("shares one concurrency budget across four prefetched images", async () => {
  let active = 0;
  let maximumActive = 0;
  let rasterLoads = 0;
  const sources = Array.from({ length: 4 }, () => {
    const source: CacheableImageSource = {
      shape: [3, 4, 4],
      labels: ["c", "y", "x"],
      tileSize: 2,
      async getRaster() {
        rasterLoads += 1;
        active += 1;
        maximumActive = Math.max(maximumActive, active);
        await new Promise((resolve) => setTimeout(resolve, 1));
        active -= 1;
        return pixels();
      },
      async getTile() {
        return pixels();
      },
    };
    return {
      loader: [source],
      selections: [{ c: 0 }, { c: 1 }, { c: 2 }],
    };
  });

  await prefetchTimepoints(
    sources,
    new AbortController().signal,
    3,
  );

  expect(rasterLoads).toBe(12);
  expect(maximumActive).toBe(3);
  expect(VIEWER_PIXEL_CACHE_BYTES).toBeGreaterThanOrEqual(
    4 * 3 * 2500 * 2500 * Uint16Array.BYTES_PER_ELEMENT,
  );
});

test("serves a completed raster prefetch to the foreground without reloading", async () => {
  const cache = new DecodedPixelCache(1024);
  let rasterLoads = 0;
  let tileLoads = 0;
  const source: CacheableImageSource = {
    shape: [2, 4, 4],
    labels: ["c", "y", "x"],
    tileSize: 2,
    async getRaster() {
      rasterLoads += 1;
      return pixels(16);
    },
    async getTile() {
      tileLoads += 1;
      return pixels();
    },
  };
  cacheImageLoader([source], "image", cache);
  const selection = { c: 1, t: 7 };

  await prefetchTimepoint(
    [source],
    [selection],
    new AbortController().signal,
  );
  await expect(source.getRaster({ selection })).resolves.toEqual(pixels(16));

  expect(rasterLoads).toBe(1);
  expect(tileLoads).toBe(0);
  expect(cache.size).toBe(1);
});

test("hands an in-flight prefetch to the foreground during the abort grace period", async () => {
  const cache = new DecodedPixelCache(1024, 50);
  let rasterLoads = 0;
  let underlyingAborted = false;
  let resolveRaster:
    | ((value: ReturnType<typeof pixels>) => void)
    | undefined;
  const source: CacheableImageSource = {
    shape: [1, 4, 4],
    labels: ["c", "y", "x"],
    tileSize: 2,
    getRaster({ signal }) {
      rasterLoads += 1;
      signal?.addEventListener("abort", () => {
        underlyingAborted = true;
      });
      return new Promise<ReturnType<typeof pixels>>((resolve) => {
        resolveRaster = resolve;
      });
    },
    async getTile() {
      return pixels();
    },
  };
  cacheImageLoader([source], "image", cache);
  const selection = { c: 0, t: 15 };
  const prefetchController = new AbortController();
  const prefetch = prefetchTimepoint(
    [source],
    [selection],
    prefetchController.signal,
  );
  const prefetchResult = prefetch.then(
    () => "resolved",
    (reason: unknown) =>
      reason instanceof DOMException ? reason.name : String(reason),
  );
  await expect.poll(() => rasterLoads).toBe(1);

  prefetchController.abort();
  await new Promise((resolve) => setTimeout(resolve, 20));
  const foreground = source.getRaster({ selection });

  expect(await prefetchResult).toBe("AbortError");
  expect(underlyingAborted).toBe(false);
  resolveRaster?.(pixels(16));
  await expect(foreground).resolves.toEqual(pixels(16));
  expect(rasterLoads).toBe(1);
});

test("aborts an orphaned read after its handoff grace period", async () => {
  const cache = new DecodedPixelCache(1024, 5);
  const consumer = new AbortController();
  let underlyingAborted = false;
  const pending = cache.getOrLoad(
    "orphan",
    (signal) =>
      new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => {
          underlyingAborted = true;
          reject(new DOMException("The operation was aborted", "AbortError"));
        });
      }),
    consumer.signal,
  );
  const outcome = pending.then(
    () => "resolved",
    (reason: unknown) =>
      reason instanceof DOMException ? reason.name : String(reason),
  );
  await Promise.resolve();

  consumer.abort();

  expect(await outcome).toBe("AbortError");
  await expect.poll(() => underlyingAborted).toBe(true);
});
