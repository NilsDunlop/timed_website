import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  expect: {
    timeout: 20_000,
  },
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4321",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
    ...devices["Desktop Chrome"],
    launchOptions: {
      args: ["--enable-webgl", "--ignore-gpu-blocklist", "--use-angle=swiftshader"],
    },
  },
});
