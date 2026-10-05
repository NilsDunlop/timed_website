export type TilePixels = {
  data: ArrayBufferView;
  width: number;
  height: number;
};

export type TileSelection = Record<string, number>;

export type TileRequest = {
  x: number;
  y: number;
  selection: TileSelection;
  signal?: AbortSignal;
};

export type CacheableTileSource = {
  shape: number[];
  labels: string[];
  tileSize: number;
  getTile(request: TileRequest): Promise<TilePixels>;
};

type CacheEntry = {
  value: TilePixels;
  bytes: number;
};

type PendingEntry = {
  controller: AbortController;
  consumers: Set<symbol>;
  promise: Promise<TilePixels>;
  settled: boolean;
};

const CACHED_SOURCE = Symbol("timed-cached-tile-source");

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted", "AbortError");
}

export class DecodedTileCache {
  private entries = new Map<string, CacheEntry>();
  private pending = new Map<string, PendingEntry>();
  private usedBytes = 0;

  constructor(readonly maximumBytes: number) {
    if (!Number.isFinite(maximumBytes) || maximumBytes <= 0) {
      throw new Error("Decoded tile cache size must be a positive number");
    }
  }

  get byteLength(): number {
    return this.usedBytes;
  }

  get size(): number {
    return this.entries.size;
  }

  clear(): void {
    for (const entry of this.pending.values()) entry.controller.abort();
    this.pending.clear();
    this.entries.clear();
    this.usedBytes = 0;
  }

  async getOrLoad(
    key: string,
    load: (signal: AbortSignal) => Promise<TilePixels>,
    signal?: AbortSignal,
  ): Promise<TilePixels> {
    if (signal?.aborted) throw abortReason(signal);

    const cached = this.entries.get(key);
    if (cached) {
      this.entries.delete(key);
      this.entries.set(key, cached);
      return cached.value;
    }

    let pending = this.pending.get(key);
    if (!pending) {
      const controller = new AbortController();
      pending = {
        controller,
        consumers: new Set(),
        promise: Promise.resolve(null as never),
        settled: false,
      };
      const created = pending;
      created.promise = Promise.resolve()
        .then(() => load(controller.signal))
        .then((value) => {
          if (!controller.signal.aborted) this.store(key, value);
          return value;
        })
        .finally(() => {
          created.settled = true;
          if (this.pending.get(key) === created) this.pending.delete(key);
        });
      this.pending.set(key, created);
    }

    return this.follow(pending, signal);
  }

  private follow(entry: PendingEntry, signal?: AbortSignal): Promise<TilePixels> {
    const consumer = Symbol("tile-cache-consumer");
    entry.consumers.add(consumer);

    return new Promise<TilePixels>((resolve, reject) => {
      let finished = false;
      const release = () => {
        entry.consumers.delete(consumer);
        signal?.removeEventListener("abort", onAbort);
        if (!entry.settled && entry.consumers.size === 0) entry.controller.abort();
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

  private store(key: string, value: TilePixels): void {
    const bytes = value.data.byteLength;
    if (bytes > this.maximumBytes) return;

    const previous = this.entries.get(key);
    if (previous) {
      this.usedBytes -= previous.bytes;
      this.entries.delete(key);
    }
    this.entries.set(key, { value, bytes });
    this.usedBytes += bytes;

    while (this.usedBytes > this.maximumBytes) {
      const oldest = this.entries.entries().next().value as
        | [string, CacheEntry]
        | undefined;
      if (!oldest) break;
      this.entries.delete(oldest[0]);
      this.usedBytes -= oldest[1].bytes;
    }
  }
}

function selectionKey(selection: TileSelection): string {
  return Object.entries(selection)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([axis, value]) => `${axis}:${value}`)
    .join(",");
}

export function cacheTileLoader<T extends CacheableTileSource[]>(
  loader: T,
  namespace: string,
  cache: DecodedTileCache,
): T {
  for (const [resolution, source] of loader.entries()) {
    const marked = source as CacheableTileSource & {
      [CACHED_SOURCE]?: true;
    };
    if (marked[CACHED_SOURCE]) continue;

    const getTile = source.getTile.bind(source);
    source.getTile = (request: TileRequest) => {
      const key = [
        namespace,
        String(resolution),
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

export async function prefetchTimepoint(
  loader: CacheableTileSource[],
  selections: TileSelection[],
  signal: AbortSignal,
  concurrency = 6,
): Promise<void> {
  const source = loader[0];
  if (!source || selections.length === 0) return;

  const xIndex = source.labels.indexOf("x");
  const yIndex = source.labels.indexOf("y");
  if (xIndex < 0 || yIndex < 0) return;

  const columns = Math.ceil(source.shape[xIndex] / source.tileSize);
  const rows = Math.ceil(source.shape[yIndex] / source.tileSize);
  const tasks: Array<() => Promise<TilePixels>> = [];
  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < columns; x += 1) {
      for (const selection of selections) {
        tasks.push(() => source.getTile({ x, y, selection, signal }));
      }
    }
  }

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

export const VIEWER_TILE_CACHE_BYTES = 128 * 1024 * 1024;
export const viewerTileCache = new DecodedTileCache(VIEWER_TILE_CACHE_BYTES);
