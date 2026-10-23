import { expect, type Locator, type Page } from "@playwright/test";

import { pixelSignature } from "./viewer";

const COLLECTION_TITLE = "idr0080 \u00b7 Cell Health";

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

async function goToCollectionRoot(page: Page) {
  const entry = browserEntry(page, "idr0080-A549");
  const root = page
    .getByRole("navigation", { name: "Data path" })
    .getByRole("button", { name: COLLECTION_TITLE, exact: true });
  await expect(root).toBeVisible();
  if (!(await entry.isVisible())) {
    await expect(root).toBeEnabled();
    await root.click();
  }
  await expect(entry).toBeVisible();
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
      name: "Browse datasets",
      exact: true,
    }),
  ).toBeVisible();

  const datasetSelector = page.getByRole("combobox", { name: "Dataset" });
  await expect(datasetSelector).toHaveValue("idr0080");
  await openExperiment(page, "idr0080-A549");
  await openPlate(page, "SQ00014610");

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
    page.getByRole("heading", { level: 2, name: "SQ00014610", exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Well C1" }).click();
  await goToCollectionRoot(page);
  await openExperiment(page, "idr0080-A549");
  await openPlate(page, "SQ00014611");
  await page.getByRole("button", { name: "Well C1" }).click();

  await goToCollectionRoot(page);
  await openExperiment(page, "idr0080-ES2");
  await openPlate(page, "SQ00014613");
  await page.getByRole("button", { name: "Well C1" }).click();
  await page.getByRole("button", { name: "Well A1" }).click();
  const controlType = page
    .getByTestId("plate-metadata")
    .locator(".well-metadata-grid > div")
    .filter({ has: page.getByText("Type", { exact: true }) })
    .locator("dd");
  await expect(controlType).toHaveText("CRISPR");

  const comparisonTray = page.getByTestId("well-selection");
  await expect(comparisonTray.locator(".comparison-source-card")).toHaveCount(4);
  const compareFields = page.getByTestId("open-comparison");
  await expect(compareFields).toHaveText("Compare 4 fields");
  await expect(compareFields).toBeEnabled();
  await compareFields.click();

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

  await page.getByRole("link", { name: "Back to data" }).click();
  await expect(datasetSelector).toHaveValue("idr0080");
  await expect(
    page.getByRole("heading", { level: 2, name: "SQ00014610", exact: true }),
  ).toBeVisible();
  await removeOpenedPlates(page);
}
