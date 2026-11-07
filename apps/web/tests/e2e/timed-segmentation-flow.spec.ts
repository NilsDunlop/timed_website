import type { Locator, Page, Request } from "@playwright/test";

import { connect, disconnect, expect, test } from "./fixtures";
import { colorPixelCount } from "./viewer";

// Fixture masks come from scripts/timed_mask_stores.py: each frame of a masked
// field holds 2 + (field + t) % 4 discs, field 0 = test_plate A01 s1,
// 2 = test_plate B01 s1 and 3 = test_plate_2 B01 s1. A01 s2 has no masks.
const MODEL = "masks_raw";
const RECIPE = "Cellpose-SAM · raw 505 + 625 nm";
const WHITE = [255, 255, 255] as const;
// The 512 px fixture field is drawn smaller than its pixels, so outline edges
// blend; near-white still comes only from outlines on the dark fixture image.
const NEAR_WHITE = 48;

function cells(field: number, t: number): number {
  return 2 + ((field + t) % 4);
}

function cellLine(count: number): string {
  return `${count} ${count === 1 ? "cell" : "cells"}`;
}

async function expectImageReady(page: Page, text = "Image ready") {
  await expect(page.getByTestId("viewer-status")).toHaveText(text, { timeout: 60_000 });
}

async function openTimedPlate(page: Page, plate: string) {
  const entry = page.locator(".file-list li").filter({
    has: page.getByText(plate, { exact: true }),
  });
  await entry.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: plate, exact: true })).toBeVisible();
}

async function stepForward(page: Page, displayed: number, ready = "Image ready") {
  const slider = page.getByTestId("time-slider");
  await slider.focus();
  await slider.press("ArrowRight");
  await expect(page.getByTestId("displayed-time")).toHaveText(`t ${displayed + 1} / 6`);
  await expectImageReady(page, ready);
}

function outlineTimepoints(page: Page, route: RegExp): number[] {
  const seen: number[] = [];
  page.on("request", (request: Request) => {
    const url = new URL(request.url());
    if (route.test(url.pathname)) seen.push(Number(url.searchParams.get("t")));
  });
  return seen;
}

function panelParts(page: Page) {
  const panel = page.getByTestId("segmentation-panel");
  return {
    panel,
    model: panel.getByRole("combobox", { name: "Model", exact: true }),
    counts: panel.getByTestId("segmentation-counts"),
    cells: panel.getByRole("checkbox", { name: "Cells", exact: true }),
  };
}

async function whitePixels(page: Page, canvas: Locator): Promise<number> {
  return colorPixelCount(page, canvas, WHITE, NEAR_WHITE);
}

test("TIMED outlines and counts follow the displayed frame", async ({ page }) => {
  await connect(page, "local-datasets", { dataset: "timed" });
  await page.goto("/data?collection=timed");
  await openTimedPlate(page, "test_plate");
  await page.getByRole("button", { name: "Well A1" }).click();
  const requested = outlineTimepoints(page, /\/masks\/masks_raw\/A01\/1\/cells\.png$/);
  await page.getByTestId("open-selected").click();
  await expectImageReady(page);

  const { panel, model, counts, cells: cellsBox } = panelParts(page);
  await expect(panel).toBeVisible();
  await expect(model).toHaveValue(MODEL);
  await expect(model.locator("option")).toHaveText([MODEL]);
  await expect(panel.getByTestId("segmentation-recipe")).toHaveText(RECIPE);
  await expect(panel.getByTestId("segmentation-unfrozen")).toHaveCount(0);
  await expect(counts).toHaveText(cellLine(cells(0, 0)));
  await expect(cellsBox).not.toBeChecked();

  const canvas = page.locator(".canvas-frame canvas").first();
  const background = await whitePixels(page, canvas);
  await cellsBox.check();
  await expect.poll(() => whitePixels(page, canvas)).toBeGreaterThan(background + 40);
  const firstFrame = await whitePixels(page, canvas);
  await expect(page).toHaveURL(/[?&]outlines=cells(&|$)/);

  for (const t of [1, 2]) {
    await stepForward(page, t);
    await expect(counts).toHaveText(cellLine(cells(0, t)));
  }
  // Frame 2 has four discs to frame 0's two, so its outline covers more pixels.
  await expect.poll(() => whitePixels(page, canvas)).toBeGreaterThan(firstFrame);
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  expect(requested.length).toBeGreaterThan(0);
  expect(Math.max(...requested)).toBe(2);
  expect(requested.every((t) => [0, 1, 2].includes(t))).toBe(true);

  await page.reload();
  await expectImageReady(page);
  await expect(cellsBox).toBeChecked();
  await expect(counts).toHaveText(cellLine(cells(0, 0)));
  await expect.poll(() => whitePixels(page, canvas)).toBeGreaterThan(background + 40);
  await disconnect(page);
});

test("a TIMED field without masks shows no segmentation panel", async ({ page }) => {
  await connect(page, "local-datasets", { dataset: "timed" });
  await page.goto("/data?collection=timed");
  await openTimedPlate(page, "test_plate");
  await page.getByRole("button", { name: "Well A1" }).click();
  await page.getByRole("combobox", { name: /^Site for / }).selectOption({ label: "Site 2" });
  await page.getByTestId("open-selected").click();
  await expectImageReady(page);
  await expect(page.getByTestId("segmentation-panel")).toHaveCount(0);

  const preset = new URL(page.url());
  preset.searchParams.set("outlines", "cells");
  await page.goto(preset.pathname + preset.search);
  await expectImageReady(page);
  await expect(page.getByTestId("segmentation-panel")).toHaveCount(0);
  await disconnect(page);
});

test("each TIMED comparison panel draws its own source's outlines", async ({ page }) => {
  await connect(page, "local-datasets", { dataset: "timed" });
  await page.goto("/data?collection=timed");
  await openTimedPlate(page, "test_plate");
  await page.getByRole("button", { name: "Well B1" }).click();
  await openTimedPlate(page, "test_plate_2");
  await page.getByRole("button", { name: "Well B1" }).click();
  const compare = page.getByTestId("open-comparison");
  await expect(compare).toHaveText("Compare 2 trajectories");
  await compare.click();
  await expectImageReady(page, "Images ready");

  const { counts, cells: cellsBox } = panelParts(page);
  const lines = counts.locator("li");
  await expect(lines).toHaveCount(2);
  await expect(lines.nth(0)).toHaveText("A" + cellLine(cells(2, 0)));
  await expect(lines.nth(1)).toHaveText("B" + cellLine(cells(3, 0)));

  const canvases = page.getByTestId("comparison-view").locator("canvas");
  await expect(canvases).toHaveCount(2);
  const backgrounds = [
    await whitePixels(page, canvases.nth(0)),
    await whitePixels(page, canvases.nth(1)),
  ];
  await cellsBox.check();
  for (const index of [0, 1]) {
    await expect
      .poll(() => whitePixels(page, canvases.nth(index)))
      .toBeGreaterThan(backgrounds[index] + 40);
  }
  await stepForward(page, 1, "Images ready");
  await expect(lines.nth(0)).toHaveText("A" + cellLine(cells(2, 1)));
  await expect(lines.nth(1)).toHaveText("B" + cellLine(cells(3, 1)));
  await expect(page.getByTestId("viewer-status")).toHaveText("Images ready");

  await page.getByRole("link", { name: "Back to data" }).click();
  const opened = page.locator(".dataset-list li");
  while ((await opened.count()) > 0) {
    const count = await opened.count();
    await opened.first().getByRole("button", { name: "Remove", exact: true }).click();
    await expect(opened).toHaveCount(count - 1);
  }
  await disconnect(page);
});
