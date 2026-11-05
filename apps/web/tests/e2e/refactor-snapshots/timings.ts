// Temporary refactor timing capture: viewer milestones from viewerDebug events,
// attached to each browser test as timings.json and compared, never asserted.
// The last commit of the Gate A refactor deletes this folder.
import { writeFileSync } from "node:fs";

import type { Page, TestInfo } from "@playwright/test";

const VIEWER_EVENT = /^\[TIMED viewer \+(\d+)ms\] (\S+)/;

type ViewerEvent = { event: string; at: number };
type PageLoad = { path: string; events: ViewerEvent[] };

function first(events: ViewerEvent[], event: string): number | null {
  return events.find((candidate) => candidate.event === event)?.at ?? null;
}

/** Milliseconds from each time commit to the next displayed frame. */
function timeChanges(events: ViewerEvent[]): number[] {
  const changes: number[] = [];
  let committed: number | null = null;
  for (const { event, at } of events) {
    if (event === "time:commit") committed = at;
    else if (event === "frame:ready" && committed !== null) {
      changes.push(at - committed);
      committed = null;
    }
  }
  return changes;
}

export function summarizeViewerLoads(loads: readonly PageLoad[]) {
  return loads
    .filter((load) => load.events.length > 0)
    .map((load) => ({
      path: load.path,
      loadStartMs: first(load.events, "viewer:load-start"),
      metadataReadyMs: first(load.events, "viewer:image-metadata-ready"),
      loaderReadyMs: first(load.events, "viewer:loader-ready"),
      firstFrameMs: first(load.events, "frame:ready"),
      timeChangeMs: timeChanges(load.events),
    }));
}

/** Records viewer milestones per page load and attaches them when the test ends. */
export async function captureViewerTimings(
  page: Page,
  testInfo: TestInfo,
  use: () => Promise<void>,
): Promise<void> {
  const loads: PageLoad[] = [];
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem("timed.viewer.debug", "1");
    } catch {
      // Storage is unavailable; the viewer then records no timings.
    }
  });
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) {
      loads.push({ path: new URL(frame.url()).pathname, events: [] });
    }
  });
  page.on("console", (message) => {
    const match = VIEWER_EVENT.exec(message.text());
    const load = loads.at(-1);
    if (match && load) load.events.push({ event: match[2], at: Number(match[1]) });
  });
  await use();
  const summary = summarizeViewerLoads(loads);
  if (summary.length === 0) return;
  const path = testInfo.outputPath("timings.json");
  writeFileSync(path, JSON.stringify(summary, null, 2) + "\n", "utf-8");
  await testInfo.attach("timings.json", { path, contentType: "application/json" });
}
