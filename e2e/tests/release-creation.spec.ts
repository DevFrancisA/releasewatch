import { expect, test } from "@playwright/test";
import { HEALTHY_URL, uniqueService } from "./helpers";

test("registering a release from the UI runs health checks to a healthy verdict", async ({
  page,
}, testInfo) => {
  const service = uniqueService(testInfo, "create");

  await page.goto("/");
  await page.getByRole("link", { name: "Register release" }).first().click();
  await expect(page.getByRole("heading", { name: "Register a release" })).toBeVisible();

  await page.getByLabel("Service").fill(service);
  await page.getByLabel("Environment").fill("staging");
  await page.getByLabel("Version").fill("v1.2.0");
  await page.getByLabel("Commit SHA").fill("9f8e7d6c5b");
  await page.getByLabel("Health URL").fill(HEALTHY_URL);
  await page.getByLabel("Triggered by").fill("playwright");
  await page.getByRole("button", { name: "Register release" }).click();

  // Lands on the detail page while verification is in progress...
  await expect(page).toHaveURL(/\/releases\/\d+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(service);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("v1.2.0");
  await expect(page.getByText("9f8e7d6", { exact: true })).toBeVisible();

  // ...and the page updates itself as the worker reports each check.
  await expect(page.getByTestId("status-badge").first()).toHaveText("Healthy");
  await expect(page.getByTestId("health-summary")).toHaveText("3 passed · 0 failed · 3 required");
  await expect(page.getByTestId("health-checks").getByRole("listitem")).toHaveCount(3);

  const timeline = page.getByTestId("timeline");
  await expect(timeline).toContainText("release.created");
  await expect(timeline).toContainText("verification.started");
  await expect(timeline).toContainText("verification.passed");

  // The new release shows up in the list, filtered by its service.
  await page.goto(`/releases?service=${service}`);
  const rows = page.getByTestId("release-table").locator("tbody tr");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("v1.2.0");
  await expect(rows.first().getByTestId("status-badge")).toHaveText("Healthy");
});

test("the form surfaces API validation errors", async ({ page }) => {
  await page.goto("/releases/new");
  await page.getByLabel("Service").fill("valid-name");
  await page.getByLabel("Version").fill("v1");
  await page.getByLabel("Health URL").fill("http://");
  // Bypass the browser's own URL check to exercise server-side validation.
  await page.locator("form").evaluate((form) => form.setAttribute("novalidate", ""));
  await page.getByRole("button", { name: "Register release" }).click();
  await expect(page.getByRole("alert")).toContainText("health_url");
  await expect(page).toHaveURL(/\/releases\/new$/);
});
