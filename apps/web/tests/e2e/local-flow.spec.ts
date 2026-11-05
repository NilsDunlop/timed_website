import { expect, test } from "@playwright/test";

import { pixelSignature } from "./viewer";

test("starts a local server, renders time data, and stops it", async ({
  page,
}, testInfo) => {
  const browserErrors: string[] = [];
  const viewerDiagnostics: string[] = [];
  const qcRequests: string[] = [];
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
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/backend/api/qc/")) {
      qcRequests.push(request.url());
    }
  });

  await page.goto("/");
  await page.getByLabel("Compute profile").selectOption("local");
  await expect(
    page.getByRole("combobox", { name: "Dataset" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 30_000,
  });
  await expect(page.getByText(/^127\.0\.0\.1:\d+$/)).toBeVisible();

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Dataset" }),
  ).toHaveCount(0);
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
    "Choose one well to inspect it, or keep up to four wells in the comparison tray while you move between linked experiments.",
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
  const browserBiologicalAnnotation = metadataPanel
    .locator("details")
    .filter({ hasText: "Biological annotation" });
  await expect(browserBiologicalAnnotation).toContainText("MOA");
  await expect(browserBiologicalAnnotation).not.toHaveAttribute("open", "");
  await expect(
    metadataPanel.locator("details").filter({ hasText: "Plate and experiment" }),
  ).not.toHaveAttribute("open", "");
  await expect(
    metadataPanel.locator("details").filter({ hasText: "Acquisition" }),
  ).not.toHaveAttribute("open", "");
  await page.evaluate(() =>
    window.localStorage.setItem("timed.viewer.debug", "1"),
  );
  const openSelected = page.getByTestId("open-selected");
  await expect(openSelected).toHaveText("Open trajectory");
  await page.evaluate(() =>
    window.sessionStorage.setItem(
      "timed.viewer.display.v1",
      JSON.stringify({ mode: "grayscale", grayscaleChannelName: "505 nm" }),
    ),
  );
  await openSelected.click();

  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
  await expect(page.getByTestId("qc-viewer-panel")).toHaveCount(0);
  await expect(
    page.getByRole("checkbox", { name: "Use QC display ranges" }),
  ).toHaveCount(0);
  expect(qcRequests).toEqual([]);
  await expect(page.locator(".canvas-frame").first()).toHaveAttribute(
    "data-scale-bar",
    "true",
  );
  await expect(
    page
      .locator("#image-controls")
      .getByRole("radiogroup", { name: "Display", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Image viewer for Compound Alpha",
    }),
  ).toBeAttached();
  const singleSourceFacts = page.getByTestId("viewer-source-facts");
  await expect(singleSourceFacts).toContainText("Compound");
  await expect(singleSourceFacts).toContainText("Compound Alpha");
  await expect(singleSourceFacts).toContainText("Concentration");
  await expect(singleSourceFacts).toContainText("1.25 µM");
  await expect(singleSourceFacts).toContainText("Well");
  await expect(singleSourceFacts).toContainText("A01");
  await expect(singleSourceFacts).toContainText("Site");
  await expect(singleSourceFacts).toContainText("1");
  await expect(singleSourceFacts).toContainText("Plate");
  await expect(singleSourceFacts).toContainText("S1.1");
  await expect(page.locator(".viewer-title-content")).not.toContainText("|");
  await expect(page.locator(".viewer-stage")).not.toContainText(".zarr");
  await expect(
    page.getByText("Drag to pan · scroll or pinch to zoom", { exact: true }),
  ).toHaveCount(0);
  const viewerMetadata = page.getByTestId("plate-metadata");
  await expect(viewerMetadata.locator("summary")).toHaveText([
    "Biological annotation",
    "Plate and experiment",
    "Acquisition",
  ]);
  await expect(viewerMetadata).not.toContainText("Plate and well information");
  await expect(viewerMetadata).toContainText("MOA");
  await expect(viewerMetadata).toContainText("AKT");
  for (const details of await viewerMetadata.locator("details").all()) {
    await expect(details).not.toHaveAttribute("open", "");
  }
  for (const channel of ["405 nm", "505 nm", "625 nm"]) {
    const checkbox = page.getByRole("checkbox", {
      name: channel,
      exact: true,
    });
    const channelControl = page.locator(".channel-control").filter({
      has: checkbox,
    });
    await expect(checkbox).toBeVisible();
    await expect(
      page.getByRole("slider", {
        name: `${channel} contrast minimum`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("slider", {
        name: `${channel} contrast maximum`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(channelControl).toContainText("Contrast minimum: 100");
    await expect(channelControl).toContainText("Contrast maximum: 65535");
  }
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
  await expect(timeSlider).toBeVisible();
  await expect(timeJump).toBeVisible();
  const timeHelp = page.getByRole("button", {
    name: "Time navigation help",
  });
  const timeTooltip = page.getByRole("tooltip");
  await expect(timeTooltip).toBeHidden();
  await timeHelp.hover();
  await expect(timeTooltip).toContainText(
    "Fast-forward changes the jump. Arrow keys navigate, including when controls are hidden. Dragging selects any frame.",
  );
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

  await page.getByRole("button", { name: "Hide controls" }).click();
  await expect(page.locator("#image-controls")).toHaveCount(0);
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("displayed-time")).toHaveText("t 6 / 6");
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("displayed-time")).toHaveText("t 4 / 6");
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");
  expect(
    viewerDiagnostics.some((message) =>
      message.includes("time:keyboard-jump"),
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Show controls" }).click();
  await expect(page.getByTestId("time-slider")).toHaveValue("3");

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Browse datasets", exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Well B1" }).click();
  const comparisonTray = page.getByTestId("well-selection");
  await expect(comparisonTray).toContainText("test_plate.zarr");
  await expect(comparisonTray).toContainText("Choose a site for side A");

  await root.getByRole("button", { name: "Open folder" }).click();
  const comparisonPlateNames = [
    "test_plate_2.zarr",
    "test_plate_3.zarr",
    "test_plate_4.zarr",
  ];
  for (const [index, plateName] of comparisonPlateNames.entries()) {
    const comparisonPlate = page
      .getByRole("listitem")
      .filter({ hasText: plateName });
    await comparisonPlate.getByRole("button", { name: "Link" }).click();
    await expect(
      page.getByRole("heading", { name: plateName }),
    ).toBeVisible();
    await expect(comparisonTray).toContainText("test_plate.zarr");
    await page.getByRole("button", { name: "Well B1" }).click();
    await expect(comparisonTray).toContainText(plateName);
    await expect(
      comparisonTray.locator(".comparison-source-card"),
    ).toHaveCount(index + 2);

    if (index === 1) {
      const trayGrid = comparisonTray.locator(".comparison-field-grid");
      await expect(trayGrid).toHaveAttribute("data-count", "3");
      const gridBox = await trayGrid.boundingBox();
      const thirdBox = await trayGrid
        .locator(".comparison-source-card")
        .nth(2)
        .boundingBox();
      expect(gridBox).not.toBeNull();
      expect(thirdBox).not.toBeNull();
      expect(
        Math.abs(
          (thirdBox?.x ?? 0) +
            (thirdBox?.width ?? 0) / 2 -
            ((gridBox?.x ?? 0) + (gridBox?.width ?? 0) / 2),
        ),
      ).toBeLessThan(1);
    }
  }
  await expect(page.getByTestId("well-selection")).toContainText(
    "Choose a site for each well",
  );
  await expect(page.getByTestId("open-comparison")).toHaveText(
    "Compare 4 trajectories",
  );
  await page.getByTestId("open-comparison").click();

  await expect(page).toHaveURL(/compareDataset=/);
  expect(
    await page.evaluate(
      () => new URL(window.location.href).searchParams.getAll("compare").length,
    ),
  ).toBe(3);
  expect(
    await page.evaluate(
      () =>
        new URL(window.location.href).searchParams.getAll("compareDataset")
          .length,
    ),
  ).toBe(3);
  await expect(
    page.getByRole("heading", { name: "Compare trajectories" }),
  ).toBeVisible();
  await expect(page.getByTestId("viewer-status")).toHaveText("Images ready", {
    timeout: 60_000,
  });
  const comparison = page.getByTestId("comparison-view");
  await expect(comparison).toHaveAttribute("data-count", "4");
  await expect(comparison.locator(".comparison-view")).toHaveCount(4);
  await expect(comparison.locator("canvas")).toHaveCount(4);
  const repairNote = page.getByTestId("site-position-fix-note");
  await expect(repairNote).toHaveCount(1);
  await expect(repairNote).toHaveText(
    "Site position repaired at timepoint 0: this field holds grid position x = 1 for the whole run.",
  );
  const comparisonHeaderHeights = await comparison
    .locator(".comparison-view > header")
    .evaluateAll((headers) =>
      headers.map((header) => header.getBoundingClientRect().height),
    );
  expect(Math.max(...comparisonHeaderHeights)).toBeLessThanOrEqual(56);
  const comparisonHeaderOffsets = await comparison
    .locator(".comparison-view > header")
    .evaluateAll((headers) =>
      headers.map((header) => {
        const headerBox = header.getBoundingClientRect();
        const badgeBox = header.children[0]?.getBoundingClientRect();
        const factsBox = header.children[1]?.getBoundingClientRect();
        if (!badgeBox || !factsBox) return Number.POSITIVE_INFINITY;
        const contentCenter = (badgeBox.left + factsBox.right) / 2;
        const headerCenter = (headerBox.left + headerBox.right) / 2;
        return Math.abs(contentCenter - headerCenter);
      }),
    );
  expect(Math.max(...comparisonHeaderOffsets)).toBeLessThanOrEqual(1);
  const comparisonSourceFacts = page.getByTestId("viewer-source-facts");
  await expect(comparisonSourceFacts).toHaveCount(4);
  for (const [index, facts] of (
    await comparisonSourceFacts.all()
  ).entries()) {
    await expect(facts).toContainText("Compound");
    await expect(facts).toContainText("DMSO");
    await expect(facts).toContainText("0 µM");
    await expect(facts).toContainText("B01");
    await expect(facts).toContainText("Site");
    await expect(facts).toContainText("Plate");
    await expect(facts).toContainText("S1." + String(index + 1));
    await expect(facts).not.toContainText("|");
  }
  await expect(
    page.getByRole("heading", { name: "Well A details" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Well B details" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Well C details" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Well D details" }),
  ).toBeVisible();
  await expect(page.locator(".viewer-stage")).not.toContainText(".zarr");
  const comparisonMetadata = page.getByTestId("plate-metadata");
  await expect(comparisonMetadata).toHaveCount(4);
  await expect(comparisonMetadata.nth(0).locator("summary")).toHaveText([
    "Biological annotation",
    "Plate and experiment",
    "Acquisition",
  ]);
  await expect(comparisonMetadata.nth(0)).toContainText("S1.1");
  await expect(comparisonMetadata.nth(1)).toContainText("S1.2");
  await expect(comparisonMetadata.nth(2)).toContainText("S1.3");
  await expect(comparisonMetadata.nth(3)).toContainText("S1.4");
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

  const dualViewUrl = await page.evaluate(() => {
    const url = new URL(window.location.href);
    const comparisonPath = url.searchParams.getAll("compare")[0];
    const comparisonDataset = url.searchParams.getAll("compareDataset")[0];
    url.searchParams.delete("compare");
    url.searchParams.delete("compareDataset");
    if (comparisonPath) url.searchParams.append("compare", comparisonPath);
    if (comparisonDataset) {
      url.searchParams.append("compareDataset", comparisonDataset);
    }
    return url.pathname + url.search;
  });
  await page.goto(dualViewUrl);
  await expect(page.getByTestId("viewer-status")).toHaveText("Images ready", {
    timeout: 60_000,
  });
  await expect(page.getByTestId("comparison-view")).toHaveAttribute(
    "data-count",
    "2",
  );
  const dualCanvasBottom = await page
    .locator(".comparison-view .canvas-frame")
    .evaluateAll((canvases) =>
      Math.max(
        ...canvases.map((canvas) => canvas.getBoundingClientRect().bottom),
      ),
    );
  const dualStatusTop = await page
    .locator(".viewer-status-line")
    .evaluate((status) => status.getBoundingClientRect().top);
  expect(dualStatusTop - dualCanvasBottom).toBeLessThanOrEqual(32);

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Disconnect and stop server" })
    .click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
  expect(qcRequests).toEqual([]);
  expect(browserErrors).toEqual([]);
});
