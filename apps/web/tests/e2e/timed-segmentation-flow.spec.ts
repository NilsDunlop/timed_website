import type { Locator, Page, Request } from "@playwright/test";

import { connect, disconnect, expect, test } from "./fixtures";
import { colorPixelCount } from "./viewer";

// Fixture masks come from scripts/timed_mask_stores.py. The export (masks_raw)
// holds 2 + (field + t) % 4 cells per frame and the production run 5 -
// (field + t) % 4, so the two never agree; in both, every cell but the last
// has a nucleus. Field 0 = test_plate A01 s1, 2 = test_plate B01 s1, 3 =
// test_plate_2 B01 s1, and 4 = test_plate A02 s2, which only the production
// run covers. A01 s2 has no masks.
const PRODUCTION = "R4_cpsam_dS_corrected";
const PRODUCTION_RECIPE = "Cellpose-SAM cells (TiLu-corrected 505 + raw 625 nm)";
const EXPORT = "masks_raw";
const EXPORT_RECIPE = "Cellpose-SAM cells (raw 505 + 625 nm) · classical nuclei (raw 625 nm)";
const WHITE = [255, 255, 255] as const;
const YELLOW = [255, 212, 0] as const;
// The 512 px fixture field is drawn smaller than its pixels, so outline edges
// blend; near-white still comes only from outlines on the dark fixture image.
const NEAR_WHITE = 48;

function exportCells(field: number, t: number): number {
  return 2 + ((field + t) % 4);
}

function productionCells(field: number, t: number): number {
  return 5 - ((field + t) % 4);
}

function countLine(cellCount: number): string {
  const nuclei = cellCount - 1;
  return (
    `${nuclei} ${nuclei === 1 ? "nucleus" : "nuclei"} · ` +
    `${cellCount} ${cellCount === 1 ? "cell" : "cells"}`
  );
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
    recipe: panel.getByTestId("segmentation-recipe"),
    counts: panel.getByTestId("segmentation-counts"),
    cells: panel.getByRole("checkbox", { name: "Cells", exact: true }),
    nuclei: panel.getByRole("checkbox", { name: "Nuclei", exact: true }),
  };
}

async function whitePixels(page: Page, canvas: Locator): Promise<number> {
  return colorPixelCount(page, canvas, WHITE, NEAR_WHITE);
}

async function yellowPixels(page: Page, canvas: Locator): Promise<number> {
  return colorPixelCount(page, canvas, YELLOW, NEAR_WHITE);
}

test("TIMED outlines and counts follow the displayed frame for both models", async ({
  page,
}) => {
  await connect(page, "local-datasets", { dataset: "timed" });
  await page.goto("/data?collection=timed");
  await openTimedPlate(page, "test_plate");
  await page.getByRole("button", { name: "Well A1" }).click();
  const production = outlineTimepoints(page, /\/masks\/R4_cpsam_dS_corrected\/A01\/1\/cells\.png$/);
  const exported = outlineTimepoints(page, /\/masks\/masks_raw\/A01\/1\/cells\.png$/);
  await page.getByTestId("open-selected").click();
  await expectImageReady(page);

  const { panel, model, recipe, counts, cells: cellsBox, nuclei: nucleiBox } = panelParts(page);
  await expect(panel).toBeVisible();
  // The production run is the default; the export stays offered for comparison.
  await expect(model).toHaveValue(PRODUCTION);
  await expect(model.locator("option")).toHaveText([PRODUCTION, EXPORT]);
  await expect(recipe).toHaveText(PRODUCTION_RECIPE);
  await expect(panel.getByTestId("segmentation-unfrozen")).toHaveCount(0);
  await expect(counts).toHaveText(countLine(productionCells(0, 0)));
  await expect(panel.getByRole("group", { name: "Outlines" }).locator("label")).toHaveText([
    "Nuclei",
    "Cells",
  ]);
  await expect(cellsBox).not.toBeChecked();
  await expect(nucleiBox).not.toBeChecked();

  const canvas = page.locator(".canvas-frame canvas").first();
  const background = await whitePixels(page, canvas);
  await cellsBox.check();
  await expect.poll(() => whitePixels(page, canvas)).toBeGreaterThan(background + 40);
  const firstFrame = await whitePixels(page, canvas);
  await expect(page).toHaveURL(/[?&]outlines=cells(&|$)/);

  for (const t of [1, 2]) {
    await stepForward(page, t);
    await expect(counts).toHaveText(countLine(productionCells(0, t)));
  }
  // Frame 2 has three production discs to frame 0's five, so fewer outline pixels.
  await expect
    .poll(async () => {
      const white = await whitePixels(page, canvas);
      return white > background + 40 && white < firstFrame;
    })
    .toBe(true);
  const productionFrame2 = await whitePixels(page, canvas);
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  expect(production.length).toBeGreaterThan(0);
  expect(Math.max(...production)).toBe(2);
  expect(production.every((t) => [0, 1, 2].includes(t))).toBe(true);
  expect(exported).toEqual([]);

  // Frame 3's two production nuclei draw in yellow above the cells.
  const yellowBefore = await yellowPixels(page, canvas);
  await nucleiBox.check();
  await expect.poll(() => yellowPixels(page, canvas)).toBeGreaterThan(yellowBefore + 40);
  await expect(page).toHaveURL(/[?&]outlines=cells%2Cnuclei(&|$)/);

  // The export's model draws its own masks at the same frame.
  await model.selectOption(EXPORT);
  await expect(recipe).toHaveText(EXPORT_RECIPE);
  await expect(counts).toHaveText(countLine(exportCells(0, 2)));
  await expect(page).toHaveURL(/[?&]model=masks_raw(&|$)/);
  await expect.poll(() => exported.includes(2)).toBe(true);
  // Its frame 2 has four discs, more than the production run's three.
  await expect.poll(() => whitePixels(page, canvas)).toBeGreaterThan(productionFrame2);
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");

  await page.reload();
  await expectImageReady(page);
  await expect(model).toHaveValue(EXPORT);
  await expect(cellsBox).toBeChecked();
  await expect(nucleiBox).toBeChecked();
  await expect(counts).toHaveText(countLine(exportCells(0, 0)));
  await expect.poll(() => whitePixels(page, canvas)).toBeGreaterThan(background + 40);
  await disconnect(page);
});

test("a TIMED field only the production run covers offers it alone", async ({ page }) => {
  await connect(page, "local-datasets", { dataset: "timed" });
  await page.goto("/data?collection=timed");
  await openTimedPlate(page, "test_plate");
  await page.getByRole("button", { name: "Well A2" }).click();
  await page.getByRole("combobox", { name: /^Site for / }).selectOption({ label: "Site 2" });
  await page.getByTestId("open-selected").click();
  await expectImageReady(page);

  const { model, recipe, counts, cells: cellsBox } = panelParts(page);
  await expect(model).toHaveValue(PRODUCTION);
  await expect(model.locator("option")).toHaveText([PRODUCTION]);
  await expect(recipe).toHaveText(PRODUCTION_RECIPE);
  await expect(counts).toHaveText(countLine(productionCells(4, 0)));

  const canvas = page.locator(".canvas-frame canvas").first();
  const background = await whitePixels(page, canvas);
  await cellsBox.check();
  await expect.poll(() => whitePixels(page, canvas)).toBeGreaterThan(background + 40);
  await stepForward(page, 1);
  await expect(counts).toHaveText(countLine(productionCells(4, 1)));
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

test("each TIMED comparison panel draws its own source's outlines over time", async ({
  page,
}) => {
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

  const { model, counts, cells: cellsBox } = panelParts(page);
  await expect(model).toHaveValue(PRODUCTION);
  await expect(model.locator("option")).toHaveText([PRODUCTION, EXPORT]);
  const lines = counts.locator("li");
  await expect(lines).toHaveCount(2);
  await expect(lines.nth(0)).toHaveText("A" + countLine(productionCells(2, 0)));
  await expect(lines.nth(1)).toHaveText("B" + countLine(productionCells(3, 0)));

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
  for (const t of [1, 2]) {
    await stepForward(page, t, "Images ready");
    await expect(lines.nth(0)).toHaveText("A" + countLine(productionCells(2, t)));
    await expect(lines.nth(1)).toHaveText("B" + countLine(productionCells(3, t)));
  }
  await model.selectOption(EXPORT);
  await expect(lines.nth(0)).toHaveText("A" + countLine(exportCells(2, 2)));
  await expect(lines.nth(1)).toHaveText("B" + countLine(exportCells(3, 2)));
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
