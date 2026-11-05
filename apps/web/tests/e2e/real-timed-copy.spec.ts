import { expect, test } from "@playwright/test";

import { pixelSignature } from "./viewer";

test.skip(
  process.env.TIMED_REAL_TIMED_COPY !== "1",
  "Run through scripts/run_e2e.py --real-timed-copy <path>.",
);

test("opens the read-only Berzelius-layout TIMED copy", async ({
  page,
}, testInfo) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (reason) => browserErrors.push(reason.message));

  await page.goto("/");
  await page.getByLabel("Compute profile").selectOption("local-timed");
  await expect(page.getByLabel("Dataset")).toHaveValue("timed");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 30_000,
  });

  await page.getByRole("link", { name: "Data", exact: true }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "TIMED", exact: true }),
  ).toBeVisible();
  const plateEntry = page.locator(".file-list li").filter({
    has: page.getByText("PB100140_P2.1", { exact: true }),
  });
  await expect(plateEntry).toContainText("OME-Zarr plate", { timeout: 60_000 });
  await plateEntry.getByRole("button", { name: "Open", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: "PB100140_P2.1",
      exact: true,
    }),
  ).toBeVisible({ timeout: 60_000 });

  const metadata = page.getByTestId("plate-metadata");
  await expect(metadata).toBeVisible();
  await metadata.getByText("Plate and experiment", { exact: true }).click();
  await expect(metadata).toContainText("U2OS_Cleo3");

  await page.getByRole("button", { name: "Well C3" }).click();
  await expect(metadata).toContainText("C03");
  await page.getByTestId("open-selected").click();
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 180_000,
  });
  await expect(page.getByTestId("displayed-time")).toHaveText("t 1 / 48");
  const repairNote = page.getByTestId("site-position-fix-note");
  await expect(repairNote).toHaveText(
    "Site position repaired at timepoint 0: this field holds grid position x = 1 for the whole run.",
  );

  const canvas = page.locator(".canvas-frame canvas").first();
  await expect(canvas).toBeVisible();
  const pixels = await pixelSignature(page, canvas);
  expect(pixels.range).toBeGreaterThan(5);
  await page.screenshot({
    path: testInfo.outputPath("real-timed-copy.png"),
    fullPage: true,
  });

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Disconnect and stop server" })
    .click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
  expect(browserErrors).toEqual([]);
});
