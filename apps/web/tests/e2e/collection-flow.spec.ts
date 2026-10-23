import { expect, test } from "@playwright/test";

test("scopes collection browsing and restores an opened plate", async ({ page }) => {
  await page.goto("/");
  await page
    .getByLabel("Compute profile")
    .selectOption("local-datasets");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 30_000,
  });

  await page.goto("/data?collection=idr0080");
  const datasetSelector = page.getByRole("combobox", { name: "Dataset" });
  await expect(datasetSelector).toHaveValue("idr0080");
  await expect(page).toHaveURL(/\/data\?collection=idr0080$/);

  await page.goto("/data?collection=missing");
  await expect(datasetSelector).toHaveValue("timed");
  await expect(datasetSelector.locator("option")).toHaveText([
    "TIMED",
    /idr0080/,
    "Files",
  ]);
  await expect(page).toHaveURL(/\/data\?collection=timed$/);
  await expect(page.getByText(/Live-cell time-lapse/)).toBeVisible();
  await expect(page.getByRole("button", { name: "TIMED" })).toBeDisabled();
  await expect(page.locator(".file-list")).not.toContainText(".zarr");

  const plate = page.locator(".file-list li").filter({
    has: page.getByText("test_plate", { exact: true }),
  });
  await expect(plate).toContainText("OME-Zarr plate");
  await plate.getByRole("button", { name: "Open" }).click();
  await expect(page.getByRole("heading", { name: "test_plate" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Opened plates" })).toBeVisible();
  await expect(page.locator(".dataset-list li")).toHaveCount(1);
  await plate.getByRole("button", { name: "Open" }).click();
  await expect(page.locator(".dataset-list li")).toHaveCount(1);

  await page.getByRole("button", { name: "Well A1" }).click();
  const tray = page.getByTestId("well-selection");
  await expect(tray).toContainText("test_plate.zarr");

  await datasetSelector.selectOption("idr0080");
  await expect(page).toHaveURL(/\/data\?collection=idr0080$/);
  await expect(page.getByText(/CRISPR Cell Painting/)).toBeVisible();
  await expect(tray).toContainText("test_plate.zarr");
  await expect(page.getByRole("heading", { name: "Opened plates" })).toBeVisible();
  await expect(page.locator(".dataset-list li")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "test_plate", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".file-list li")).toHaveCount(31);
  await expect(page.locator(".file-list")).not.toContainText("plain-file.txt");
  const listingFilter = page.getByLabel("Filter this dataset");
  await expect(listingFilter).toBeVisible();
  await listingFilter.fill("31");
  await expect(page.locator(".file-list li")).toHaveCount(1);
  await expect(page.locator(".file-list")).toContainText("group-31");

  await datasetSelector.selectOption("__files__");
  await expect(page).toHaveURL(/\/data$/);
  await expect(page.getByRole("heading", { name: "Browse data roots" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Linked datasets" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Unlink" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "test_plate.zarr", exact: true }),
  ).toBeVisible();
  await expect(tray).toContainText("test_plate.zarr");

  await datasetSelector.selectOption("timed");
  await page.getByRole("button", { name: "test_plate", exact: true }).click();
  const openSelected = page.getByTestId("open-selected");
  await expect(openSelected).toHaveAttribute("href", /collection=timed/);
  await openSelected.click();
  await expect(page).toHaveURL(/\/view\/?\?.*collection=timed/);
  const backLink = page.getByRole("link", { name: "Back to data" });
  await expect(backLink).toHaveAttribute(
    "href",
    /\/data\?collection=timed&dataset=/,
  );
  await backLink.click();
  await expect(datasetSelector).toHaveValue("timed");
  await expect(page.getByRole("heading", { name: "test_plate" })).toBeVisible();
  await expect(page).toHaveURL(/\/data\?collection=timed&dataset=/);

  await page.locator(".dataset-list li").getByRole("button", {
    name: "Remove",
  }).click();
  await expect(page.locator(".dataset-list li")).toHaveCount(0);

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Disconnect and stop server" })
    .click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
});
