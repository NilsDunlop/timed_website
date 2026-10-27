import { expect, test } from "@playwright/test";

import {
  defaultViewerDisplayPreference,
  preferredGrayscaleChannelIndex,
  readViewerDisplayPreference,
  writeViewerDisplayPreference,
} from "../../src/lib/viewerDisplay";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem(key: string) {
      return values.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      values.set(key, value);
    },
  };
}

test("defaults to fluorescence and remembers grayscale mode by channel name", () => {
  const storage = memoryStorage();
  expect(readViewerDisplayPreference(storage)).toEqual(
    defaultViewerDisplayPreference,
  );

  writeViewerDisplayPreference(storage, {
    mode: "grayscale",
    grayscaleChannelName: "ER",
  });
  expect(readViewerDisplayPreference(storage)).toEqual({
    mode: "grayscale",
    grayscaleChannelName: "ER",
  });
  expect(
    preferredGrayscaleChannelIndex(["DNA", "ER", "AGP"], "ER"),
  ).toBe(1);
});

test("falls back safely when the stored mode or channel is unavailable", () => {
  const malformed = {
    getItem: () => '{"mode":"split","grayscaleChannelName":"ER"}',
    setItem: () => undefined,
  };
  expect(readViewerDisplayPreference(malformed)).toEqual(
    defaultViewerDisplayPreference,
  );
  expect(preferredGrayscaleChannelIndex(["405 nm", "505 nm"], "ER")).toBe(0);
  expect(preferredGrayscaleChannelIndex([], "ER")).toBeNull();
});
