import { expect, test } from "@playwright/test";

import { pixelSignature } from "./viewer";

test("starts a local server, renders time data, and stops it", async ({
  page,
}, testInfo) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (reason) => browserErrors.push(reason.message));

  await page.goto("/");
  await page.getByLabel("Compute profile").selectOption("local");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 30_000,
  });
  await expect(page.getByText(/^127\.0\.0\.1:\d+$/)).toBeVisible();

  await page.getByRole("link", { name: "Data", exact: true }).click();
  const root = page.getByRole("listitem").filter({ hasText: "test-data" });
  await root.getByRole("button", { name: "Open folder" }).click();
  const plate = page.getByRole("listitem").filter({ hasText: "test_plate.zarr" });
  await expect(plate).toContainText("OME-Zarr plate");
  await plate.getByRole("button", { name: "Link" }).click();
  await expect(page.getByRole("heading", { name: "test_plate.zarr" })).toBeVisible();
  await page.getByRole("button", { name: "Well A1" }).click();
  await page.getByRole("link", { name: "Open" }).first().click();

  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
  const canvas = page.locator(".canvas-frame canvas").first();
  const firstFrame = await pixelSignature(page, canvas);
  expect(firstFrame.range).toBeGreaterThan(5);
  await page.screenshot({
    path: testInfo.outputPath("local-viewer.png"),
    fullPage: true,
  });

  const timeSlider = page.getByTestId("time-slider");
  await timeSlider.fill("1");
  await page.waitForTimeout(1200);
  const secondFrame = await pixelSignature(page, canvas);
  expect(secondFrame.checksum).not.toBe(firstFrame.checksum);

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Disconnect and stop server" })
    .click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
  expect(browserErrors).toEqual([]);
});
