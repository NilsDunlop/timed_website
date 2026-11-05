import { connect, disconnect, expect, test } from "./fixtures";
import { exerciseIdr0080Flow } from "./idr0080";

const cellHealthPlate = "Cell Health \u00b7 A549 \u00b7 SQ00014610";
const secondCellHealthPlate = "Cell Health \u00b7 A549 \u00b7 SQ00014611";

async function expectRestoredPlateAndWell(page: import("@playwright/test").Page) {
  await expect(
    page.getByRole("heading", { level: 1, name: "Cell Health", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 2, name: cellHealthPlate, exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Well A1" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByTestId("well-selection")).toContainText("A01");
}

test("chooses a dataset on Connect and restores a plate and well", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByLabel("Compute profile")
    .selectOption("local-datasets");

  const datasetChoice = page.getByLabel("Dataset");
  await expect(datasetChoice).toHaveValue("timed");
  await expect(
    page.getByRole("link", { name: "QC", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Compute profile").selectOption("local");
  await expect(page.getByRole("link", { name: "QC", exact: true })).toHaveCount(0);
  await page.getByLabel("Compute profile").selectOption("local-datasets");
  await datasetChoice.selectOption("idr0080");
  await expect(page.getByRole("link", { name: "QC", exact: true })).toBeVisible();
  await expect(page.getByText(/idr0080-like CRISPR Cell Painting/)).toBeVisible();

  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 30_000,
  });
  await page.getByRole("link", { name: "Data", exact: true }).click();

  await expect(
    page.getByRole("heading", { level: 1, name: "Cell Health", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Dataset" })).toHaveCount(0);
  await expect(page.getByRole("option", { name: "Files" })).toHaveCount(0);

  const a549 = page.locator(".file-list li").filter({
    has: page.getByText("A549", { exact: true }),
  });
  await expect(a549).toContainText("2 plates");
  await a549.getByRole("button", { name: "Open folder" }).click();

  const packedPlate = page.locator(".file-list li").filter({
    has: page.getByText(cellHealthPlate, { exact: true }),
  });
  await expect(packedPlate).toContainText("5 wells");
  await packedPlate.getByRole("button", { name: "Open", exact: true }).click();
  await expect(
    page.getByRole("heading", { level: 2, name: cellHealthPlate, exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Well A1" }).click();
  await page.getByTestId("open-selected").click();
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await expectRestoredPlateAndWell(page);

  await page.reload();
  await expectRestoredPlateAndWell(page);

  const kras = page.getByRole("button", { name: "Well C1" });
  const secondKras = page.getByRole("button", { name: "Well P24" });
  const cuttingControl = page.getByRole("button", { name: "Well A2" });
  const emptyControl = page.getByRole("button", { name: "Well A1" });
  await kras.click();

  const highlights = page.getByTestId("well-highlight-controls");
  const sameGene = highlights.getByRole("button", {
    name: "Same gene",
    exact: true,
  });
  const controls = highlights.getByRole("button", {
    name: "Controls",
    exact: true,
  });
  await sameGene.click();
  await expect(sameGene).toHaveAttribute("aria-pressed", "true");
  await expect(kras).toHaveClass(/well-highlighted/);
  await expect(secondKras).toHaveClass(/well-highlighted/);
  await expect(cuttingControl).toHaveClass(/well-dimmed/);

  await controls.click();
  await expect(sameGene).toHaveAttribute("aria-pressed", "false");
  await expect(controls).toHaveAttribute("aria-pressed", "true");
  await expect(emptyControl).toHaveClass(/well-highlighted/);
  await expect(kras).toHaveClass(/well-dimmed/);
  await controls.click();
  await expect(controls).toHaveAttribute("aria-pressed", "false");
  await expect(kras).not.toHaveClass(/well-dimmed/);

  const a549Again = page.locator(".file-list li").filter({
    has: page.getByText("A549", { exact: true }),
  });
  await a549Again.getByRole("button", { name: "Open folder" }).click();
  const secondPlate = page.locator(".file-list li").filter({
    has: page.getByText(secondCellHealthPlate, { exact: true }),
  });
  await secondPlate.getByRole("button", { name: "Open", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: secondCellHealthPlate,
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Well C1" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    page.getByTestId("well-selection").locator(".comparison-source-card"),
  ).toHaveCount(3);

  const firstOpenedPlate = page.locator(".dataset-list li").filter({
    has: page.getByRole("button", { name: cellHealthPlate, exact: true }),
  });
  await firstOpenedPlate
    .getByRole("button", { name: cellHealthPlate, exact: true })
    .click();
  await expect(
    page.getByRole("heading", { level: 2, name: cellHealthPlate, exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Well C1" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  const secondOpenedPlate = page.locator(".dataset-list li").filter({
    has: page.getByRole("button", { name: secondCellHealthPlate, exact: true }),
  });
  await secondOpenedPlate
    .getByRole("button", { name: "Remove", exact: true })
    .click();
  await page.getByRole("button", { name: "Well C1" }).click();
  await expect(page.getByRole("button", { name: "Well A1" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await expect(page.getByLabel("Dataset")).toHaveValue("idr0080");
  await page.getByLabel("Dataset").selectOption("timed");
  await expect(
    page.getByRole("link", { name: "QC", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "Data", exact: true }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "TIMED", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Dataset" })).toHaveCount(0);

  await page.goto("/data?collection=idr0080");
  await expectRestoredPlateAndWell(page);
  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await expect(page.getByLabel("Dataset")).toHaveValue("idr0080");

  await page.getByRole("link", { name: "Data", exact: true }).click();
  const openedPlate = page.locator(".dataset-list li").filter({
    has: page.getByRole("button", { name: cellHealthPlate, exact: true }),
  });
  await openedPlate.getByRole("button", { name: "Remove", exact: true }).click();

  await disconnect(page);
});


test("views Cell Health fields, display modes and comparisons locally", async ({ page }) => {
  await connect(page, "local-datasets");
  await exerciseIdr0080Flow(page);
  await disconnect(page);
});
