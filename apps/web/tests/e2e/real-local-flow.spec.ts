import { expect, test } from "@playwright/test";

import { pixelSignature } from "./viewer";

const rootName = process.env.TIMED_REAL_LOCAL_ROOT_NAME;
const storeName = process.env.TIMED_REAL_LOCAL_STORE;

test.skip(
  !rootName || !storeName,
  "Set the real-local environment through scripts/run_e2e.py.",
);

test("renders a real local time series and measures timepoint changes", async ({
  page,
}, testInfo) => {
  const browserErrors: string[] = [];
  let zarrResponses = 0;
  const isZarrRequest = (url: string) => url.includes("/backend/zarr/");
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (reason) => browserErrors.push(reason.message));
  page.on("response", (response) => {
    if (isZarrRequest(response.url())) zarrResponses += 1;
  });

  await page.goto("/");
  await page.getByLabel("Compute profile").selectOption("local");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 30_000,
  });

  await page.getByRole("link", { name: "Data", exact: true }).click();
  const root = page.getByRole("listitem").filter({ hasText: rootName! });
  await root.getByRole("button", { name: "Open folder" }).click();
  const store = page.getByRole("listitem").filter({ hasText: storeName! });
  await expect(store).toContainText("OME-Zarr plate", { timeout: 30_000 });
  await store.getByRole("button", { name: "Link" }).click();
  await expect(page.getByRole("heading", { name: storeName! })).toBeVisible();
  await page.getByRole("button", { name: "Well D12" }).click();

  const initialStarted = performance.now();
  await page.getByRole("link", { name: "Open" }).first().click();
  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 120_000,
  });
  const initialMilliseconds = performance.now() - initialStarted;

  await expect(page.getByTestId("displayed-time")).toHaveText("t 1 / 48");
  const canvas = page.locator(".canvas-frame canvas").first();
  await expect(canvas).toBeVisible();
  let previousFrame = await pixelSignature(page, canvas);
  expect(previousFrame.range).toBeGreaterThan(5);

  const changeMilliseconds: number[] = [];
  const timeSlider = page.getByTestId("time-slider");
  for (const timepoint of [1, 2, 3]) {
    const started = performance.now();
    await timeSlider.fill(String(timepoint));
    await expect(page.getByTestId("displayed-time")).toHaveText(
      "t " + String(timepoint + 1) + " / 48",
      { timeout: 60_000 },
    );
    await expect(page.getByTestId("viewer-status")).toHaveText("Image ready");

    const nextFrame = await pixelSignature(page, canvas);
    expect(nextFrame.checksum).not.toBe(previousFrame.checksum);
    changeMilliseconds.push(performance.now() - started);
    previousFrame = nextFrame;
  }

  console.log(
    "REAL_LOCAL_TIMINGS " +
      JSON.stringify({
        initial_ms: Math.round(initialMilliseconds),
        timepoint_ms: changeMilliseconds.map(Math.round),
        zarr_responses: zarrResponses,
      }),
  );
  await page.screenshot({
    path: testInfo.outputPath("real-local-viewer.png"),
    fullPage: true,
  });

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Disconnect and stop server" })
    .click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
  expect(browserErrors).toEqual([]);
});
