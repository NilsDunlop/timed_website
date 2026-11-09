import type { Locator, Page } from "@playwright/test";

import { connect, disconnect, expect, test } from "./fixtures";
import { openExperiment, openPlate } from "./idr0080";
import { colorPixelCount } from "./viewer";

// Fixture counts come from scripts/mask_stores.py: 14610 A01 s1 has 4 nuclei and
// 2 cells in the default model and 6 cells in the stock model, which covers 14610 only.
const DEFAULT_MODEL = "idr0080_cpsam_B90";
const STOCK_MODEL = "cpsam_stock_R2_top999";
const PLATE_14610 = "Cell Health · A549 · SQ00014610";
const PLATE_14611 = "Cell Health · A549 · SQ00014611";
const NUCLEI_COLOR = [255, 212, 0] as const;

async function expectImageReady(page: Page, text = "Image ready") {
  await expect(page.getByTestId("viewer-status")).toHaveText(text, { timeout: 60_000 });
}

async function nucleusPixels(page: Page, canvas: Locator): Promise<number> {
  return colorPixelCount(page, canvas, NUCLEI_COLOR);
}

function panelParts(page: Page) {
  const panel = page.getByTestId("segmentation-panel");
  return {
    panel,
    model: panel.getByRole("combobox", { name: "Model", exact: true }),
    counts: panel.getByTestId("segmentation-counts"),
    kind: (name: string) => panel.getByRole("checkbox", { name, exact: true }),
    width: panel.getByRole("slider", { name: "Outline width", exact: true }),
    opacity: panel.getByRole("slider", { name: "Outline opacity", exact: true }),
  };
}

test("outlines a Cell Health field and keeps its outline preset", async ({ page }) => {
  await connect(page, "local-datasets", { dataset: "idr0080" });
  await page.goto("/data?collection=idr0080");
  await openExperiment(page, "A549");
  await openPlate(page, PLATE_14610);
  await page.getByRole("button", { name: "Well A1" }).click();
  await page.getByTestId("open-selected").click();
  await expectImageReady(page);

  const { panel, model, counts, kind, width, opacity } = panelParts(page);
  await expect(panel).toBeVisible();
  await expect(panel.locator("legend").first()).toHaveText("Segmentation");
  await expect(model).toHaveValue(DEFAULT_MODEL);
  // The selector shows each full tag; the selected model's recipe has its own line.
  await expect(model.locator("option")).toHaveText([DEFAULT_MODEL, STOCK_MODEL]);
  await expect(panel.getByTestId("segmentation-recipe")).toHaveText("IDR0080_B90");
  await expect(panel.getByTestId("segmentation-unfrozen")).toHaveText("May still change");
  await expect(counts).toHaveText("4 nuclei · 2 cells · 2 cytoplasm");
  for (const name of ["Nuclei", "Cells", "Cytoplasm"]) {
    await expect(kind(name)).not.toBeChecked();
  }
  await expect(width).toHaveValue("2");
  await expect(opacity).toHaveValue("1");

  const canvas = page.locator(".canvas-frame canvas").first();
  expect(await nucleusPixels(page, canvas)).toBe(0);
  await kind("Nuclei").check();
  await expect.poll(() => nucleusPixels(page, canvas)).toBeGreaterThan(200);
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  await expect(page).toHaveURL(/[?&]outlines=nuclei(&|$)/);
  await expect(page).toHaveURL(new RegExp(`[?&]model=${DEFAULT_MODEL}(&|$)`));

  const twoPixelOutline = await nucleusPixels(page, canvas);
  await width.fill("4");
  await expect(panel).toContainText("Outline width: 4 px");
  await expect.poll(() => nucleusPixels(page, canvas)).toBeGreaterThan(twoPixelOutline);
  await opacity.fill("0.5");
  await expect(panel).toContainText("Outline opacity: 50%");
  await expect.poll(() => nucleusPixels(page, canvas)).toBe(0);
  await opacity.fill("1");
  await expect.poll(() => nucleusPixels(page, canvas)).toBeGreaterThan(twoPixelOutline);

  await model.selectOption(STOCK_MODEL);
  await expect(counts).toHaveText("4 nuclei · 6 cells · 6 cytoplasm");
  await expect(panel.getByTestId("segmentation-recipe")).toHaveText("R2");
  await expect(page).toHaveURL(new RegExp(`[?&]model=${STOCK_MODEL}(&|$)`));
  await expect.poll(() => nucleusPixels(page, canvas)).toBeGreaterThan(0);

  await page.reload();
  await expectImageReady(page);
  await expect(model).toHaveValue(STOCK_MODEL);
  await expect(kind("Nuclei")).toBeChecked();
  await expect(kind("Cells")).not.toBeChecked();
  await expect.poll(() => nucleusPixels(page, canvas)).toBeGreaterThan(0);

  await kind("Nuclei").uncheck();
  await expect.poll(() => nucleusPixels(page, canvas)).toBe(0);
  await expect(page).not.toHaveURL(/[?&](outlines|model)=/);

  await disconnect(page);
});

test("offers only shared models when comparing plates and outlines every panel", async ({
  page,
}) => {
  await connect(page, "local-datasets", { dataset: "idr0080" });
  await page.goto("/data?collection=idr0080");
  await openExperiment(page, "A549");
  await openPlate(page, PLATE_14610);
  await page.getByRole("button", { name: "Well A1" }).click();
  // The listing still shows A549's plates; the selected well carries over.
  await openPlate(page, PLATE_14611);
  await expect(page.getByRole("button", { name: "Well A1" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const compare = page.getByTestId("open-comparison");
  await expect(compare).toHaveText("Compare 2 fields");
  await compare.click();
  await expectImageReady(page, "Images ready");

  const { model, counts, kind } = panelParts(page);
  await expect(model.locator("option")).toHaveText([DEFAULT_MODEL]);
  await expect(counts.locator("li")).toHaveCount(2);
  await expect(counts.locator("li").nth(0)).toHaveText("A4 nuclei · 2 cells · 2 cytoplasm");
  await expect(counts.locator("li").nth(1)).toHaveText(/^B\d+ nuclei/);

  const canvases = page.getByTestId("comparison-view").locator("canvas");
  await expect(canvases).toHaveCount(2);
  for (const index of [0, 1]) {
    expect(await nucleusPixels(page, canvases.nth(index))).toBe(0);
  }
  await kind("Nuclei").check();
  for (const index of [0, 1]) {
    await expect.poll(() => nucleusPixels(page, canvases.nth(index))).toBeGreaterThan(100);
  }
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

test("the QC field view shares the segmentation panel across the review queue", async ({
  page,
}) => {
  await connect(page, "local-datasets", { dataset: "idr0080" });
  await page.getByRole("link", { name: "QC", exact: true }).click();
  await page.getByRole("button", { name: "Review queue", exact: true }).click();
  await expectImageReady(page);
  await expect(page).toHaveURL(/qc=1/);

  const { panel, counts, kind } = panelParts(page);
  await expect(panel).toBeVisible();
  await expect(counts).toHaveText("4 nuclei · 2 cells · 2 cytoplasm");
  const canvas = page.locator(".canvas-frame canvas").first();
  await kind("Nuclei").check();
  await expect.poll(() => nucleusPixels(page, canvas)).toBeGreaterThan(200);

  await page.getByTestId("qc-review-bar").getByRole("button", { name: "Next" }).click();
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectImageReady(page);
  await expect(page).toHaveURL(/[?&]outlines=nuclei(&|$)/);
  await expect(kind("Nuclei")).toBeChecked();
  await expect.poll(() => nucleusPixels(page, canvas)).toBeGreaterThan(100);
  await disconnect(page);
});
