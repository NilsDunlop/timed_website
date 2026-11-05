// Temporary refactor snapshots: id masking, golden files and viewer state.
// Goldens are recorded from unchanged code with TIMED_UPDATE_GOLDENS=1. The
// last commit of the Gate A refactor deletes this folder.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, type Page } from "@playwright/test";

const GOLDEN_ROOT = join(dirname(fileURLToPath(import.meta.url)), "golden");
const UPDATE = process.env.TIMED_UPDATE_GOLDENS === "1";

/** Replaces 32-hex dataset and queue ids by their order of first appearance. */
export class IdMask {
  private readonly ids = new Map<string, string>();

  mask(text: string): string {
    return text.replace(/[0-9a-f]{32}/g, (id) => {
      let placeholder = this.ids.get(id);
      if (!placeholder) {
        placeholder = `<id-${this.ids.size + 1}>`;
        this.ids.set(id, placeholder);
      }
      return placeholder;
    });
  }
}

/** Collects named values for one golden file and compares them at the end. */
export class Golden {
  private readonly values: Record<string, unknown> = {};

  constructor(private readonly name: string) {}

  record(label: string, value: unknown): void {
    expect(label in this.values, `duplicate golden label ${label}`).toBe(false);
    this.values[label] = value;
  }

  check(): void {
    const path = join(GOLDEN_ROOT, `${this.name}.json`);
    const actual = JSON.parse(JSON.stringify(this.values)) as Record<string, unknown>;
    if (UPDATE) {
      mkdirSync(GOLDEN_ROOT, { recursive: true });
      writeFileSync(path, JSON.stringify(actual, null, 2) + "\n", "utf-8");
    }
    expect(existsSync(path), `record ${this.name} with TIMED_UPDATE_GOLDENS=1`).toBe(true);
    const expected = JSON.parse(readFileSync(path, "utf-8")) as Record<string, unknown>;
    expect(actual).toEqual(expected);
  }
}

/**
 * Unlink every dataset, so a snapshot starts from the registry state it was
 * recorded in whatever earlier flows linked. Unlinking never touches data.
 */
export async function unlinkAllDatasets(page: Page): Promise<void> {
  const listed = await page.request.get("/backend/api/datasets");
  expect(listed.ok()).toBe(true);
  const { datasets } = (await listed.json()) as { datasets: { id: string }[] };
  for (const { id } of datasets) {
    const removed = await page.request.delete(`/backend/api/datasets/${id}`, {
      headers: { "X-Timed-Client": "web" },
    });
    expect(removed.status()).toBe(204);
  }
}

/** The page path, query and hash with ids masked. */
export function maskedLocation(url: string, mask: IdMask): string {
  const parsed = new URL(url);
  return mask.mask(parsed.pathname + parsed.search + parsed.hash);
}

/** Displayed viewer state; omits pixels, timings, ids and URLs. */
export async function viewerStateSnapshot(page: Page) {
  return page.evaluate(() => {
    const text = (element: Element | null | undefined) =>
      element?.textContent?.replace(/\s+/g, " ").trim() ?? null;
    const controls = document.querySelector("#image-controls");
    const checkedGrayscale = controls?.querySelector<HTMLInputElement>(
      'input[name="grayscale-channel"]:checked',
    );
    const channels = Array.from(
      document.querySelectorAll("#image-controls .channel-control"),
    ).map((fieldset) => {
      const legend = fieldset.querySelector("legend");
      const checkbox = legend?.querySelector<HTMLInputElement>('input[type="checkbox"]');
      const sliders = Array.from(
        fieldset.querySelectorAll<HTMLInputElement>('input[type="range"]'),
      );
      return {
        name: text(legend),
        labels: Array.from(fieldset.querySelectorAll(":scope > label > span")).map(text),
        visible: checkbox ? checkbox.checked : null,
        color:
          legend?.querySelector<HTMLElement>(".channel-swatch")?.style.backgroundColor ?? null,
        contrast: sliders.map((slider) => slider.value),
        domain: sliders.map((slider) => [slider.min, slider.max]),
        step: sliders.map((slider) => slider.step),
        locked: sliders.map((slider) => slider.disabled),
      };
    });
    const timeSlider = document.querySelector<HTMLInputElement>('[data-testid="time-slider"]');
    return {
      heading: text(document.querySelector("#viewer-heading")),
      status: text(document.querySelector('[data-testid="viewer-status"]')),
      controlsHeading: text(controls?.querySelector("h2")),
      displayGroup: Boolean(controls?.querySelector(".viewer-mode-control")),
      mode:
        controls?.querySelector<HTMLInputElement>('input[name="viewer-display-mode"]:checked')
          ?.value ?? null,
      grayscaleChannel: checkedGrayscale ? text(checkedGrayscale.closest("label")) : null,
      channels,
      time: {
        label: text(document.querySelector('label[for="time-slider"]')),
        requested: timeSlider?.value ?? null,
        maximum: timeSlider?.max ?? null,
        displayed: text(document.querySelector('[data-testid="displayed-time"]')),
        jump: text(document.querySelector('[data-testid="time-jump"] span')),
      },
      scaleBars: Array.from(document.querySelectorAll(".canvas-frame")).map((frame) =>
        frame.getAttribute("data-scale-bar"),
      ),
      comparisonCount:
        document.querySelector('[data-testid="comparison-view"]')?.getAttribute("data-count") ??
        null,
      canvases: document.querySelectorAll(".canvas-frame canvas").length,
    };
  });
}
