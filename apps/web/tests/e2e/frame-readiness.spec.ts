import { expect, test } from "@playwright/test";

import { FrameReadinessTracker } from "../../src/lib/frameReadiness";

test("retains a viewport callback that arrives before frame activation", () => {
  const readiness = new FrameReadinessTracker();

  readiness.activate("15:0");
  const early = readiness.record("23:0", "I11_s2.zarr");
  expect(early).toEqual({
    active: false,
    frameKey: "23:0",
    paths: ["I11_s2.zarr"],
  });

  expect(readiness.activate("23:0")).toEqual({
    active: true,
    frameKey: "23:0",
    paths: ["I11_s2.zarr"],
  });
});

test("keeps comparison readiness isolated by frame", () => {
  const readiness = new FrameReadinessTracker();

  readiness.activate("39:0");
  readiness.record("39:0", "well-a.zarr");
  readiness.record("39:0", "well-b.zarr");
  readiness.record("31:0", "obsolete.zarr");

  expect(readiness.pathsFor("39:0")).toEqual([
    "well-a.zarr",
    "well-b.zarr",
  ]);
  expect(readiness.pathsFor("31:0")).toEqual(["obsolete.zarr"]);
  expect(readiness.activeFrame).toBe("39:0");
});

test("clears readiness when a new dataset is loaded", () => {
  const readiness = new FrameReadinessTracker();
  readiness.activate("47:0");
  readiness.record("47:0", "I11_s2.zarr");

  readiness.clear();

  expect(readiness.activeFrame).toBe("");
  expect(readiness.pathsFor("47:0")).toEqual([]);
});

test("prepares a fresh readiness record when revisiting a frame", () => {
  const readiness = new FrameReadinessTracker();
  readiness.activate("39:0");
  readiness.record("39:0", "I11_s2.zarr");

  readiness.activate("47:0");
  readiness.prepare("39:0");

  expect(readiness.pathsFor("39:0")).toEqual([]);
  expect(readiness.activate("39:0").paths).toEqual([]);
});
