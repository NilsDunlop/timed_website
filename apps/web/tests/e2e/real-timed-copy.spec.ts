import { disconnect, expect, test } from "./fixtures";
import { pixelSignature } from "./viewer";

test.skip(
  process.env.TIMED_REAL_TIMED_COPY !== "1",
  "Run through scripts/run_e2e.py --real-timed-copy <path>.",
);

test("opens the read-only Berzelius-layout TIMED copy", async ({
  page,
}, testInfo) => {
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
  // C03 site 1 carries repair metadata, but the viewer shows no repair note.
  await expect(page.getByTestId("site-position-fix-note")).toHaveCount(0);
  await expect(page.getByText(/Site position repaired/)).toHaveCount(0);
  // The export's masks cover four other fields; the production run covers
  // every field, so with the copy's seg/ folder this one offers it alone.
  const panel = page.getByTestId("segmentation-panel");
  if (process.env.TIMED_REAL_TIMED_SEGMENTATION === "1") {
    const model = panel.getByRole("combobox", { name: "Model", exact: true });
    await expect(model.locator("option")).toHaveText(["R4_cpsam_dS_corrected"]);
    await expect(panel.getByTestId("segmentation-counts")).toHaveText(
      "45 nuclei · 55 cells",
      { timeout: 60_000 },
    );
  } else {
    await expect(panel).toHaveCount(0);
  }

  const canvas = page.locator(".canvas-frame canvas").first();
  await expect(canvas).toBeVisible();
  const pixels = await pixelSignature(page, canvas);
  expect(pixels.range).toBeGreaterThan(5);
  await page.screenshot({
    path: testInfo.outputPath("real-timed-copy.png"),
    fullPage: true,
  });

  await disconnect(page);
});
