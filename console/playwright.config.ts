import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../build/console-e2e",
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:18766",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "uv run python -m tests.console_server --port 18766",
    cwd: "..",
    url: "http://127.0.0.1:18766/",
    timeout: 60_000,
    reuseExistingServer: false,
  },
});
