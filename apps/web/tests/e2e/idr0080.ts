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

  const grayscale = controls.getByRole("group", { name: "Grayscale" });
  const mitoGrayscale = grayscale.getByRole("checkbox", {
    name: "Show Mito in grayscale",
    exact: true,
  });
  const erGrayscale = grayscale.getByRole("checkbox", {
    name: "Show ER in grayscale",
    exact: true,
  });
  await mitoGrayscale.check();
  await expect(mitoGrayscale).toBeChecked();
  await expect(erGrayscale).not.toBeChecked();
  for (const [name, visible] of channels) {
    const composite = controls.getByRole("checkbox", { name, exact: true });
    await expect(composite).toBeDisabled();
    if (visible) await expect(composite).toBeChecked();
    else await expect(composite).not.toBeChecked();
  }

  const erContrastMaximum = controls.getByRole("slider", {
    name: "ER contrast maximum",
    exact: true,
  });
  await expect(erContrastMaximum).toBeEnabled();
  const originalErContrastMaximum = await erContrastMaximum.inputValue();
  await erContrastMaximum.press("ArrowLeft");
  await expect(erContrastMaximum).not.toHaveValue(originalErContrastMaximum);

  await erGrayscale.check();
  await expect(erGrayscale).toBeChecked();
  await expect(mitoGrayscale).not.toBeChecked();
  await erGrayscale.uncheck();
  await expect(grayscale.locator('input[type="checkbox"]:checked')).toHaveCount(
    0,
  );
  for (const [name, visible] of channels) {
    const composite = controls.getByRole("checkbox", { name, exact: true });
    await expect(composite).toBeEnabled();
    if (visible) await expect(composite).toBeChecked();
    else await expect(composite).not.toBeChecked();
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
    page.getByRole("heading", { level: 1, name: "Cell Health", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: primaryPlateName,
      exact: true,
    }),
  ).toBeVisible();

  const matchPanel = page.getByTestId("perturbation-matches");
  await expect(matchPanel).toBeVisible();

  await matchPanel
    .getByRole("button", { name: "Same gene", exact: true })
    .click();
  const matchResults = matchPanel.getByTestId("perturbation-match-results");
  await expect(matchResults.getByTestId("perturbation-match-row")).toHaveCount(5);
  await expect(matchResults.locator('[data-cell-line="A549"]')).toBeVisible();
  await expect(matchResults.locator('[data-cell-line="ES2"]')).toBeVisible();
  await expect(matchResults).toContainText("KRAS-2");
  await expect(
    matchResults
      .getByTestId("perturbation-match-row")
      .filter({ hasText: "SQ00014610" })
      .filter({ hasText: "Well C01" }),
  ).toHaveCount(0);

  await matchPanel
    .getByRole("button", { name: "Controls on this plate", exact: true })
    .click();
  await expect(matchResults.getByTestId("perturbation-match-row")).toHaveCount(1);
  await expect(matchResults).toContainText("SQ00014610");
  await expect(matchResults).toContainText("Well A01");
  await expect(matchResults).toContainText("Guide EMPTY");
  await expect(matchResults).toContainText("EMPTY control");

  await matchPanel
    .getByRole("button", { name: "Same well on other plates", exact: true })
    .click();
  await expect(matchResults.getByTestId("perturbation-match-row")).toHaveCount(2);
  for (const plate of ["SQ00014611", "SQ00014613"]) {
    await matchPanel
      .getByRole("button", {
        name: `Add ${plate} C01 to comparison`,
        exact: true,
      })
      .click();
    await expect(
      matchPanel.getByRole("button", {
        name: `${plate} C01 added`,
        exact: true,
      }),
    ).toBeDisabled();
  }

  const comparisonTray = page.getByTestId("well-selection");
  await expect(comparisonTray.locator(".comparison-source-card")).toHaveCount(3);
  const secondPlateSite = page.getByLabel(
    "Site for Cell Health \u00b7 A549 \u00b7 SQ00014611 C01",
  );
  await expect(secondPlateSite).toHaveValue("C01_s1.zarr");
  await secondPlateSite.selectOption("C01_s2.zarr");
  await expect(secondPlateSite).toHaveValue("C01_s2.zarr");

  await matchPanel
    .getByRole("button", { name: "Controls on this plate", exact: true })
    .click();
  await matchPanel
    .getByRole("button", {
      name: "Add SQ00014610 A01 to comparison",
      exact: true,
    })
    .click();
  await expect(comparisonTray.locator(".comparison-source-card")).toHaveCount(4);
  await expect(page.locator(".dataset-list li")).toHaveCount(3);

  await matchPanel
    .getByRole("button", { name: "Same gene", exact: true })
    .click();
  await expect(matchPanel.getByTestId("perturbation-match-limit")).toBeVisible();
  const unaddedGene = matchResults
    .getByTestId("perturbation-match-row")
    .filter({ hasText: "SQ00014610" })
    .filter({ hasText: "Well P24" });
  await expect(
    unaddedGene.getByRole("button", { name: /Add .* to comparison/ }),
  ).toBeDisabled();

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
  const comparisonGrayscale = page
    .locator("#image-controls")
    .getByRole("group", { name: "Grayscale" });
  await comparisonGrayscale
    .getByRole("checkbox", {
      name: "Show DNA in grayscale",
      exact: true,
    })
    .check();
  await expect(page.getByTestId("viewer-status")).toHaveText("Images ready");
  for (let index = 0; index < 4; index += 1) {
    await expect(comparisonCanvases.nth(index)).toBeVisible();
  }

  await page.getByRole("link", { name: "Back to data" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Cell Health", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Dataset" })).toHaveCount(0);
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: primaryPlateName,
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Well A1" }).click();
  const controlType = page
    .getByTestId("plate-metadata")
    .locator(".well-metadata-grid > div")
    .filter({ has: page.getByText("Type", { exact: true }) })
    .locator("dd");
  await expect(controlType).toHaveText("CRISPR");
  await removeOpenedPlates(page);
}
