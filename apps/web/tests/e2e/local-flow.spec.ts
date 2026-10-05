import { expect, test } from "@playwright/test";

import { pixelSignature } from "./viewer";

test("starts a local server, renders time data, and stops it", async ({
  page,
}, testInfo) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (reason) => browserErrors.push(reason.message));

  await page.goto("/");
  await page.getByLabel("Compute profile").selectOption("local");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 30_000,
  });
  await expect(page.getByText(/^127\.0\.0\.1:\d+$/)).toBeVisible();

  await page.getByRole("link", { name: "Data", exact: true }).click();
  const root = page.getByRole("listitem").filter({ hasText: "test-data" });
  await root.getByRole("button", { name: "Open folder" }).click();
  const plate = page.getByRole("listitem").filter({ hasText: "test_plate.zarr" });
  await expect(plate).toContainText("OME-Zarr plate");
  await plate.getByRole("button", { name: "Link" }).click();
  await expect(page.getByRole("heading", { name: "test_plate.zarr" })).toBeVisible();
  const metadataPanel = page.getByTestId("plate-metadata");
  await expect(metadataPanel).toContainText("Plate information");
  await expect(page.getByTestId("compound-filter")).toContainText(
    "Compound Alpha",
  );
  const selectionHelp = page.getByRole("button", { name: "Selection help" });
  const selectionTooltip = page.getByRole("tooltip");
  await expect(selectionTooltip).toBeHidden();
  await selectionHelp.hover();
  await expect(selectionTooltip).toContainText(
    "Choose one well to inspect it, or two wells to compare their time trajectories.",
  );
  await expect(page.locator(".well-selector-legend")).toBeVisible();
  await page
    .getByTestId("compound-filter")
    .selectOption({ value: "Compound Alpha" });
  const firstWell = page.getByRole("button", { name: "Well A1" });
  await expect(firstWell).toHaveClass(/well-highlighted/);
  await expect(page.getByRole("button", { name: "Well A2" })).toHaveClass(
    /well-dimmed/,
  );

  await firstWell.click();
  await expect(metadataPanel).toContainText("Plate and well information");
  await expect(metadataPanel).toContainText("Compound Alpha");
  await expect(metadataPanel).toContainText("1.25");
  await expect(
    metadataPanel.locator("details").filter({ hasText: "Plate and experiment" }),
  ).not.toHaveAttribute("open", "");
  await expect(
    metadataPanel.locator("details").filter({ hasText: "Acquisition" }),
  ).not.toHaveAttribute("open", "");
  await page.getByRole("link", { name: "Open" }).first().click();

  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
  await expect(
    page.getByText("Drag to pan · scroll or pinch to zoom", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByTestId("plate-metadata")).toContainText(
    "Plate and well information",
  );
  await expect(page.getByTestId("plate-metadata")).toContainText(
    "Compound Alpha",
  );
  await expect(
    page.getByRole("slider", { name: /contrast maximum/i }).first(),
  ).toHaveValue("65535");
  const canvas = page.locator(".canvas-frame canvas").first();
  const firstFrame = await pixelSignature(page, canvas);
  expect(firstFrame.range).toBeGreaterThan(5);
  await page.screenshot({
    path: testInfo.outputPath("local-viewer.png"),
    fullPage: true,
  });

  const timeSlider = page.getByTestId("time-slider");
  await timeSlider.fill("1");
  await expect(timeSlider).toHaveValue("1");
  await expect(page.getByTestId("displayed-time")).toHaveText("t 2 / 6");
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  await expect
    .poll(
      async () => (await pixelSignature(page, canvas)).checksum,
      {
        message: "the rendered image should change at timepoint 2",
        timeout: 15_000,
        intervals: [500],
      },
    )
    .not.toBe(firstFrame.checksum);

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Browse TIMED" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Well A1" }).click();
  await page.getByRole("button", { name: "Well A2" }).click();
  await expect(page.getByTestId("well-selection")).toContainText(
    "Choose a site from each well",
  );
  await expect(page.getByRole("button", { name: "Well B1" })).toBeDisabled();
  await page.getByTestId("open-comparison").click();

  await expect(
    page.getByRole("heading", { name: "Compare trajectories" }),
  ).toBeVisible();
  await expect(page.getByTestId("viewer-status")).toHaveText("Images ready", {
    timeout: 60_000,
  });
  const comparison = page.getByTestId("comparison-view");
  await expect(comparison.locator(".comparison-view")).toHaveCount(2);
  await expect(comparison.locator("canvas")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Zoom out" })).toBeEnabled();
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: "Reset view to center" }).click();

  await page.getByRole("button", { name: "Hide controls" }).click();
  await expect(page.locator("#image-controls")).toHaveCount(0);
  await page.getByRole("button", { name: "Show controls" }).click();
  await expect(page.locator("#image-controls")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("local-comparison.png"),
    fullPage: true,
  });

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Disconnect and stop server" })
    .click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
  expect(browserErrors).toEqual([]);
});
