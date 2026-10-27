import { expect, type Locator, type Page } from "@playwright/test";

import { pixelSignature } from "./viewer";

function browserEntry(page: Page, name: string): Locator {
  return page.locator(".file-list li").filter({
    has: page.getByText(name, { exact: true }),
  });
}

async function openExperiment(page: Page, experiment: string) {
  const entry = browserEntry(page, experiment);
  await expect(entry).toBeVisible();
  await entry.getByRole("button", { name: "Open folder" }).click();
}

async function openPlate(page: Page, plate: string) {
  const entry = browserEntry(page, plate);
  await expect(entry).toBeVisible();
  await entry.getByRole("button", { name: "Open", exact: true }).click();
  await expect(
    page.getByRole("heading", { level: 2, name: plate, exact: true }),
  ).toBeVisible();
}

async function expectCustomProperty(
  locator: Locator,
  property: string,
  value: string,
) {
  await expect
    .poll(() =>
      locator.evaluate(
        (element, propertyName) =>
          (element as HTMLElement).style
            .getPropertyValue(propertyName)
            .trim(),
        property,
      ),
    )
    .toBe(value);
}

async function expectLabelTextInsideSegment(radio: Locator) {
  const segment = radio.locator("xpath=ancestor::label");
  const labelText = segment.locator("span");
  const [segmentBox, labelTextBox] = await Promise.all([
    segment.boundingBox(),
    labelText.boundingBox(),
  ]);
  expect(segmentBox).not.toBeNull();
  expect(labelTextBox).not.toBeNull();
  if (segmentBox == null || labelTextBox == null) return;

  const roundingTolerance = 0.5;
  expect(labelTextBox.x).toBeGreaterThanOrEqual(
    segmentBox.x - roundingTolerance,
  );
  expect(labelTextBox.y).toBeGreaterThanOrEqual(
    segmentBox.y - roundingTolerance,
  );
  expect(labelTextBox.x + labelTextBox.width).toBeLessThanOrEqual(
    segmentBox.x + segmentBox.width + roundingTolerance,
  );
  expect(labelTextBox.y + labelTextBox.height).toBeLessThanOrEqual(
    segmentBox.y + segmentBox.height + roundingTolerance,
  );
}

async function removeOpenedPlates(page: Page) {
  const opened = page.locator(".dataset-list li");
  while ((await opened.count()) > 0) {
    const count = await opened.count();
    await opened
      .first()
      .getByRole("button", { name: "Remove", exact: true })
      .click();
    await expect(opened).toHaveCount(count - 1);
  }
}

export async function exerciseIdr0080Flow(page: Page) {
  await page.goto("/data?collection=idr0080");
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Cell Health",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Dataset" })).toHaveCount(0);
  await expect(page.getByRole("option", { name: "Files" })).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Change dataset", exact: true }),
  ).toBeVisible();

  const primaryPlateName = "Cell Health \u00b7 A549 \u00b7 SQ00014610";
  await openExperiment(page, "A549");
  await openPlate(page, primaryPlateName);

  const geneFilter = page.getByTestId("compound-filter");
  const wellTypeFilter = page.getByTestId("moa-filter");
  const controlFilter = page.getByTestId("well-kind-filter");
  await expect(geneFilter.locator("xpath=ancestor::label")).toContainText(
    "Gene",
  );
  await expect(geneFilter).toContainText("All genes");
  await expect(wellTypeFilter.locator("xpath=ancestor::label")).toContainText(
    "Well type",
  );
  await expect(wellTypeFilter).toContainText("All well types");
  await expect(controlFilter.locator("xpath=ancestor::label")).toContainText(
    "EMPTY control",
  );

  const legend = page.getByLabel("Well color legend");
  for (const category of [
    "Query guides",
    "Cutting controls",
    "Non-cutting controls",
    "EMPTY control",
  ]) {
    await expect(legend).toContainText(category);
  }

  const cuttingControl = page.getByRole("button", { name: "Well A2" });
  const nonCuttingControl = page.getByRole("button", { name: "Well B1" });
  const kras = page.getByRole("button", { name: "Well C1" });
  const secondKras = page.getByRole("button", { name: "Well P24" });
  await expectCustomProperty(cuttingControl, "--well-color", "#b86b35");
  await expectCustomProperty(nonCuttingControl, "--well-color", "#9367a8");
  await expectCustomProperty(kras, "--well-color", "#3b82a0");
  await expectCustomProperty(secondKras, "--well-color", "#3b82a0");

  await geneFilter.selectOption("KRAS");
  await expect(kras).toHaveClass(/well-highlighted/);
  await expect(secondKras).toHaveClass(/well-highlighted/);
  await expect(cuttingControl).toHaveClass(/well-dimmed/);
  await geneFilter.selectOption("");

  await kras.click();
  const browserMetadata = page.getByTestId("plate-metadata");
  await expect(browserMetadata).toContainText("Plate and well information");
  await expect(browserMetadata).toContainText("Gene");
  await expect(browserMetadata).toContainText("KRAS");
  await expect(browserMetadata).not.toContainText("Not specified");

  const biological = browserMetadata
    .locator("details")
    .filter({ hasText: "Biological annotation" });
  await biological.locator("summary").click();
  for (const value of [
    "Well type",
    "Query guides",
    "Guide",
    "KRAS-1",
    "Ensembl ID",
    "ENSG00000133703",
    "Control category",
    "targeting",
  ]) {
    await expect(biological).toContainText(value);
  }

  const acquisition = browserMetadata
    .locator("details")
    .filter({ hasText: "Acquisition" });
  await acquisition.locator("summary").click();
  await expect(acquisition.getByText("1 timepoint", { exact: true })).toBeVisible();

  const openField = page.getByTestId("open-selected");
  await expect(openField).toHaveText("Open field");
  await openField.click();
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });

  const controls = page.locator("#image-controls");
  await expect(
    controls.getByRole("heading", { level: 2, name: "Channels", exact: true }),
  ).toBeVisible();
  await expect(controls.getByText("Time and channels", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByTestId("time-slider")).toHaveCount(0);
  await expect(page.getByTestId("time-jump")).toHaveCount(0);
  await expect(page.getByTestId("displayed-time")).toHaveCount(0);

  const channels = [
    ["DNA", true, "292", "11552"],
    ["ER", true, "438", "17554"],
    ["RNA", false, "554", "9283"],
    ["AGP", true, "151", "5035"],
    ["Mito", false, "324", "4066"],
  ] as const;
  for (const [name, visible, start, end] of channels) {
    const checkbox = page.getByRole("checkbox", { name, exact: true });
    const channelControl = page.locator(".channel-control").filter({
      has: checkbox,
    });
    if (visible) await expect(checkbox).toBeChecked();
    else await expect(checkbox).not.toBeChecked();
    await expect(channelControl).toContainText(`Contrast minimum: ${start}`);
    await expect(channelControl).toContainText(`Contrast maximum: ${end}`);
  }

  await expect(page.locator(".canvas-frame").first()).toHaveAttribute(
    "data-scale-bar",
    "true",
  );
  const originalViewport = page.viewportSize();
  await page.setViewportSize({
    width: 320,
    height: originalViewport?.height ?? 720,
  });
  const controlsBox = await controls.boundingBox();
  expect(controlsBox).not.toBeNull();
  expect(controlsBox?.width).toBeGreaterThanOrEqual(279);
  expect(controlsBox?.width).toBeLessThanOrEqual(281);

  const displayGroup = controls.getByRole("radiogroup", {
    name: "Display",
    exact: true,
  });
  await expect(displayGroup).toBeVisible();
  await expect(displayGroup.getByRole("radio")).toHaveCount(2);
  const colorMode = displayGroup.getByRole("radio", {
    name: "Color",
    exact: true,
  });
  const grayscaleMode = displayGroup.getByRole("radio", {
    name: "Grayscale",
    exact: true,
  });
  await expect(colorMode).toBeChecked();
  await expect(grayscaleMode).not.toBeChecked();
  await expectLabelTextInsideSegment(colorMode);
  await expectLabelTextInsideSegment(grayscaleMode);

  await colorMode.focus();
  await expect(colorMode).toBeFocused();
  await colorMode.press("ArrowRight");
  await expect(grayscaleMode).toBeFocused();
  await expect(grayscaleMode).toBeChecked();
  await expect(colorMode).not.toBeChecked();
  const focusedSegment = grayscaleMode.locator("xpath=ancestor::label");
  const focusOutline = await focusedSegment.evaluate((element) => {
    const style = window.getComputedStyle(element);
    return {
      style: style.outlineStyle,
      width: Number.parseFloat(style.outlineWidth),
    };
  });
  expect(focusOutline.style).toBe("solid");
  expect(focusOutline.width).toBeGreaterThan(0);
  if (originalViewport != null) await page.setViewportSize(originalViewport);

  const grayscaleChannels = controls.getByRole("group", {
    name: "Grayscale channel",
  });
  const mitoGrayscale = grayscaleChannels.getByRole("radio", {
    name: "Mito",
    exact: true,
  });
  const erGrayscale = grayscaleChannels.getByRole("radio", {
    name: "ER",
    exact: true,
  });
  await mitoGrayscale.check();
  await expect(mitoGrayscale).toBeChecked();
  await expect(erGrayscale).not.toBeChecked();
  await expect(controls.getByRole("slider")).toHaveCount(2);
  await expect(
    controls.getByRole("slider", {
      name: "DNA contrast maximum",
      exact: true,
    }),
  ).toHaveCount(0);

  await erGrayscale.check();
  const erContrastMaximum = controls.getByRole("slider", {
    name: "ER contrast maximum",
    exact: true,
  });
  await expect(erContrastMaximum).toBeEnabled();
  const originalErContrastMaximum = await erContrastMaximum.inputValue();
  await erContrastMaximum.press("ArrowLeft");
  await expect(erContrastMaximum).not.toHaveValue(originalErContrastMaximum);
  const editedErContrastMaximum = await erContrastMaximum.inputValue();

  await expect(erGrayscale).toBeChecked();
  await expect(mitoGrayscale).not.toBeChecked();
  await colorMode.check();
  await expect(grayscaleChannels).toHaveCount(0);
  await expect(controls.getByRole("slider")).toHaveCount(10);
  await expect(erContrastMaximum).toHaveValue(editedErContrastMaximum);
  for (const [name, visible] of channels) {
    const composite = controls.getByRole("checkbox", { name, exact: true });
    if (visible) await expect(composite).toBeChecked();
    else await expect(composite).not.toBeChecked();
  }
  await grayscaleMode.check();
  await expect(
    controls
      .getByRole("group", { name: "Grayscale channel" })
      .getByRole("radio", { name: "ER", exact: true }),
  ).toBeChecked();
  await expect(erContrastMaximum).toHaveValue(editedErContrastMaximum);

  const sourceFacts = page.getByTestId("viewer-source-facts");
  await expect(sourceFacts).toContainText("Gene");
  await expect(sourceFacts).toContainText("KRAS");
  await expect(sourceFacts).not.toContainText("Compound");
  await expect(sourceFacts).not.toContainText("Not specified");

  const viewerMetadata = page.getByTestId("plate-metadata");
  await expect(viewerMetadata).not.toContainText("Not specified");
  const viewerBiological = viewerMetadata
    .locator("details")
    .filter({ hasText: "Biological annotation" });
  await viewerBiological.locator("summary").click();
  await expect(viewerBiological).toContainText("KRAS-1");
  await expect(viewerBiological).toContainText("ENSG00000133703");
  await expect(viewerBiological).toContainText("targeting");

  const canvas = page.locator(".canvas-frame canvas").first();
  await expect(canvas).toBeVisible();
  expect((await pixelSignature(page, canvas)).range).toBeGreaterThan(5);

  await page.getByRole("link", { name: "Back to data" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Cell Health", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: primaryPlateName,
      exact: true,
    }),
  ).toBeVisible();

  await expect(page.getByText("Related fields", { exact: true })).toHaveCount(0);
  const highlightControls = page.getByTestId("well-highlight-controls");
  const sameGene = highlightControls.getByRole("button", {
    name: "Same gene",
    exact: true,
  });
  const controlHighlights = highlightControls.getByRole("button", {
    name: "Controls",
    exact: true,
  });
  const emptyControl = page.getByRole("button", { name: "Well A1" });

  await sameGene.click();
  await expect(sameGene).toHaveAttribute("aria-pressed", "true");
  await expect(controlHighlights).toHaveAttribute("aria-pressed", "false");
  await expect(kras).toHaveClass(/well-highlighted/);
  await expect(secondKras).toHaveClass(/well-highlighted/);
  await expect(cuttingControl).toHaveClass(/well-dimmed/);

  await sameGene.click();
  await expect(sameGene).toHaveAttribute("aria-pressed", "false");
  await expect(kras).not.toHaveClass(/well-highlighted/);
  await expect(cuttingControl).not.toHaveClass(/well-dimmed/);

  await controlHighlights.click();
  await expect(controlHighlights).toHaveAttribute("aria-pressed", "true");
  await expect(emptyControl).toHaveClass(/well-highlighted/);
  await expect(kras).toHaveClass(/well-dimmed/);

  await sameGene.click();
  await expect(sameGene).toHaveAttribute("aria-pressed", "true");
  await expect(controlHighlights).toHaveAttribute("aria-pressed", "false");
  await expect(kras).toHaveClass(/well-highlighted/);
  await expect(secondKras).toHaveClass(/well-highlighted/);

  const secondPlateName = "Cell Health \u00b7 A549 \u00b7 SQ00014611";
  await openExperiment(page, "A549");
  await openPlate(page, secondPlateName);
  await expect(page.getByRole("button", { name: "Well C1" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByRole("button", { name: "Well P24" })).toHaveClass(
    /well-highlighted/,
  );

  const comparisonTray = page.getByTestId("well-selection");
  await expect(comparisonTray.locator(".comparison-source-card")).toHaveCount(2);
  const secondPlateSite = page.getByLabel(
    "Site for Cell Health \u00b7 A549 \u00b7 SQ00014611 C01",
  );
  await expect(secondPlateSite).toHaveValue("C01_s1.zarr");
  await secondPlateSite.selectOption("C01_s2.zarr");
  await expect(secondPlateSite).toHaveValue("C01_s2.zarr");

  const dataPath = page.getByRole("navigation", { name: "Data path" });
  await dataPath
    .getByRole("button", { name: "Cell Health", exact: true })
    .click();
  const thirdPlateName = "Cell Health \u00b7 ES2 \u00b7 SQ00014613";
  await openExperiment(page, "ES2");
  await openPlate(page, thirdPlateName);
  await expect(page.getByRole("button", { name: "Well C1" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(comparisonTray.locator(".comparison-source-card")).toHaveCount(3);

  await page.getByRole("button", { name: "Well A1" }).click();
  await expect(comparisonTray.locator(".comparison-source-card")).toHaveCount(4);
  await expect(page.locator(".dataset-list li")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "Well P24" })).toBeDisabled();

  const compareFields = page.getByTestId("open-comparison");
  await expect(compareFields).toHaveText("Compare 4 fields");
  await expect(compareFields).toBeEnabled();
  await compareFields.click();
  await expect(page).toHaveURL(/\/view\/?\?/);
  const viewerUrl = new URL(page.url());
  const comparisonDatasets = viewerUrl.searchParams.getAll("compareDataset");
  const comparisonFields = viewerUrl.searchParams.getAll("compare");
  expect(comparisonDatasets).toHaveLength(3);
  expect(comparisonFields).toHaveLength(3);
  expect(comparisonFields[0]).toBe("C01_s2.zarr");
  expect(viewerUrl.searchParams.get("returnDataset")).toBe(
    comparisonDatasets[2],
  );

  await expect(page.getByTestId("viewer-status")).toHaveText("Images ready", {
    timeout: 60_000,
  });
  await expect(page.getByTestId("comparison-view")).toHaveAttribute(
    "data-count",
    "4",
  );
  await expect(page.getByTestId("comparison-view").locator("canvas")).toHaveCount(
    4,
  );
  await expect(page.getByTestId("viewer-source-facts")).toHaveCount(4);
  const comparisonCanvases = page
    .getByTestId("comparison-view")
    .locator("canvas");
  const comparisonControls = page.locator("#image-controls");
  await expect(
    comparisonControls.getByRole("radio", {
      name: "Grayscale",
      exact: true,
    }),
  ).toBeChecked();
  const comparisonGrayscale = comparisonControls.getByRole("group", {
    name: "Grayscale channel",
  });
  await expect(
    comparisonGrayscale.getByRole("radio", { name: "ER", exact: true }),
  ).toBeChecked();
  await expect(comparisonControls.getByRole("slider")).toHaveCount(2);
  await comparisonGrayscale
    .getByRole("radio", { name: "DNA", exact: true })
    .check();
  await expect(page.getByTestId("viewer-status")).toHaveText("Images ready");
  const comparisonFrames = page
    .getByTestId("comparison-view")
    .locator(".canvas-frame");
  for (let index = 0; index < 4; index += 1) {
    await expect(comparisonCanvases.nth(index)).toBeVisible();
    await expect(comparisonFrames.nth(index)).toHaveAttribute(
      "data-scale-bar",
      "true",
    );
  }
  await comparisonControls
    .getByRole("radio", { name: "Color", exact: true })
    .check();
  for (const [name, visible] of channels) {
    const composite = comparisonControls.getByRole("checkbox", {
      name,
      exact: true,
    });
    if (visible) await expect(composite).toBeChecked();
    else await expect(composite).not.toBeChecked();
  }

  await page.getByRole("link", { name: "Back to data" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Cell Health", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Dataset" })).toHaveCount(0);
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: thirdPlateName,
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Well A1" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const controlType = page
    .getByTestId("plate-metadata")
    .locator(".well-metadata-grid > div")
    .filter({ has: page.getByText("Type", { exact: true }) })
    .locator("dd");
  await expect(controlType).toHaveText("CRISPR");
  await removeOpenedPlates(page);
}
