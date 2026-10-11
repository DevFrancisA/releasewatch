import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against the full stack (API + worker + Postgres +
 * built frontend), normally started with `docker compose up` from the repo
 * root. Set fast health-check timings on the stack so verification completes
 * in seconds:
 *
 *   RELEASEWATCH_HEALTH_CHECK_INITIAL_DELAY_SECONDS=1 \
 *   RELEASEWATCH_HEALTH_CHECK_INTERVAL_SECONDS=1 docker compose up -d --build
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
