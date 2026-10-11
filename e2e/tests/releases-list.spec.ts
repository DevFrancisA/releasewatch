import { expect, test } from "@playwright/test";
import { sendWebhook, uniqueService } from "./helpers";

test("release list pages with Load more and filters by status", async ({
  page,
  request,
}, testInfo) => {
  const service = uniqueService(testInfo, "list");
  for (let i = 0; i < 30; i++) {
    const event = i % 10 === 9 ? "deployment.failed" : "deployment.succeeded";
    await sendWebhook(request, { event, service, version: `v0.${i}.0`, deployment_id: `d${i}` });
  }

  await page.goto(`/releases?service=${service}`);
  const rows = page.getByTestId("release-table").locator("tbody tr");
  await expect(rows).toHaveCount(25);
  await expect(rows.first()).toContainText("v0.29.0");

  await page.getByRole("button", { name: "Load more" }).click();
  await expect(rows).toHaveCount(30);
  await expect(rows.last()).toContainText("v0.0.0");
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);

  await page.getByLabel("Status").selectOption("failed");
  await expect(page).toHaveURL(/status=failed/);
  await expect(rows).toHaveCount(3);
});

test("dashboard shows summary stats and links to services", async ({ page, request }, testInfo) => {
  const service = uniqueService(testInfo, "dash");
  await sendWebhook(request, { event: "deployment.succeeded", service, version: "v1.0.0" });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByTestId("stat-releases")).not.toHaveText("0");
  await page.getByRole("link", { name: service, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/releases\\?service=${service}`));
  await expect(page.getByTestId("release-table").locator("tbody tr")).toHaveCount(1);
});
