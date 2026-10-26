import { expect, test } from "@playwright/test";

const cellHealthPlate = "Cell Health \u00b7 A549 \u00b7 SQ00014610";

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
  await datasetChoice.selectOption("idr0080");
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

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await expect(page.getByLabel("Dataset")).toHaveValue("idr0080");
  await page.getByLabel("Dataset").selectOption("timed");
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

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Disconnect and stop server" })
    .click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
});
