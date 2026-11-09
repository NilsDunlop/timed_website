import {
  initialUsername,
  isUsername,
  readUsername,
  writeUsername,
} from "../../src/lib/connectUsername";
import { expect, test } from "./fixtures";

// A shared Slurm profile without a username, as config/profiles.yaml has it,
// and a local profile, which needs none. The connector routes are mocked, so
// nothing here contacts a cluster.
const SHARED = {
  id: "cluster",
  name: "Shared cluster",
  kind: "slurm",
  data_roots: ["/data"],
  metadata_root: null,
  host: "login.example.org",
  port: 22,
  username: null,
  known_hosts: null,
  slurm: { account: "project", partition: "cpu", time: "01:00:00", cpus: 1, mem: null },
  remote_state_dir: "~/.timed",
  auto_install_uv: true,
  module_loads: [],
  collections: [],
};
const LAPTOP = {
  ...SHARED,
  id: "laptop",
  name: "This computer",
  kind: "local",
  host: null,
  slurm: null,
};

test("the username helpers keep only plain login names", () => {
  for (const name of ["alice", "x_user", "a.b-c", "dev"]) expect(isUsername(name)).toBe(true);
  for (const name of ["", "-oProxyCommand=x", "two words", "a/b"]) {
    expect(isUsername(name)).toBe(false);
  }
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
  };
  expect(readUsername(storage, "cluster")).toBeNull();
  writeUsername(storage, "cluster", "alice");
  expect(readUsername(storage, "cluster")).toBe("alice");
  expect(readUsername(storage, "other")).toBeNull();
  values.set("timed.connect.username.cluster", "not a name");
  expect(readUsername(storage, "cluster")).toBeNull();
  expect(initialUsername(null, "dev")).toBe("dev");
  expect(initialUsername("alice", "dev")).toBe("alice");
  expect(initialUsername(null, null)).toBe("");
});

test("a Slurm profile asks for a username, sends it and remembers it", async ({ page }) => {
  const sent: unknown[] = [];
  await page.route("**/connector/profiles", (route) =>
    route.fulfill({ json: [SHARED, LAPTOP] }),
  );
  await page.route("**/connector/session", (route) =>
    route.fulfill({ json: { state: "disconnected" } }),
  );
  await page.route("**/connector/connect", async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({
      status: 202,
      json: { state: "error", profile_id: "cluster", message: "Stopped by the test" },
    });
  });

  await page.goto("/");
  const username = page.getByLabel("Username");
  const connect = page.getByRole("button", { name: "Connect", exact: true });
  await expect(username).toHaveValue("");
  await expect(page.getByText("Your login name on login.example.org.")).toBeVisible();
  await expect(connect).toBeDisabled();

  await username.fill("two words");
  await expect(page.getByText("Enter only the login name")).toBeVisible();
  await expect(connect).toBeDisabled();

  await username.fill(" alice ");
  await expect(connect).toBeEnabled();
  await connect.click();
  await expect.poll(() => sent).toEqual([{ profile_id: "cluster", username: "alice" }]);

  await page.reload();
  await expect(page.getByLabel("Username")).toHaveValue("alice");
  await page.getByLabel("Compute profile").selectOption("laptop");
  await expect(page.getByLabel("Username")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toBeEnabled();
});
