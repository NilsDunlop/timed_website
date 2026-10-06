import { viewerDebug } from "./viewerDebug";

export type DecodedPixels = {
  data: ArrayBufferView;
  width: number;
  height: number;
};

export type PixelSelection = Record<string, number>;

export type RasterRequest = {
  selection: PixelSelection;
  signal?: AbortSignal;
};

export type TileRequest = RasterRequest & {
  x: number;
  y: number;
};

export type CacheableImageSource = {
  shape: number[];
  labels: string[];
  tileSize: number;
  getRaster(request: RasterRequest): Promise<DecodedPixels>;
  getTile(request: TileRequest): Promise<DecodedPixels>;
};

type CacheEntry = {
  value: DecodedPixels;
  bytes: number;
};

type PendingEntry = {
  controller: AbortController;
  consumers: Set<symbol>;
  promise: Promise<DecodedPixels>;
  settled: boolean;
  orphanTimer: ReturnType<typeof setTimeout> | null;
};

const CACHED_SOURCE = Symbol("timed-cached-image-source");

export const PENDING_LOAD_GRACE_MS = 300;

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted", "AbortError");
}

/**
 * A byte-bounded LRU for decoded image data shared by foreground rendering and
 * speculative prefetching. Pending reads are also shared. When their final
 * consumer leaves, a short grace period lets the foreground renderer adopt a
 * just-prefetched read after the time-slider debounce.
 */
export class DecodedPixelCache {
  private entries = new Map<string, CacheEntry>();
  private pending = new Map<string, PendingEntry>();
  private usedBytes = 0;

  constructor(
    readonly maximumBytes: number,
    readonly orphanGraceMs = PENDING_LOAD_GRACE_MS,
  ) {
    if (!Number.isFinite(maximumBytes) || maximumBytes <= 0) {
      throw new Error("Decoded pixel cache size must be a positive number");
    }
    if (!Number.isFinite(orphanGraceMs) || orphanGraceMs < 0) {
      throw new Error("Decoded pixel cache grace period must not be negative");
    }
  }

  get byteLength(): number {
    return this.usedBytes;
  }

  get size(): number {
    return this.entries.size;
  }

  clear(): void {
    viewerDebug("cache:clear", {
      completedEntries: this.entries.size,
      pendingEntries: this.pending.size,
      bytes: this.usedBytes,
    });
    for (const entry of this.pending.values()) {
      if (entry.orphanTimer !== null) clearTimeout(entry.orphanTimer);
      entry.controller.abort();
    }
    this.pending.clear();
    this.entries.clear();
    this.usedBytes = 0;
  }

  async getOrLoad(
    key: string,
    load: (signal: AbortSignal) => Promise<DecodedPixels>,
    signal?: AbortSignal,
  ): Promise<DecodedPixels> {
    if (signal?.aborted) throw abortReason(signal);

    const cached = this.entries.get(key);
    if (cached) {
      viewerDebug("cache:hit", { key, bytes: cached.bytes });
      this.entries.delete(key);
      this.entries.set(key, cached);
      return cached.value;
    }

    let pending = this.pending.get(key);
    if (pending?.controller.signal.aborted) {
      this.pending.delete(key);
      pending = undefined;
    }

    if (!pending) {
      viewerDebug("cache:miss", { key });
      const controller = new AbortController();
      pending = {
        controller,
        consumers: new Set(),
        promise: Promise.resolve(null as never),
        settled: false,
        orphanTimer: null,
      };
      const created = pending;
      created.promise = Promise.resolve()
        .then(() => {
          viewerDebug("pixels:load-start", { key });
          return load(controller.signal);
        })
        .then((value) => {
          if (!controller.signal.aborted) this.store(key, value);
          viewerDebug("pixels:load-complete", {
            key,
            bytes: value.data.byteLength,
            cached: !controller.signal.aborted,
          });
          return value;
        })
        .catch((reason: unknown) => {
          viewerDebug("pixels:load-error", {
            key,
            reason: reason instanceof Error ? reason.message : String(reason),
            aborted: controller.signal.aborted,
          });
          throw reason;
        })
        .finally(() => {
          created.settled = true;
          if (created.orphanTimer !== null) clearTimeout(created.orphanTimer);
          if (this.pending.get(key) === created) this.pending.delete(key);
        });
      this.pending.set(key, created);
    } else {
      if (pending.orphanTimer !== null) {
        clearTimeout(pending.orphanTimer);
        pending.orphanTimer = null;
        viewerDebug("cache:orphan-rescued", { key });
      }
      viewerDebug("cache:join-pending", {
        key,
        consumers: pending.consumers.size,
      });
    }

    return this.follow(key, pending, signal);
  }

  private follow(
    key: string,
    entry: PendingEntry,
    signal?: AbortSignal,
  ): Promise<DecodedPixels> {
    const consumer = Symbol("pixel-cache-consumer");
    entry.consumers.add(consumer);

    return new Promise<DecodedPixels>((resolve, reject) => {
      let finished = false;
      const release = () => {
        entry.consumers.delete(consumer);
        signal?.removeEventListener("abort", onAbort);
        if (!entry.settled && entry.consumers.size === 0) {
          this.scheduleOrphanAbort(key, entry);
        }
      };
      const onAbort = () => {
        if (finished || !signal) return;
        finished = true;
        release();
        reject(abortReason(signal));
      };

      signal?.addEventListener("abort", onAbort, { once: true });
      if (signal?.aborted) {
        onAbort();
        return;
      }
      entry.promise.then(
        (value) => {
          if (finished) return;
          finished = true;
          release();
          resolve(value);
        },
        (reason: unknown) => {
          if (finished) return;
          finished = true;
          release();
          reject(reason);
        },
      );
    });
  }

  private scheduleOrphanAbort(key: string, entry: PendingEntry): void {
    if (entry.orphanTimer !== null || entry.controller.signal.aborted) return;
    viewerDebug("cache:orphan-grace", {
      key,
      graceMs: this.orphanGraceMs,
    });
    entry.orphanTimer = setTimeout(() => {
      entry.orphanTimer = null;
      if (entry.settled || entry.consumers.size > 0) return;
      viewerDebug("cache:orphan-abort", { key });
      entry.controller.abort();
    }, this.orphanGraceMs);
  }

  private store(key: string, value: DecodedPixels): void {
    const bytes = value.data.byteLength;
    if (bytes > this.maximumBytes) return;

    const previous = this.entries.get(key);
    if (previous) {
      this.usedBytes -= previous.bytes;
      this.entries.delete(key);
    }
    this.entries.set(key, { value, bytes });
    this.usedBytes += bytes;
    let evicted = 0;

    while (this.usedBytes > this.maximumBytes) {
      const oldest = this.entries.entries().next().value as
        | [string, CacheEntry]
        | undefined;
      if (!oldest) break;
      this.entries.delete(oldest[0]);
      this.usedBytes -= oldest[1].bytes;
      evicted += 1;
    }
    viewerDebug("cache:store", {
      key,
      bytes,
      usedBytes: this.usedBytes,
      completedEntries: this.entries.size,
      evicted,
    });
  }
}

function selectionKey(selection: PixelSelection): string {
  return Object.entries(selection)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([axis, value]) => `${axis}:${value}`)
    .join(",");
}

/** Wrap every source method Viv can use so foreground and prefetch share data. */
export function cacheImageLoader<T extends CacheableImageSource[]>(
  loader: T,
  namespace: string,
  cache: DecodedPixelCache,
): T {
  for (const [resolution, source] of loader.entries()) {
    const marked = source as CacheableImageSource & {
      [CACHED_SOURCE]?: true;
    };
    if (marked[CACHED_SOURCE]) continue;

    const getRaster = source.getRaster.bind(source);
    source.getRaster = (request: RasterRequest) => {
      const key = [
        namespace,
        String(resolution),
        "raster",
        selectionKey(request.selection),
      ].join("|");
      return cache.getOrLoad(
        key,
        (signal) => getRaster({ ...request, signal }),
        request.signal,
      );
    };

    const getTile = source.getTile.bind(source);
    source.getTile = (request: TileRequest) => {
      const key = [
        namespace,
        String(resolution),
        "tile",
        String(request.x),
        String(request.y),
        selectionKey(request.selection),
      ].join("|");
      return cache.getOrLoad(
        key,
        (signal) => getTile({ ...request, signal }),
        request.signal,
      );
    };
    Object.defineProperty(marked, CACHED_SOURCE, { value: true });
  }
  return loader;
}

/**
 * Prefetch one single-resolution timepoint through getRaster, matching Viv's
 * ImageLayer foreground path exactly. Multiscale prefetch needs viewport-aware
 * resolution selection and is deliberately left disabled.
 */
export async function prefetchTimepoint(
  loader: CacheableImageSource[],
  selections: PixelSelection[],
  signal: AbortSignal,
  concurrency = 3,
): Promise<void> {
  const source = loader[0];
  if (loader.length !== 1 || !source || selections.length === 0) return;

  const tasks = selections.map(
    (selection) => () => source.getRaster({ selection, signal }),
  );
  let next = 0;
  const workers = Array.from(
    { length: Math.min(Math.max(1, concurrency), tasks.length) },
    async () => {
      while (next < tasks.length) {
        if (signal.aborted) throw abortReason(signal);
        const task = tasks[next];
        next += 1;
        await task();
      }
    },
  );
  await Promise.all(workers);
}

export const VIEWER_PIXEL_CACHE_BYTES = 128 * 1024 * 1024;
export const viewerPixelCache = new DecodedPixelCache(VIEWER_PIXEL_CACHE_BYTES);
