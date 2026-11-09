import type { Page } from "@playwright/test";

import {
  accessIssues,
  accessMessage,
  collectionIssue,
  qcIssue,
  type AccessIssue,
} from "../../src/lib/accessIssues";
import { expect, test } from "./fixtures";

// A connected Slurm profile whose data server reports what this account can't
// read. The connector and data-server routes are mocked, so nothing here
// contacts a cluster.
const TIMED = { id: "timed", title: "TIMED", path: "/data/zarr", default: true };
const CELLS = {
  id: "cells",
  title: "Cell Health",
  path: "/data/cells/cells.index.parquet",
  default: false,
  qc_available: true,
};
const PROFILE = {
  id: "cluster",
  name: "Shared cluster",
  kind: "slurm",
  data_roots: ["/data", "/data/extra"],
  metadata_root: null,
  host: "login.example.org",
  port: 22,
  username: null,
  known_hosts: null,
  slurm: { account: "project", partition: "cpu", time: "01:00:00", cpus: 1, mem: null },
  remote_state_dir: "~/.timed",
  auto_install_uv: true,
  module_loads: [],
  collections: [TIMED, CELLS],
};
const ISSUES = [
  { kind: "data_root", reason: "Permission denied", path: "/data/extra", collection: null },
  { kind: "collection", reason: "Permission denied", path: TIMED.path, collection: TIMED.path },
  { kind: "qc_export", reason: "Permission denied", path: null, collection: CELLS.path },
];
const SESSION = {
  state: "connected",
  profile_id: "cluster",
  job_id: "42",
  node: "node01",
  handshake: { hostname: "node01", access_issues: [...ISSUES, { kind: "unknown" }] },
};

async function mockConnectedCluster(page: Page): Promise<string[]> {
  const dataRequests: string[] = [];
  await page.route("**/connector/profiles", (route) => route.fulfill({ json: [PROFILE] }));
  await page.route("**/connector/session", (route) => route.fulfill({ json: SESSION }));
  await page.route("**/backend/api/**", (route) => {
    const url = new URL(route.request().url());
    dataRequests.push(url.pathname);
    return url.pathname.endsWith("/api/datasets")
      ? route.fulfill({ json: { datasets: [] } })
      : route.fulfill({ status: 500, json: { detail: "Not expected by this test" } });
  });
  return dataRequests;
}

test("the access report keeps only well-formed issues and reads plainly", () => {
  const issues = accessIssues(SESSION.handshake);
  expect(issues).toEqual(ISSUES);
  expect(accessIssues(null)).toEqual([]);
  expect(accessIssues({ access_issues: "none" })).toEqual([]);
  expect(collectionIssue(issues, TIMED.path)?.kind).toBe("collection");
  expect(collectionIssue(issues, CELLS.path)).toBeNull();
  expect(qcIssue(issues, CELLS.path)?.kind).toBe("qc_export");
  const collections = [TIMED, CELLS];
  expect(issues.map((issue) => accessMessage(issue, collections))).toEqual([
    "Your account can't read the data folder /data/extra.",
    "Your account can't read TIMED (/data/zarr).",
    "Your account can't read the Cell Health QC export, so QC review is unavailable.",
  ]);
  const segmentation = { kind: "segmentation", reason: "Permission denied", collection: TIMED.path };
  expect(accessMessage(segmentation as AccessIssue, collections)).toBe(
    "Your account can't read the TIMED segmentation, so its outlines are unavailable.",
  );
  const annotations = { kind: "qc_annotations", reason: "Not writable", collection: CELLS.path };
  expect(accessMessage(annotations as AccessIssue, collections)).toBe(
    "Your account can't write the Cell Health QC annotations, so QC is read-only.",
  );
});

test("Connect lists what this account can't read and marks the dataset", async ({ page }) => {
  await mockConnectedCluster(page);
  await page.goto("/");

  const report = page.getByTestId("access-issues");
  await expect(report.getByRole("listitem")).toHaveText([
    "Your account can't read the data folder /data/extra.",
    "Your account can't read TIMED (/data/zarr).",
    "Your account can't read the Cell Health QC export, so QC review is unavailable.",
  ]);
  await expect(page.getByLabel("Dataset").locator("option")).toHaveText([
    "TIMED (unavailable)",
    "Cell Health",
  ]);
  // Connected: the login field is gone, as after any login.
  await expect(page.getByLabel("Username")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Disconnect and cancel job" })).toBeVisible();
});

test("Data shows an unavailable dataset without opening it", async ({ page }) => {
  const dataRequests = await mockConnectedCluster(page);
  await page.goto("/data?collection=timed");

  const notice = page
    .getByRole("status")
    .filter({ hasText: "Your account can't read TIMED (/data/zarr)." });
  await expect(notice).toBeVisible();
  await expect(notice.getByRole("link", { name: "Connect" })).toHaveAttribute("href", "/");
  await expect(page.getByText("Folder is empty.")).toHaveCount(0);
  expect(dataRequests.filter((path) => path.endsWith("/api/fs/list"))).toEqual([]);
});

test("QC says the export is unreadable before asking for it", async ({ page }) => {
  const dataRequests = await mockConnectedCluster(page);
  await page.goto("/qc?collection=cells");

  await expect(page.getByRole("alert")).toContainText(
    "Your account can't read the Cell Health QC export, so QC review is unavailable.",
  );
  expect(dataRequests.filter((path) => path.includes("/api/qc/"))).toEqual([]);
});
