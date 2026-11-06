import { expect, test } from "@playwright/test";

import { V3FetchStore } from "../../src/lib/v3FetchStore";

type FetchCall = {
  cache: RequestCache | undefined;
  range: string | null;
  signal: AbortSignal | null | undefined;
  url: string;
};

function call(input: URL | RequestInfo, init: RequestInit | undefined): FetchCall {
  return {
    cache: init?.cache,
    range: new Headers(init?.headers).get("Range"),
    signal: init?.signal,
    url: String(input),
  };
}

test("bypasses HTTP caching only for byte-range reads", async () => {
  const originalFetch = globalThis.fetch;
  const calls: FetchCall[] = [];
  globalThis.fetch = async (input, init) => {
    calls.push(call(input, init));
    const ranged = new Headers(init?.headers).has("Range");
    return new Response(new Uint8Array([1, 2, 3]), {
      status: ranged ? 206 : 200,
    });
  };

  try {
    const store = new V3FetchStore("https://example.test/image.zarr");
    await store.get("zarr.json");
    await store.getRange("s0/c/0", { offset: 10, length: 20 });
  } finally {
    globalThis.fetch = originalFetch;
  }

  expect(calls).toEqual([
    {
      cache: undefined,
      range: null,
      signal: undefined,
      url: "https://example.test/image.zarr/zarr.json",
    },
    {
      cache: "no-store",
      range: "bytes=10-29",
      signal: undefined,
      url: "https://example.test/image.zarr/s0/c/0",
    },
  ]);
});

test("answers Zarr v2 metadata probes without a request", async () => {
  const originalFetch = globalThis.fetch;
  const calls: FetchCall[] = [];
  globalThis.fetch = async (input, init) => {
    calls.push(call(input, init));
    return new Response(new Uint8Array([1]), { status: 200 });
  };

  try {
    const store = new V3FetchStore("https://example.test/image.zarr");
    expect(await store.get("/.zattrs")).toBeUndefined();
    expect(await store.get("/.zgroup")).toBeUndefined();
    expect(await store.get("/s0/.zattrs")).toBeUndefined();
  } finally {
    globalThis.fetch = originalFetch;
  }

  expect(calls).toEqual([]);
});

test("shares and retains one successful shard suffix read", async () => {
  const originalFetch = globalThis.fetch;
  const calls: FetchCall[] = [];
  let finish: ((response: Response) => void) | undefined;
  globalThis.fetch = (input, init) => {
    calls.push(call(input, init));
    return new Promise<Response>((resolve) => {
      finish = resolve;
    });
  };

  try {
    const store = new V3FetchStore("https://example.test/image.zarr/");
    const controller = new AbortController();
    const first = store.getRange(
      "s0/c/0",
      { suffixLength: 36_868 },
      { signal: controller.signal },
    );
    const second = store.getRange("s0/c/0", { suffixLength: 36_868 });

    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      cache: "no-store",
      range: "bytes=-36868",
      signal: undefined,
    });

    finish?.(
      new Response(new Uint8Array([4, 5, 6]), {
        status: 206,
      }),
    );
    await expect(first).resolves.toEqual(new Uint8Array([4, 5, 6]));
    await expect(second).resolves.toEqual(new Uint8Array([4, 5, 6]));
    await expect(
      store.getRange("s0/c/0", { suffixLength: 36_868 }),
    ).resolves.toEqual(new Uint8Array([4, 5, 6]));
    expect(calls).toHaveLength(1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("does not retain a failed shard suffix read", async () => {
  const originalFetch = globalThis.fetch;
  let attempts = 0;
  globalThis.fetch = async () => {
    attempts += 1;
    if (attempts === 1) throw new TypeError("Failed to fetch");
    return new Response(new Uint8Array([7]), { status: 206 });
  };

  try {
    const store = new V3FetchStore("https://example.test/image.zarr/");
    await expect(
      store.getRange("s0/c/0", { suffixLength: 36_868 }),
    ).rejects.toThrow("Failed to fetch");
    await expect(
      store.getRange("s0/c/0", { suffixLength: 36_868 }),
    ).resolves.toEqual(new Uint8Array([7]));
    expect(attempts).toBe(2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
