import { type Locator, type Page } from "@playwright/test";

export async function pixelSignature(page: Page, canvas: Locator) {
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

/** How many pixels of a canvas screenshot match a colour within a tolerance. */
export async function colorPixelCount(
  page: Page,
  canvas: Locator,
  color: readonly [number, number, number],
  tolerance = 8,
): Promise<number> {
  const screenshot = await canvas.screenshot();
  return page.evaluate(
    async ({ encoded, rgb, limit }) => {
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
      const data = context.getImageData(0, 0, image.width, image.height).data;
      let count = 0;
      for (let index = 0; index < data.length; index += 4) {
        if (
          Math.abs(data[index] - rgb[0]) <= limit &&
          Math.abs(data[index + 1] - rgb[1]) <= limit &&
          Math.abs(data[index + 2] - rgb[2]) <= limit
        ) {
          count += 1;
        }
      }
      return count;
    },
    { encoded: screenshot.toString("base64"), rgb: [...color], limit: tolerance },
  );
}
