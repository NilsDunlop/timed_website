import { expect, test } from "@playwright/test";

import { pixelSignature } from "./viewer";

test("starts a local server, renders time data, and stops it", async ({
  page,
}, testInfo) => {
  const browserErrors: string[] = [];
  const viewerDiagnostics: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
    if (
      message.type() === "info" &&
      message.text().startsWith("[TIMED viewer")
    ) {
      viewerDiagnostics.push(message.text());
    }
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
    "Choose one well to inspect it, or keep it in the comparison tray while you open another linked experiment and choose a second well.",
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
  await page.evaluate(() =>
    window.localStorage.setItem("timed.viewer.debug", "1"),
  );
  await page.getByTestId("open-selected").click();

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
  expect(
    viewerDiagnostics.some((message) => message.includes("viewer:loader-ready")),
  ).toBe(true);
  expect(
    viewerDiagnostics.some((message) => message.includes("frame:ready")),
  ).toBe(true);
  expect(
    viewerDiagnostics.some((message) => message.includes("fetch:complete")),
  ).toBe(true);
  const canvas = page.locator(".canvas-frame canvas").first();
  const firstFrame = await pixelSignature(page, canvas);
  expect(firstFrame.range).toBeGreaterThan(5);
  await page.screenshot({
    path: testInfo.outputPath("local-viewer.png"),
    fullPage: true,
  });

  const timeSlider = page.getByTestId("time-slider");
  const timeJump = page.getByTestId("time-jump");
  await expect(timeJump).toContainText("1 h");
  await timeJump.click();
  await expect(timeJump).toContainText("2 h");
  await timeJump.click();
  await expect(timeJump).toContainText("4 h");
  await expect
    .poll(
      () =>
        viewerDiagnostics.some(
          (message) =>
            message.includes("prefetch:complete") &&
            message.includes("timepoint: 4"),
        ),
      {
        message: "the four-hour jump destination should be prefetched",
        timeout: 15_000,
      },
    )
    .toBe(true);
  const transitionStart = viewerDiagnostics.length;
  await timeSlider.focus();
  await timeSlider.press("ArrowRight");
  await expect(timeSlider).toHaveValue("3");
  await expect(page.getByTestId("displayed-time")).toHaveText("t 4 / 6");
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  expect(
    viewerDiagnostics.some((message) => message.includes("time:jump-change")),
  ).toBe(true);
  expect(
    viewerDiagnostics.some((message) => message.includes("time:commit")),
  ).toBe(true);
  const transitionDiagnostics = viewerDiagnostics.slice(transitionStart);
  const committedAt = transitionDiagnostics.findIndex((message) =>
    message.includes("time:commit"),
  );
  const readyAt = transitionDiagnostics.findIndex((message) =>
    message.includes("frame:ready"),
  );
  expect(committedAt).toBeGreaterThanOrEqual(0);
  expect(readyAt).toBeGreaterThan(committedAt);
  const foregroundLoad = transitionDiagnostics.slice(committedAt, readyAt + 1);
  expect(
    foregroundLoad.filter((message) => message.includes("cache:hit")),
  ).toHaveLength(3);
  expect(
    foregroundLoad.some((message) => message.includes("fetch:start")),
  ).toBe(false);
  await expect
    .poll(
      async () => (await pixelSignature(page, canvas)).checksum,
      {
        message: "the rendered image should change at the four-hour jump destination",
        timeout: 15_000,
        intervals: [500],
      },
    )
    .not.toBe(firstFrame.checksum);

  await expect
    .poll(
      () =>
        viewerDiagnostics.some(
          (message) =>
            message.includes("prefetch:complete") &&
            message.includes("timepoint: 6"),
        ),
      {
        message: "the final timepoint should be prefetched while moving forward",
        timeout: 15_000,
      },
    )
    .toBe(true);
  await timeSlider.press("ArrowRight");
  await expect(timeSlider).toHaveValue("5");
  await expect(page.getByTestId("displayed-time")).toHaveText("t 6 / 6");
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");

  const endpointStart = viewerDiagnostics.length;
  await expect
    .poll(
      () =>
        viewerDiagnostics
          .slice(endpointStart)
          .some(
            (message) =>
              message.includes("prefetch:complete") &&
              message.includes("timepoint: 4"),
          ),
      {
        message: "the previous jump should be prefetched at the endpoint",
        timeout: 15_000,
      },
    )
    .toBe(true);

  const backwardStart = viewerDiagnostics.length;
  await timeSlider.press("ArrowLeft");
  await expect(timeSlider).toHaveValue("3");
  await expect(page.getByTestId("displayed-time")).toHaveText("t 4 / 6");
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  const backwardDiagnostics = viewerDiagnostics.slice(backwardStart);
  expect(
    backwardDiagnostics.filter((message) => message.includes("cache:hit")),
  ).toHaveLength(3);
  expect(
    backwardDiagnostics.some((message) => message.includes("frame:ready")),
  ).toBe(true);

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Browse TIMED" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Well B1" }).click();
  const comparisonTray = page.getByTestId("well-selection");
  await expect(comparisonTray).toContainText("test_plate.zarr");
  await expect(comparisonTray).toContainText("Choose a site for side A");

  await root.getByRole("button", { name: "Open folder" }).click();
  const comparisonPlate = page
    .getByRole("listitem")
    .filter({ hasText: "test_plate_2.zarr" });
  await comparisonPlate.getByRole("button", { name: "Link" }).click();
  await expect(
    page.getByRole("heading", { name: "test_plate_2.zarr" }),
  ).toBeVisible();
  await expect(comparisonTray).toContainText("test_plate.zarr");
  await page.getByRole("button", { name: "Well B1" }).click();
  await expect(page.getByTestId("well-selection")).toContainText(
    "Choose a site from each well",
  );
  await expect(comparisonTray).toContainText("test_plate_2.zarr");
  await page.getByTestId("open-comparison").click();

  await expect(page).toHaveURL(/compareDataset=/);
  await expect(
    page.getByRole("heading", { name: "Compare trajectories" }),
  ).toBeVisible();
  await expect(page.getByTestId("viewer-status")).toHaveText("Images ready", {
    timeout: 60_000,
  });
  const comparison = page.getByTestId("comparison-view");
  await expect(comparison.locator(".comparison-view")).toHaveCount(2);
  await expect(comparison.locator("canvas")).toHaveCount(2);
  await expect(
    page.getByRole("heading", { name: "Well A · test_plate.zarr" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Well B · test_plate_2.zarr" }),
  ).toBeVisible();
  const comparisonMetadata = page.getByTestId("plate-metadata");
  await expect(comparisonMetadata).toHaveCount(2);
  await expect(comparisonMetadata.nth(0)).toContainText("S1.1");
  await expect(comparisonMetadata.nth(1)).toContainText("S1.2");
  const secondMetadataBeforeExpansion =
    await comparisonMetadata.nth(1).boundingBox();
  const scrollBeforeExpansion = await page.evaluate(() => window.scrollY);
  await comparisonMetadata
    .nth(0)
    .getByText("Plate and experiment", { exact: true })
    .click();
  await expect(
    comparisonMetadata.nth(0).getByText("S1.1", { exact: true }),
  ).toBeVisible();
  const secondMetadataAfterExpansion =
    await comparisonMetadata.nth(1).boundingBox();
  const scrollAfterExpansion = await page.evaluate(() => window.scrollY);
  expect(secondMetadataBeforeExpansion).not.toBeNull();
  expect(secondMetadataAfterExpansion).not.toBeNull();
  expect(
    (secondMetadataAfterExpansion?.y ?? 0) + scrollAfterExpansion,
  ).toBeCloseTo(
    (secondMetadataBeforeExpansion?.y ?? 0) + scrollBeforeExpansion,
    1,
  );
  expect(secondMetadataAfterExpansion?.height).toBe(
    secondMetadataBeforeExpansion?.height,
  );
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
