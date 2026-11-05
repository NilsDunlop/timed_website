// Temporary refactor snapshot: exact Data-page /view hrefs and the displayed
// viewer state, recorded from unchanged code on 2026-11-05. The last commit of
// the Gate A refactor deletes this folder.
import type { Locator, Page } from "@playwright/test";

import { connect, disconnect, expect, test } from "../fixtures";
import { Golden, IdMask, unlinkAllDatasets, viewerStateSnapshot } from "./snapshots";

async function follow(page: Page, link: Locator, ready: string) {
  await link.click();
  await expect(page.getByTestId("viewer-status")).toHaveText(ready, {
    timeout: 60_000,
  });
}

async function href(link: Locator, mask: IdMask): Promise<string> {
  return mask.mask((await link.getAttribute("href")) ?? "");
}

test("legacy Data links and TIMED and native viewer states are unchanged", async ({
  page,
}) => {
  const golden = new Golden("data-viewer-legacy");
  const mask = new IdMask();
  await connect(page, "local");
  await unlinkAllDatasets(page);
  await page.getByRole("link", { name: "Data", exact: true }).click();
  const root = page.getByRole("listitem").filter({ hasText: "test-data" });
  await root.getByRole("button", { name: "Open folder" }).click();

  const plate = page.getByRole("listitem").filter({ hasText: "test_plate.zarr" });
  await plate.getByRole("button", { name: "Link" }).click();
  await expect(page.getByRole("heading", { name: "test_plate.zarr" })).toBeVisible();
  await page.getByRole("button", { name: "Well A1" }).click();
  const openSelected = page.getByTestId("open-selected");
  golden.record("timed single href", await href(openSelected, mask));
  await follow(page, openSelected, "Image ready");
  golden.record("timed single", await viewerStateSnapshot(page));

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await page.getByRole("button", { name: "Well B1" }).click();
  await root.getByRole("button", { name: "Open folder" }).click();
  const secondPlate = page.getByRole("listitem").filter({ hasText: "test_plate_2.zarr" });
  await secondPlate.getByRole("button", { name: "Link" }).click();
  await expect(page.getByRole("heading", { name: "test_plate_2.zarr" })).toBeVisible();
  await page.getByRole("button", { name: "Well B1" }).click();
  const openComparison = page.getByTestId("open-comparison");
  await expect(openComparison).toHaveText("Compare 2 trajectories");
  golden.record("timed comparison href", await href(openComparison, mask));
  await follow(page, openComparison, "Images ready");
  golden.record("timed comparison", await viewerStateSnapshot(page));

  for (const store of ["test_image_v04.zarr", "test_image_v05.zarr"]) {
    await page.getByRole("link", { name: "Data", exact: true }).click();
    await root.getByRole("button", { name: "Open folder" }).click();
    const entry = page.getByRole("listitem").filter({ hasText: store });
    await entry.getByRole("button", { name: "Link" }).click();
    await expect(page.getByRole("heading", { name: store })).toBeVisible();
    const openImage = page.getByRole("link", { name: "Open image" });
    golden.record(`${store} href`, await href(openImage, mask));
    await follow(page, openImage, "Image ready");
    golden.record(store, await viewerStateSnapshot(page));
  }

  await disconnect(page);
  golden.check();
});

test("collection Data links and TIMED and Cell Health viewer states are unchanged", async ({
  page,
}) => {
  const golden = new Golden("data-viewer-collections");
  const mask = new IdMask();
  await connect(page, "local-datasets", { dataset: "timed" });
  await unlinkAllDatasets(page);
  await page.getByRole("link", { name: "Data", exact: true }).click();
  const timedPlate = page.locator(".file-list li").filter({
    has: page.getByText("test_plate", { exact: true }),
  });
  await timedPlate.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.getByRole("heading", { name: "test_plate" })).toBeVisible();
  await page.getByRole("button", { name: "Well A1" }).click();
  const openTimed = page.getByTestId("open-selected");
  golden.record("timed collection href", await href(openTimed, mask));
  await follow(page, openTimed, "Image ready");
  golden.record("timed collection", await viewerStateSnapshot(page));

  await page.goto("/data?collection=idr0080");
  const experiment = page.locator(".file-list li").filter({
    has: page.getByText("A549", { exact: true }),
  });
  await experiment.getByRole("button", { name: "Open folder" }).click();
  const cellHealthPlate = "Cell Health · A549 · SQ00014610";
  const packedPlate = page.locator(".file-list li").filter({
    has: page.getByText(cellHealthPlate, { exact: true }),
  });
  await packedPlate.getByRole("button", { name: "Open", exact: true }).click();
  await expect(
    page.getByRole("heading", { level: 2, name: cellHealthPlate, exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Well A1" }).click();
  const openField = page.getByTestId("open-selected");
  golden.record("cell health href", await href(openField, mask));
  await follow(page, openField, "Image ready");
  golden.record("cell health color", await viewerStateSnapshot(page));
  await page
    .locator("#image-controls")
    .getByRole("radio", { name: "Grayscale", exact: true })
    .check();
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  golden.record("cell health grayscale", await viewerStateSnapshot(page));

  await page.getByRole("link", { name: "Back to data" }).click();
  await expect(
    page.getByRole("heading", { level: 2, name: cellHealthPlate, exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Well C1" }).click();
  const compare = page.getByTestId("open-comparison");
  await expect(compare).toHaveText("Compare 2 fields");
  golden.record("cell health comparison href", await href(compare, mask));
  await follow(page, compare, "Images ready");
  golden.record("cell health comparison", await viewerStateSnapshot(page));

  await disconnect(page);
  golden.check();
});
