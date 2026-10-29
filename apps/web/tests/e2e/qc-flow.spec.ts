import {
  expect,
  test,
  type Locator,
  type Page,
  type Request,
  type Route,
} from "@playwright/test";

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

async function expectViewerReady(page: Page) {
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
}

async function displayedTarget(row: Locator) {
  const cells = row.locator("td");
  return {
    plate: Number((await cells.nth(0).innerText()).replaceAll(",", "")),
    well: (await cells.nth(1).innerText()).trim(),
    site: Number((await cells.nth(2).innerText()).trim()),
  };
}

function expectCurrentTarget(
  page: Page,
  target: { plate: number; well: string; site: number },
) {
  const query = new URL(page.url()).searchParams;
  expect(query.get("qcPlate")).toBe(String(target.plate));
  expect(query.get("qcWell")).toBe(target.well);
  expect(query.get("qcSite")).toBe(String(target.site));
  expect(query.get("path")).toBe(`${target.well}_s${target.site}.zarr`);
}

test("reviews the unblinded QC overview, maps, filters, and tables", async ({
  page,
}) => {
  const qcRequests: URL[] = [];
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
    };
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
  await expect(page.getByTestId("qc-overview")).toBeHidden();
  await page.getByText("Plate overview", { exact: true }).click();
  await expect(page.getByTestId("qc-overview")).toBeVisible();
  await expect(page.locator("[data-qc-plate]")).toHaveCount(9);
  await expect(
    page.locator('[data-qc-plate="14610"] [aria-label^="Well "]'),
  ).toHaveCount(384);
  await expect(page.getByLabel(/About plate shading:/)).toBeVisible();
  await expect(page.locator("[data-qc-slider-id]")).toHaveCount(1);
  await expect(page.getByText("Pending review", { exact: true })).toBeVisible();
  await expect(page.getByRole("listbox", { name: "Annotation status" })).toHaveCount(0);
  await expect(page.getByRole("listbox", { name: "Well type" })).toHaveCount(0);
  await expect(page.getByText(/under protection/i)).toHaveCount(0);
  await page.getByText("More filters", { exact: true }).click();
  await expect(page.getByRole("listbox", { name: "Annotation status" })).toBeVisible();
  await expect(page.getByRole("listbox", { name: "Control status" })).toBeVisible();

  const fieldsCard = page.getByTestId("qc-counts").locator("div").filter({
    has: page.getByText("Sites", { exact: true }),
  });
  const fieldCount = fieldsCard.locator("dd");
  await expect(fieldCount).toHaveText("70");
  const siteTable = page.getByRole("table", { name: "QC sites" });
  await expect(siteTable.getByRole("columnheader", { name: "Flagged by" })).toBeVisible();
  await expect(siteTable.getByRole("columnheader", { name: /Fixture Context Count/ })).toHaveCount(0);
  await page.getByText("Additional table columns", { exact: true }).click();
  await page
    .getByRole("checkbox", { name: "Show context, attributes, and metric values" })
    .check();
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

  const metricSelect = page.getByRole("combobox", { name: "Metric" });
  await expect(metricSelect).toHaveValue("fixture_focus__DNA__control_z");
  for (const [column, label] of [
    ["fixture_intensity__AGP__raw", "Fixture Intensity"],
    ["fixture_automatic__site__raw", "Fixture Automatic"],
  ] as const) {
    await metricSelect.selectOption(column);
    const lowerRange = page.getByRole("slider", { name: `${label} filter minimum` });
    const upperRange = page.getByRole("slider", { name: `${label} filter maximum` });
    await expect(page.getByRole("spinbutton", { name: `${label} filter minimum value` })).toHaveValue(
      String(await lowerRange.getAttribute("min")),
    );
    await expect(page.getByRole("spinbutton", { name: `${label} filter maximum value` })).toHaveValue(
      String(await upperRange.getAttribute("max")),
    );
    if (column === "fixture_automatic__site__raw") {
      await expect(
        page.getByText("Registered markers: registered 0.75", { exact: true }),
      ).toBeVisible();
      await expect(page.getByRole("listitem", { name: "registered: 0.75" })).toBeVisible();
    }
  }
  await metricSelect.selectOption("fixture_focus__DNA__control_z");

  const reviewCard = page.locator(
    '[data-qc-slider-id="fixture_focus__DNA__control_z"]',
  );
  await expect(reviewCard.locator(".qc-slider-counts dd")).toHaveText([
    "70",
    "35",
    "14",
  ]);

  const reviewBound = page.getByRole("slider", {
    name: "Fixture Focus lower flag bound",
  });
  const reviewBoundValue = page.getByRole("spinbutton", {
    name: "Fixture Focus lower flag bound value",
  });
  await expect(reviewBoundValue).toHaveValue("-2");
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
  ]);
  const adjustedReviewBound = await reviewBoundValue.inputValue();
  const flaggedWell = page
    .locator('[data-qc-plate="14610"]')
    .getByRole("button", { name: "Well A1", exact: true });
  await expect(flaggedWell).toHaveAttribute("title", /site 1: Fixture Focus/);
  await expect(flaggedWell).not.toHaveAttribute("title", /Fixture Intensity/);

  await page.getByRole("button", { name: /Plate 14610, DNA:/ }).click();
  await expect(fieldCount).toHaveText("1");
  await expect(siteTable.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator('[data-qc-plate="14611"]')).toHaveClass(/qc-plate-map-dimmed/);
  await expect(page).toHaveURL(/qc\.plate=14610/);
  await expect(page).toHaveURL(/qc\.channel=DNA/);

  await page.reload();
  await expect(page.getByRole("button", { name: /Plate 14610, DNA:/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(fieldCount).toHaveText("1");
  await expect(metricSelect).toHaveValue("fixture_focus__DNA__control_z");
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
  await expect(focusMetric).toBeVisible();
  await expect(qcPanel.getByRole("heading", { name: "Review metrics" })).toBeVisible();
  await expect(focusMetric).toContainText("-2.489");
  await expect(focusMetric).toContainText("Site value");
  await expect(focusMetric).toContainText("0.81");
  await expect(focusMetric).toContainText("Control Z");
  await expect(
    focusMetric.getByRole("button", {
      name: /Is the synthetic DNA focus below the review bound\?/,
    }),
  ).toBeVisible();
  await expect(qcPanel.getByText(/^Context \(/)).toHaveCount(0);
  await expect(qcPanel.getByText(/^Attributes \(/)).toHaveCount(0);
  await expect(qcPanel.getByText("Image display settings", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Fixed QC display range/)).toBeVisible();
  await expect(page.getByTestId("qc-display-provenance")).toHaveCount(0);
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

  await expect(page.getByRole("checkbox", { name: "Use QC display ranges" })).toHaveCount(0);
  const flaggedSites = page.getByTestId("qc-flagged-sites");
  await expect(flaggedSites).toBeVisible();
  await expect(flaggedSites.getByRole("button", { name: "1", exact: true })).toBeVisible();

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

test("reviews ordered queues and persists field and well annotations", async ({
  page,
}) => {
  const qcRequests: Request[] = [];
  const annotationWrites: Request[] = [];
  let failNextAnnotationRefresh = false;
  await page.route("**/backend/api/qc/annotations*", async (route) => {
    if (route.request().method() === "GET" && failNextAnnotationRefresh) {
      failNextAnnotationRefresh = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Fixture refresh failure" }),
      });
      return;
    }
    await route.continue();
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/backend/api/qc/")) return;
    qcRequests.push(request);
    if (url.pathname.endsWith("/annotations") && request.method() === "POST") {
      annotationWrites.push(request);
    }
  });

  await connectCellHealth(page);
  await page.getByRole("link", { name: "QC", exact: true }).click();
  await expect(page.getByText("Plate overview", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Review queue", exact: true }).click();
  await expectViewerReady(page);
  await expect(page).toHaveURL(/qcQueue=/);
  await expect(page).toHaveURL(/qcIndex=0/);
  await expect(page).toHaveURL(/qcScope=field/);
  expectCurrentTarget(page, { plate: 14610, well: "A01", site: 1 });

  const reviewBar = page.getByTestId("qc-review-bar");
  await expect(reviewBar).toContainText("Review 1 of 45");
  await expect(reviewBar).toContainText("0 of 45 annotated");
  await expect(page.getByTestId("qc-review-reason")).toContainText(/Fixture Focus.*DNA/);
  await expect(reviewBar.getByRole("button", { name: "Previous" })).toBeDisabled();
  await expect(reviewBar.getByRole("link", { name: "Back to QC" })).toHaveCount(0);
  await expect(reviewBar).not.toContainText("Use [ and ]");

  const annotationPanel = page.getByTestId("qc-annotation-panel");
  await expect(
    annotationPanel.getByRole("radio", { name: "Current field" }),
  ).toBeChecked();
  await expect(
    annotationPanel.getByRole("combobox", { name: "Channel", exact: true }),
  ).toHaveValue("DNA");
  const labelSelect = annotationPanel.getByRole("combobox", {
    name: "Label",
    exact: true,
  });
  await expect(labelSelect.getByRole("option", { name: "Bright Biological" })).toHaveCount(1);
  await expect(labelSelect.getByRole("option", { name: "Misregistered" })).toHaveCount(0);
  await labelSelect.selectOption("blurred");
  await expect(annotationPanel).toContainText("The whole field is out of focus.");
  await annotationPanel
    .getByRole("combobox", { name: "Confidence", exact: true })
    .selectOption("high");
  const note = annotationPanel.getByRole("textbox", {
    name: "Note (optional)",
    exact: true,
  });
  await note.fill("Fixture field note ");
  await note.focus();
  await page.keyboard.type("[]");
  await expect(note).toHaveValue("Fixture field note []");
  await expect(page).toHaveURL(/qcIndex=0/);
  failNextAnnotationRefresh = true;
  await annotationPanel.getByRole("button", { name: "Save annotation" }).click();
  await expect(
    annotationPanel.getByText(
      "Annotation saved, but the latest annotation list could not be refreshed.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(annotationPanel.getByTestId("qc-current-annotations")).toContainText(
    "Blurred",
  );
  await expect(reviewBar).toContainText("1 of 45 annotated");

  await page.reload();
  await expectViewerReady(page);
  await expect(page.getByTestId("qc-review-bar")).toContainText("Review 1 of 45");
  await expect(page.getByTestId("qc-review-bar")).toContainText("1 of 45 annotated");
  await expect(page.getByTestId("qc-current-annotations")).toContainText("Blurred");

  await page
    .getByTestId("qc-review-bar")
    .getByRole("button", { name: "Next" })
    .click();
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectViewerReady(page);
  await expect(page).toHaveURL(/qcScope=well/);
  expectCurrentTarget(page, { plate: 14610, well: "A01", site: 1 });
  await expect(page.getByTestId("qc-review-bar")).toContainText("Review 2 of 45");
  await expect(page.getByTestId("qc-site-stepper")).toContainText("Site 1 of 2");
  await expect(
    page.getByText("Well-level review; this queue does not identify individual flagged sites."),
  ).toBeVisible();

  await page
    .getByTestId("qc-review-bar")
    .getByRole("button", { name: "Previous" })
    .click();
  await expect(page).toHaveURL(/qcIndex=0/);
  await expectViewerReady(page);
  await page.locator("body").press("ArrowRight");
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectViewerReady(page);
  await page.locator("body").press("ArrowLeft");
  await expect(page).toHaveURL(/qcIndex=0/);
  await expectViewerReady(page);
  await page
    .getByTestId("qc-review-bar")
    .getByRole("button", { name: "Next" })
    .click();
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectViewerReady(page);

  const wellAnnotationPanel = page.getByTestId("qc-annotation-panel");
  await expect(
    wellAnnotationPanel.getByRole("radio", { name: "Whole well" }),
  ).toBeChecked();
  await wellAnnotationPanel
    .getByRole("combobox", { name: "Channel", exact: true })
    .selectOption("all");
  await wellAnnotationPanel
    .getByRole("combobox", { name: "Label", exact: true })
    .selectOption("debris");
  await wellAnnotationPanel
    .getByRole("combobox", { name: "Confidence", exact: true })
    .selectOption("low");
  await wellAnnotationPanel
    .getByRole("textbox", { name: "Note (optional)", exact: true })
    .fill("Fixture whole-well note");
  await wellAnnotationPanel.getByRole("button", { name: "Save annotation" }).click();
  await expect(
    wellAnnotationPanel.getByText("Annotation saved.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("qc-review-bar")).toContainText("2 of 45 annotated");

  await page.reload();
  await expectViewerReady(page);
  await expect(page.getByTestId("qc-current-annotations")).toContainText("Debris");
  await expect(page.getByTestId("qc-review-bar")).toContainText("2 of 45 annotated");
  await page.getByRole("link", { name: "QC", exact: true }).click();
  await expect(page.getByText("Plate overview", { exact: true })).toBeVisible();

  const fieldsCard = page.getByTestId("qc-counts").locator("div").filter({
    has: page.getByText("Sites", { exact: true }),
  });
  const fieldCount = fieldsCard.locator("dd");
  await page.getByText("More filters", { exact: true }).click();
  const annotationFilter = page.getByRole("listbox", { name: "Annotation status" });
  await annotationFilter.selectOption("blurred");
  await expect(fieldCount).toHaveText("1");
  const annotatedSiteRows = page
    .getByRole("table", { name: "QC sites" })
    .locator("tbody tr")
    .filter({ has: page.getByRole("button", { name: "Open site" }) });
  await expect(annotatedSiteRows).toHaveCount(1);
  await expect(annotatedSiteRows.first()).toContainText("Blurred");
  await annotationFilter.selectOption([]);

  await page.getByRole("button", { name: "Wells", exact: true }).click();
  await annotationFilter.selectOption("debris");
  await expect(fieldCount).toHaveText("2");
  const annotatedWellRows = page
    .getByRole("table", { name: "QC wells" })
    .locator("tbody tr")
    .filter({ has: page.getByRole("button", { name: "Open well" }) });
  await expect(annotatedWellRows).toHaveCount(1);
  await expect(annotatedWellRows.first()).toContainText("Debris");
  await annotationFilter.selectOption([]);
  await page.getByRole("button", { name: "Sites", exact: true }).click();

  const siteTable = page.getByRole("table", { name: "QC sites" });
  const displayedRows = siteTable.locator("tbody tr").filter({
    has: page.getByRole("button", { name: "Open site" }),
  });
  const firstTarget = await displayedTarget(displayedRows.nth(0));
  const secondTarget = await displayedTarget(displayedRows.nth(1));
  const sessionQueueTotal = Number((await fieldCount.innerText()).replaceAll(",", ""));
  expect(sessionQueueTotal).toBeGreaterThan(1);

  await page.getByRole("button", { name: "Review these", exact: true }).click();
  await expectViewerReady(page);
  await expect(page.getByTestId("qc-review-bar")).toContainText(
    `Review 1 of ${sessionQueueTotal}`,
  );
  await expect(page.getByTestId("qc-review-reason")).toContainText(/Fixture Focus.*DNA/);
  expectCurrentTarget(page, firstTarget);
  await page.reload();
  await expectViewerReady(page);
  await expect(page.getByTestId("qc-review-bar")).toContainText(
    `Review 1 of ${sessionQueueTotal}`,
  );
  expectCurrentTarget(page, firstTarget);
  await page
    .getByTestId("qc-review-bar")
    .getByRole("button", { name: "Next" })
    .click();
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectViewerReady(page);
  expectCurrentTarget(page, secondTarget);
  await page.getByRole("link", { name: "QC", exact: true }).click();
  await expect(page.getByText("Plate overview", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Review these", exact: true }).click();
  await expectViewerReady(page);
  await page.evaluate(() => {
    window.sessionStorage.setItem("timed.qc.review-queue.v1", "not-json");
  });
  await page.reload();
  await expectViewerReady(page);
  const unavailableQueue = page.getByTestId("qc-review-bar");
  await expect(unavailableQueue).toContainText("Review 0 of 0");
  await expect(unavailableQueue.getByRole("alert")).toContainText(
    "This review queue is unavailable",
  );
  await page.getByRole("link", { name: "QC", exact: true }).click();
  await expect(page.getByText("Plate overview", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Review queue", exact: true }).click();
  await expectViewerReady(page);
  await expect(page.getByTestId("qc-review-bar")).toContainText("2 of 45 annotated");
  await page
    .getByTestId("qc-current-annotations")
    .getByRole("button", { name: /Retract Blurred annotation/ })
    .click();
  await expect(page.getByTestId("qc-annotation-panel")).toContainText(
    "Annotation retracted.",
  );
  await expect(page.getByTestId("qc-review-bar")).toContainText("1 of 45 annotated");
  await page
    .getByTestId("qc-review-bar")
    .getByRole("button", { name: "Next" })
    .click();
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectViewerReady(page);
  await page
    .getByTestId("qc-current-annotations")
    .getByRole("button", { name: /Retract Debris annotation/ })
    .click();
  await expect(page.getByTestId("qc-annotation-panel")).toContainText(
    "Annotation retracted.",
  );
  await expect(page.getByTestId("qc-review-bar")).toContainText("0 of 45 annotated");
  await page.reload();
  await expectViewerReady(page);
  await expect(page.getByTestId("qc-current-annotations")).toContainText(
    "No current annotations.",
  );

  expect(annotationWrites).toHaveLength(4);
  for (const request of annotationWrites) {
    expect(await request.headerValue("x-timed-client")).toBe("web");
    expect(new URL(request.url()).searchParams.has("blind")).toBe(false);
  }
  expect(qcRequests.length).toBeGreaterThan(0);
  expect(
    qcRequests.every((request) => !new URL(request.url()).searchParams.has("blind")),
  ).toBe(true);

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page.getByRole("button", { name: "Disconnect and stop server" }).click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
});
