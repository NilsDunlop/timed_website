import { expect, test, type Page, type Route } from "@playwright/test";

const fixturePlates = [14610, 14611, 14613];
const allPlates = Array.from({ length: 9 }, (_, index) => 14610 + index);

function experimentForPlate(plate: number): { experiment: string; cellLine: string } {
  if (plate <= 14612) return { experiment: "idr0080-A549", cellLine: "A549" };
  if (plate <= 14615) return { experiment: "idr0080-ES2", cellLine: "ES2" };
  return { experiment: "idr0080-HCC44", cellLine: "HCC44" };
}

async function expandFieldsToNinePlates(route: Route) {
  const response = await route.fetch();
  const payload = (await response.json()) as {
    row_count: number;
    columns: Record<string, unknown[]>;
    well_metadata?: Record<string, unknown[]> | null;
  };
  const plates = payload.columns.plate.map(Number);
  const source = plates.flatMap((plate, index) => (plate === 14610 ? [index] : []));
  const missing = allPlates.filter((plate) => !fixturePlates.includes(plate));
  for (const plate of missing) {
    const identity = experimentForPlate(plate);
    for (const [column, values] of Object.entries(payload.columns)) {
      for (const rowIndex of source) {
        const value =
          column === "plate"
            ? plate
            : column === "plate_barcode"
              ? `SQ000${plate}`
              : column === "experiment"
                ? identity.experiment
                : column === "cell_line"
                  ? identity.cellLine
                  : values[rowIndex];
        values.push(value);
      }
    }
    for (const values of Object.values(payload.well_metadata ?? {})) {
      for (const rowIndex of source) values.push(values[rowIndex]);
    }
  }
  payload.row_count = payload.columns.plate.length;
  await route.fulfill({
    status: response.status(),
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

async function expandTriageToNinePlates(route: Route) {
  const response = await route.fetch();
  const payload = (await response.json()) as { rows: Record<string, unknown>[] };
  const source = payload.rows.filter((row) => Number(row.plate) === 14610);
  for (const plate of allPlates.filter((candidate) => !fixturePlates.includes(candidate))) {
    payload.rows.push(...source.map((row) => ({ ...row, plate })));
  }
  await route.fulfill({
    status: response.status(),
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

async function connectCellHealth(page: Page) {
  await page.goto("/");
  await page.getByLabel("Compute profile").selectOption("local-datasets");
  await page.getByLabel("Dataset").selectOption("idr0080");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 30_000,
  });
}

test("reviews the unblinded QC overview, maps, filters, and tables", async ({
  page,
}) => {
  const qcRequests: URL[] = [];
  let fieldMetadataCount = 0;
  let forceFieldExportMismatch = false;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/backend/api/qc/")) qcRequests.push(url);
  });

  await connectCellHealth(page);
  await page.route("**/backend/api/qc/fields?*", expandFieldsToNinePlates);
  await page.route("**/backend/api/qc/triage?*", expandTriageToNinePlates);
  await page.route("**/backend/api/qc/field?*", async (route) => {
    const response = await route.fetch();
    const payload = (await response.json()) as {
      export_id: string;
      well_metadata?: Record<string, unknown> | null;
    };
    fieldMetadataCount = Object.keys(payload.well_metadata ?? {}).length;
    if (forceFieldExportMismatch) payload.export_id = `${payload.export_id}-replacement`;
    await route.fulfill({
      status: response.status(),
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
  await page.getByRole("link", { name: "QC", exact: true }).click();

  await expect(
    page.getByRole("heading", { level: 1, name: "Cell Health", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("qc-overview")).toBeVisible();
  await expect(page.locator("[data-qc-plate]")).toHaveCount(9);
  await expect(
    page.locator('[data-qc-plate="14610"] [aria-label^="Well "]'),
  ).toHaveCount(384);
  await expect(page.locator("[data-qc-slider-id]")).toHaveCount(3);
  await expect(
    page.getByText("Registered markers: registered 0.75", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("listitem", { name: "registered: 0.75" })).toBeVisible();
  await expect(page.getByText("Pending review", { exact: true })).toBeVisible();

  const fieldsCard = page.getByTestId("qc-counts").locator("div").filter({
    has: page.getByText("Fields", { exact: true }),
  });
  const fieldCount = fieldsCard.locator("dd");
  await expect(fieldCount).toHaveText("70");
  const siteTable = page.getByRole("table", { name: "QC sites" });
  await expect(siteTable.getByRole("columnheader", { name: /Fixture Context Count/ })).toBeVisible();
  await expect(
    siteTable.getByRole("columnheader", { name: /Perturbation Type/ }),
  ).toBeVisible();
  await expect(
    siteTable.getByRole("columnheader", { name: /Fixture Focus \(Control Z\)/ }),
  ).toBeVisible();
  await expect(
    siteTable.getByRole("columnheader", { name: /Fixture Focus \(Raw\)/ }),
  ).toBeVisible();
  expect(await siteTable.locator("tbody tr").count()).toBeLessThan(70);
  await page.getByTestId("qc-table-scroll").evaluate((element) => {
    element.scrollTop = element.scrollHeight;
    element.dispatchEvent(new Event("scroll", { bubbles: true }));
  });

  const reviewCard = page.locator(
    '[data-qc-slider-id="fixture_focus__DNA__control_z"]',
  );
  await expect(reviewCard.locator(".qc-slider-counts dd")).toHaveText([
    "70",
    "35",
    "14",
    "0",
  ]);

  const reviewBound = page.getByRole("slider", {
    name: "Fixture Focus lower flag bound",
  });
  const reviewBoundValue = page.getByRole("spinbutton", {
    name: "Fixture Focus lower flag bound value",
  });
  await expect(reviewBoundValue).toHaveValue("-2");
  for (const label of ["Fixture Intensity", "Fixture Automatic"]) {
    const lowerRange = page.getByRole("slider", { name: `${label} filter minimum` });
    const upperRange = page.getByRole("slider", { name: `${label} filter maximum` });
    await expect(page.getByRole("spinbutton", { name: `${label} filter minimum value` })).toHaveValue(
      String(await lowerRange.getAttribute("min")),
    );
    await expect(page.getByRole("spinbutton", { name: `${label} filter maximum value` })).toHaveValue(
      String(await upperRange.getAttribute("max")),
    );
  }
  const minimum = Number(await reviewBound.getAttribute("min"));
  const step = Number(await reviewBound.getAttribute("step"));
  await reviewBoundValue.fill(String(minimum));
  await expect(fieldCount).toHaveText("0");
  await reviewBoundValue.fill(String(minimum + step));
  await expect(fieldCount).toHaveText("7");
  await expect(siteTable.getByRole("button", { name: "Open site" }).first()).toBeVisible();
  await expect(reviewCard.locator(".qc-slider-counts dd")).toHaveText([
    "7",
    "7",
    "7",
    "0",
  ]);
  const adjustedReviewBound = await reviewBoundValue.inputValue();
  await expect(
    page
      .locator('[data-qc-plate="14610"]')
      .getByRole("button", { name: "Well A1", exact: true }),
  ).toHaveAttribute("title", /site 1: Fixture Focus/);

  await page.getByRole("button", { name: /Plate 14610, DNA:/ }).click();
  await expect(fieldCount).toHaveText("1");
  await expect(siteTable.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator('[data-qc-plate="14611"]')).toHaveClass(/qc-plate-map-dimmed/);
  await expect(page).toHaveURL(/qc\.plate=14610/);
  await expect(page).toHaveURL(/qc\.channel=DNA/);

  const metricSearch = page.getByRole("searchbox", { name: "Find a metric" });
  await metricSearch.fill("focus");
  await page.reload();
  await expect(page.getByRole("button", { name: /Plate 14610, DNA:/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(fieldCount).toHaveText("1");
  await expect(metricSearch).toHaveValue("focus");
  await expect(reviewBoundValue).toHaveValue(adjustedReviewBound);

  await page.getByRole("button", { name: /Plate 14610, DNA:/ }).click();
  await page.getByRole("button", { name: "Wells", exact: true }).click();
  const wellsTable = page.getByRole("table", { name: "QC wells" });
  await expect(wellsTable).toBeVisible();
  await expect(page).toHaveURL(/qc\.table=wells/);

  const cellLine = page.getByRole("listbox", { name: "Cell Line" });
  await cellLine.selectOption("A549");
  await expect(fieldCount).toHaveText("2");
  await expect(
    page
      .locator('[data-qc-plate="14616"]')
      .getByRole("button", { name: "Well A1", exact: true }),
  ).toHaveClass(/well-dimmed/);
  const plateFilter = page.getByRole("listbox", { name: "Plate" });
  await plateFilter.selectOption(["14610", "14612"]);
  await expect(plateFilter).toHaveValues(["14610", "14612"]);
  await expect(fieldCount).toHaveText("2");
  const wellRows = wellsTable.locator("tbody tr").filter({
    has: page.getByRole("button", { name: /^Open well/ }),
  });
  await expect(wellRows).toHaveCount(2);
  await expect(wellRows.first().locator("td").first()).toHaveText("14,610");
  await wellsTable.getByRole("button", { name: /^Plate/ }).click();
  await expect(
    wellsTable.getByRole("columnheader", {
      name: /Plate/,
    }),
  ).toHaveAttribute("aria-sort", "descending");
  await expect(wellRows.first().locator("td").first()).toHaveText("14,612");
  await expect(page).toHaveURL(/qc\.direction=desc/);
  await page.reload();
  await expect(page.getByRole("table", { name: "QC wells" })).toBeVisible();
  await expect(reviewBoundValue).toHaveValue(adjustedReviewBound);
  await expect(cellLine).toHaveValues(["A549"]);
  await expect(plateFilter).toHaveValues(["14610", "14612"]);
  await expect(
    page.getByRole("table", { name: "QC wells" }).getByRole("columnheader", {
      name: /Plate/,
    }),
  ).toHaveAttribute("aria-sort", "descending");

  expect(qcRequests.length).toBeGreaterThan(0);
  expect(qcRequests.every((url) => !url.searchParams.has("blind"))).toBe(true);

  const qcReturn = `${new URL(page.url()).pathname}${new URL(page.url()).search}`;
  await page
    .locator('[data-qc-plate="14610"]')
    .getByRole("button", { name: "Well A1", exact: true })
    .click();
  await expect(page).toHaveURL(/\/view\/?\?/);
  await expect(page).toHaveURL(/qc=1/);
  await expect(page).toHaveURL(/qcScope=well/);
  await expect(page).toHaveURL(/qcSite=1/);
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
  const qcPanel = page.getByTestId("qc-viewer-panel");
  await expect(qcPanel).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to QC" })).toHaveAttribute(
    "href",
    qcReturn,
  );
  const focusMetric = qcPanel.locator(
    '[data-qc-metric-column="fixture_focus__DNA__control_z"]',
  );
  await expect(focusMetric).toContainText("-2.489");
  await expect(focusMetric).toContainText("Raw 0.81");
  await expect(focusMetric).toContainText("Worse low");
  await expect(focusMetric).toContainText("Is the synthetic DNA focus below the review bound?");
  await expect(qcPanel.getByRole("region", { name: "Site metrics" })).toContainText("0.125");
  await expect(
    qcPanel.locator('[data-qc-context-column="fixture_context_count"]'),
  ).toContainText("21");
  expect(fieldMetadataCount).toBeGreaterThan(0);
  await expect(qcPanel.locator("[data-qc-attribute]")).toHaveCount(fieldMetadataCount);

  const rangeProvenance = page.getByTestId("qc-display-provenance");
  await expect(
    rangeProvenance.getByRole("heading", {
      name: "QC display range · export fixture-qc-primary-20261029",
    }),
  ).toBeVisible();
  await expect(rangeProvenance.locator('[data-qc-range-channel="DNA"]')).toContainText(
    /101.*5,250/,
  );
  await expect(rangeProvenance.locator('[data-qc-range-channel="DNA"]')).toContainText(
    /Quantiles 0\.01.*0\.995.*10 reference fields/,
  );
  for (const [channel, low, high] of [
    ["DNA", "101", "5250"],
    ["ER", "102", "5500"],
    ["AGP", "103", "5750"],
    ["RNA", "104", "6000"],
    ["Mito", "105", "6250"],
  ] as const) {
    await expect(page.getByRole("slider", { name: `${channel} contrast minimum` })).toHaveValue(
      low,
    );
    await expect(page.getByRole("slider", { name: `${channel} contrast maximum` })).toHaveValue(
      high,
    );
    await expect(page.getByRole("slider", { name: `${channel} contrast minimum` })).toBeDisabled();
    await expect(page.getByRole("slider", { name: `${channel} contrast maximum` })).toBeDisabled();
  }

  const rangeToggle = page.getByRole("checkbox", { name: "Use QC display ranges" });
  await rangeToggle.uncheck();
  await expect(page.getByRole("slider", { name: "DNA contrast minimum" })).toHaveValue("292");
  await expect(page.getByRole("slider", { name: "DNA contrast maximum" })).toHaveValue("11552");
  await expect(page.getByRole("slider", { name: "DNA contrast minimum" })).toBeEnabled();
  await rangeToggle.check();
  await expect(page.getByRole("slider", { name: "DNA contrast minimum" })).toHaveValue("101");
  await expect(page.getByRole("slider", { name: "DNA contrast maximum" })).toHaveValue("5250");

  const siteStepper = page.getByTestId("qc-site-stepper");
  await expect(siteStepper).toContainText("Site 1 of 2");
  await expect(siteStepper.getByRole("button", { name: "Previous site" })).toBeDisabled();
  await siteStepper.getByRole("button", { name: "Next site" }).click();
  await expect(page).toHaveURL(/path=A01_s2\.zarr/);
  await expect(page).toHaveURL(/qcSite=2/);
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
  await expect(page.getByTestId("qc-site-stepper")).toContainText("Site 2 of 2");
  await expect(
    page
      .getByTestId("qc-viewer-panel")
      .locator('[data-qc-metric-column="fixture_focus__DNA__control_z"]'),
  ).toContainText("-2.478");
  await page
    .getByTestId("qc-site-stepper")
    .getByRole("button", { name: "Previous site" })
    .click();
  await expect(page).toHaveURL(/path=A01_s1\.zarr/);
  await expect(page).toHaveURL(/qcSite=1/);
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });

  await page.getByRole("link", { name: "Back to QC" }).click();
  await expect(page).toHaveURL(new RegExp(qcReturn.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  await expect(page.getByRole("table", { name: "QC wells" })).toBeVisible();
  await expect(cellLine).toHaveValues(["A549"]);
  await expect(plateFilter).toHaveValues(["14610", "14612"]);

  await page.getByRole("button", { name: "Sites", exact: true }).click();
  const realSiteOpen = page
    .getByRole("table", { name: "QC sites" })
    .locator("tbody tr")
    .filter({ hasText: /^14,610/ })
    .getByRole("button", { name: "Open site" });
  await realSiteOpen.click();
  await expect(page).toHaveURL(/qcScope=field/);
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
  await expect(page.getByTestId("qc-viewer-panel")).toBeVisible();
  await expect(page.getByTestId("qc-site-stepper")).toHaveCount(0);
  await page.getByRole("link", { name: "Back to QC" }).click();

  forceFieldExportMismatch = true;
  await realSiteOpen.click();
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
  await expect(page.getByTestId("qc-viewer-error")).toBeVisible();
  await expect(page.getByTestId("qc-viewer-panel")).toHaveCount(0);
  await expect(page.getByRole("slider", { name: "DNA contrast minimum" })).toHaveValue("292");
  await expect(page.getByRole("slider", { name: "DNA contrast minimum" })).toBeEnabled();
  expect(qcRequests.every((url) => !url.searchParams.has("blind"))).toBe(true);

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page.getByRole("button", { name: "Disconnect and stop server" }).click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
});
