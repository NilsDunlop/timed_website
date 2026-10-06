import { viewerDebug } from "./viewerDebug";

export type ByteRange =
  | { offset: number; length: number }
  | { suffixLength: number };

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted", "AbortError");
}

/**
 * Fetch-backed Zarr v3 store.
 *
 * Explicit range reads bypass the browser HTTP cache because Chromium stores
 * partial responses as sparse cache entries and rejects concurrent operations
 * against one entry. The small immutable shard footer is shared in memory so
 * disabling the HTTP cache does not duplicate identical suffix requests.
 */
export class V3FetchStore {
  private root: URL;
  private suffixReads = new Map<string, Promise<Uint8Array | undefined>>();

  constructor(root: string) {
    this.root = new URL(root);
    if (!this.root.pathname.endsWith("/")) this.root.pathname += "/";
  }

  private url(key: string): URL {
    return new URL(key.replace(/^\//, ""), this.root);
  }

  private async read(response: Response): Promise<Uint8Array | undefined> {
    if (response.status === 404) return undefined;
    if (response.status !== 200 && response.status !== 206) {
      throw new Error(
        "Unexpected Zarr response " +
          String(response.status) +
          " " +
          response.statusText,
      );
    }
    return new Uint8Array(await response.arrayBuffer());
  }

  private async request(
    key: string,
    options: RequestInit,
  ): Promise<Uint8Array | undefined> {
    const url = this.url(key);
    const started = performance.now();
    const range = new Headers(options.headers).get("Range");
    viewerDebug("fetch:start", {
      key,
      range,
      path: url.pathname,
      cacheMode: options.cache ?? "default",
    });
    try {
      const response = await fetch(url, options);
      const value = await this.read(response);
      viewerDebug("fetch:complete", {
        key,
        range,
        status: response.status,
        bytes: value?.byteLength ?? 0,
        durationMs: Math.round(performance.now() - started),
        cache: response.headers.get("X-TIMED-Cache") ?? response.headers.get("Age"),
      });
      return value;
    } catch (reason) {
      viewerDebug("fetch:error", {
        key,
        range,
        durationMs: Math.round(performance.now() - started),
        reason: reason instanceof Error ? reason.message : String(reason),
      });
      throw reason;
    }
  }

  async get(
    key: string,
    options: RequestInit = {},
  ): Promise<Uint8Array | undefined> {
    if (key.endsWith("/.zattrs") || key.endsWith("/.zgroup")) {
      return undefined;
    }
    return this.request(key, options);
  }

  async getRange(
    key: string,
    range: ByteRange,
    options: RequestInit = {},
  ): Promise<Uint8Array | undefined> {
    if (options.signal?.aborted) throw abortReason(options.signal);

    const headers = new Headers(options.headers);
    if ("suffixLength" in range) {
      headers.set("Range", "bytes=-" + String(range.suffixLength));
    } else {
      headers.set(
        "Range",
        "bytes=" +
          String(range.offset) +
          "-" +
          String(range.offset + range.length - 1),
      );
    }
    const requestOptions: RequestInit = {
      ...options,
      headers,
      cache: "no-store",
    };

    if ("suffixLength" in range) {
      const cacheKey = key + "|suffix:" + String(range.suffixLength);
      const existing = this.suffixReads.get(cacheKey);
      if (existing) {
        viewerDebug("range:suffix-share", {
          key,
          suffixLength: range.suffixLength,
        });
        return existing;
      }

      const { signal: _consumerSignal, ...sharedOptions } = requestOptions;
      const pending = this.request(key, sharedOptions).catch((reason: unknown) => {
        this.suffixReads.delete(cacheKey);
        throw reason;
      });
      this.suffixReads.set(cacheKey, pending);
      viewerDebug("range:suffix-start", {
        key,
        suffixLength: range.suffixLength,
      });
      return pending;
    }

    return this.request(key, requestOptions);
  }
}
