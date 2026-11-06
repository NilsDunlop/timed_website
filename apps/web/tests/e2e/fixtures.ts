// Shared browser fixtures: every browser flow fails on an unexpected console
// error, and connects and disconnects through the same helpers.
import { expect, test as base, type Page } from "@playwright/test";

type AllowedBrowserError = { pattern: RegExp; reason: string };

export type BrowserErrors = {
  /** Allow one expected console error for this test, with the reason it occurs. */
  allow(pattern: RegExp, reason: string): void;
  readonly messages: readonly string[];
};

export const test = base.extend<{
  browserErrors: BrowserErrors;
  sessionCleanup: void;
}>({
  // A failed flow can leave the connector connected, which would make every
  // later flow fail on the disabled profile choice. Disconnect it.
  sessionCleanup: [
    async ({ request }, use, testInfo) => {
      await use();
      if (testInfo.status === testInfo.expectedStatus) return;
      try {
        const session = await request.get("/connector/session");
        const snapshot = (await session.json()) as { state?: string };
        if (snapshot.state === "disconnected") return;
        await request.post("/connector/disconnect", {
          headers: { "X-Timed-Client": "web" },
          data: { cancel_job: true },
        });
      } catch {
        // The connector is unreachable; nothing to clean up.
      }
    },
    { auto: true },
  ],
  browserErrors: [
    async ({ page }, use) => {
      const messages: string[] = [];
      const allowed: AllowedBrowserError[] = [];
      page.on("console", (message) => {
        if (message.type() === "error") messages.push(message.text());
      });
      page.on("pageerror", (reason) => messages.push(reason.message));
      await use({
        allow(pattern, reason) {
          expect(reason.trim(), "an allowed browser error needs a reason").not.toBe("");
          allowed.push({ pattern, reason });
        },
        messages,
      });
      const unexpected = messages.filter(
        (message) => !allowed.some(({ pattern }) => pattern.test(message)),
      );
      expect(unexpected, "unexpected browser console errors").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

export type ConnectOptions = {
  dataset?: string;
  /** SSH/Slurm profiles prompt for the host key and credentials. */
  credentials?: { password: string; verificationCode: string };
  timeout?: number;
};

export async function connect(page: Page, profile: string, options: ConnectOptions = {}) {
  await page.goto("/");
  await page.getByLabel("Compute profile").selectOption(profile);
  if (options.dataset) await page.getByLabel("Dataset").selectOption(options.dataset);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  if (options.credentials) {
    await expect(page.getByRole("heading", { name: "Confirm SSH host key" })).toBeVisible();
    await page.getByRole("button", { name: "Accept host key" }).click();
    await expect(page.getByRole("heading", { name: "SSH authentication" })).toBeVisible();
    await page.getByLabel("Password:").fill(options.credentials.password);
    await page.getByLabel("Verification code:").fill(options.credentials.verificationCode);
    await page.getByRole("button", { name: "Submit" }).click();
  }
  await expect(page.getByTestId("session-state")).toHaveText("connected", {
    timeout: options.timeout ?? 30_000,
  });
}

export async function disconnect(
  page: Page,
  button: "Disconnect and stop server" | "Disconnect and cancel job" =
    "Disconnect and stop server",
) {
  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await page.getByRole("button", { name: button }).click();
  await expect(page.getByTestId("session-state")).toHaveText("disconnected");
}
