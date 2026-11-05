// Temporary refactor snapshot: exact QC launch, queue and stepper URLs and the
// QC field view state, recorded from unchanged code on 2026-11-05. Dataset and
// queue ids are masked. The last commit of the Gate A refactor deletes this
// folder.
import type { Page } from "@playwright/test";

import { connect, disconnect, expect, test } from "../fixtures";

import {
  Golden,
  IdMask,
  maskedLocation,
  unlinkAllDatasets,
  viewerStateSnapshot,
} from "./snapshots";

async function expectViewerReady(page: Page) {
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
}

async function openQc(page: Page) {
  await page.getByRole("link", { name: "QC", exact: true }).click();
  await expect(page.getByText("Plate overview", { exact: true })).toBeVisible();
}

test("QC links, queue moves and the site stepper keep their exact URLs", async ({
  page,
}) => {
  const golden = new Golden("qc-urls");
  const mask = new IdMask();
  const location = () => maskedLocation(page.url(), mask);
  await connect(page, "local-datasets", { dataset: "idr0080" });
  await unlinkAllDatasets(page);
  await openQc(page);
  golden.record("qc page", location());

  const sites = page.getByRole("table", { name: "QC sites" });
  await sites.getByRole("button", { name: "Open site" }).first().click();
  await expectViewerReady(page);
  golden.record("sites table: open site", location());
  golden.record(
    "sites table: back to QC",
    await page.getByRole("link", { name: "Back to QC" }).getAttribute("href"),
  );
  golden.record("qc field view", await viewerStateSnapshot(page));
  await page.getByRole("link", { name: "Back to QC" }).click();
  await expect(page.getByText("Plate overview", { exact: true })).toBeVisible();

  await page.getByText("Plate overview", { exact: true }).click();
  await page
    .locator('[data-qc-plate="14610"]')
    .getByRole("button", { name: "Well A1", exact: true })
    .click();
  await expectViewerReady(page);
  golden.record("overview: open well", location());
  await page.getByTestId("qc-site-stepper").getByRole("button", { name: "Next site" }).click();
  await expectViewerReady(page);
  golden.record("overview well: next site", location());
  await page.getByRole("link", { name: "Back to QC" }).click();
  await expect(page.getByText("Plate overview", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Wells", exact: true }).click();
  const wells = page.getByRole("table", { name: "QC wells" });
  await expect(wells).toBeVisible();
  golden.record("qc page in wells mode", location());
  await wells.getByRole("button", { name: /^Open well/ }).first().click();
  await expectViewerReady(page);
  golden.record("wells table: open well", location());
  await page.getByRole("link", { name: "Back to QC" }).click();
  await expect(page.getByRole("table", { name: "QC wells" })).toBeVisible();

  const reviewBar = page.getByTestId("qc-review-bar");
  await page.getByRole("button", { name: "Review these", exact: true }).click();
  await expectViewerReady(page);
  golden.record("wells mode: review these", location());
  await reviewBar.getByRole("button", { name: "Next" }).click();
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectViewerReady(page);
  golden.record("wells mode queue: next", location());

  await openQc(page);
  await page.getByRole("button", { name: "Sites", exact: true }).click();
  await expect(page.getByRole("table", { name: "QC sites" })).toBeVisible();
  await page.getByRole("button", { name: "Review these", exact: true }).click();
  await expectViewerReady(page);
  golden.record("sites mode: review these", location());
  await reviewBar.getByRole("button", { name: "Next" }).click();
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectViewerReady(page);
  golden.record("sites mode queue: next", location());

  await openQc(page);
  await page.getByRole("button", { name: "Review queue", exact: true }).click();
  await expectViewerReady(page);
  golden.record("review list: launch", location());
  await reviewBar.getByRole("button", { name: "Next" }).click();
  await expect(page).toHaveURL(/qcIndex=1/);
  await expectViewerReady(page);
  golden.record("review list queue: next", location());

  await disconnect(page);
  golden.check();
});
