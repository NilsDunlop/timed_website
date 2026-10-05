import { expect, type Locator, type Page, test } from "@playwright/test";

async function pixelSignature(page: Page, canvas: Locator) {
  const screenshot = await canvas.screenshot();
  return page.evaluate(async (encoded) => {
    const image = new Image();
    const loaded = new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("Could not decode canvas screenshot"));
    });
    image.src = "data:image/png;base64," + encoded;
    await loaded;
    const target = document.createElement("canvas");
    target.width = image.width;
    target.height = image.height;
    const context = target.getContext("2d");
    if (!context) throw new Error("2D canvas is unavailable");
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, image.width, image.height).data;
    let minimum = 255;
    let maximum = 0;
    let checksum = 0;
    for (let index = 0; index < pixels.length; index += 16) {
      const value = pixels[index] + pixels[index + 1] + pixels[index + 2];
      minimum = Math.min(minimum, value / 3);
      maximum = Math.max(maximum, value / 3);
      checksum = (checksum + value * (index + 1)) % 2_147_483_647;
    }
    return { range: maximum - minimum, checksum };
  }, screenshot.toString("base64"));
}

test("connects through Slurm and renders plate plus native OME-Zarr data", async ({
  page,
}, testInfo) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (reason) => browserErrors.push(reason.message));

  await page.goto("/");
  await page.getByLabel("Compute profile").selectOption("test-cluster");
  await page.getByRole("button", { name: "Connect", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Confirm SSH host key" })).toBeVisible();
  await page.getByRole("button", { name: "Accept host key" }).click();

  await expect(page.getByRole("heading", { name: "SSH authentication" })).toBeVisible();
  await page.getByLabel("Password:").fill("dev-password");
  await page.getByLabel("Verification code:").fill("123456");
  await page.getByRole("button", { name: "Submit" }).click();

  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: 180_000,
  });
  await page.screenshot({
    path: testInfo.outputPath("connected.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "Data", exact: true }).click();

  const root = page.getByRole("listitem").filter({ hasText: "test-data" });
  await expect(root).toBeVisible();
  await root.getByRole("button", { name: "Open folder" }).click();

  const plate = page.getByRole("listitem").filter({ hasText: "test_plate.zarr" });
  await expect(plate).toContainText("OME-Zarr plate");
  await plate.getByRole("button", { name: "Link" }).click();

  await expect(page.getByRole("heading", { name: "test_plate.zarr" })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("data-browser.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Well A1" }).click();
  await page.getByRole("link", { name: "Open" }).first().click();

  await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
    timeout: 60_000,
  });
  const canvas = page.locator(".canvas-frame canvas").first();
  await expect(canvas).toBeVisible();
  const firstFrame = await pixelSignature(page, canvas);
  expect(firstFrame.range).toBeGreaterThan(5);
  await page.screenshot({
    path: testInfo.outputPath("plate-viewer.png"),
    fullPage: true,
  });

  const firstChannel = page.locator(".channel-control input[type=checkbox]").first();
  await firstChannel.uncheck();
  await expect(firstChannel).not.toBeChecked();
  await firstChannel.check();

  const contrastMaximum = page
    .getByRole("slider", { name: /contrast maximum/i })
    .first();
  const originalContrast = await contrastMaximum.inputValue();
  await contrastMaximum.press("ArrowLeft");
  await expect(contrastMaximum).not.toHaveValue(originalContrast);

  const timeSlider = page.getByTestId("time-slider");
  await timeSlider.fill("1");
  await expect(timeSlider).toHaveValue("1");
  await page.waitForTimeout(1200);
  const secondFrame = await pixelSignature(page, canvas);
  expect(secondFrame.checksum).not.toBe(firstFrame.checksum);

  for (const imageName of ["test_image_v04.zarr", "test_image_v05.zarr"]) {
    await page.getByRole("link", { name: "Data", exact: true }).click();
    const imageRoot = page.getByRole("listitem").filter({ hasText: "test-data" });
    await imageRoot.getByRole("button", { name: "Open folder" }).click();
    const imageEntry = page
      .getByRole("listitem")
      .filter({ hasText: imageName });
    await imageEntry.getByRole("button", { name: "Link" }).click();
    await expect(page.getByRole("heading", { name: imageName })).toBeVisible();
    await page.getByRole("link", { name: "Open image" }).click();
    await expect(page.getByTestId("viewer-status")).toHaveText("Image ready", {
      timeout: 60_000,
    });
    const imageCanvas = page.locator(".canvas-frame canvas").first();
    const imageFrame = await pixelSignature(page, imageCanvas);
    expect(imageFrame.range).toBeGreaterThan(5);
  }

  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Disconnect and cancel job" })
    .click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
  expect(browserErrors).toEqual([]);
});
