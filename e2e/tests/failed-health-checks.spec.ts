import { expect, test } from "@playwright/test";
import { deploy, FAILING_URL, sendWebhook, uniqueService, waitForStatus } from "./helpers";

test("a deployment whose health checks fail is marked unhealthy", async ({
  page,
  request,
}, testInfo) => {
  const service = uniqueService(testInfo, "failing");
  const releaseId = await deploy(request, service, "v3.0.0", FAILING_URL);

  await page.goto(`/releases/${releaseId}`);
  await expect(page.getByTestId("status-badge").first()).toHaveText("Unhealthy");

  // Two failures reach the threshold; no third check is scheduled.
  await expect(page.getByTestId("health-summary")).toHaveText("0 passed · 2 failed · 3 required");
  const checks = page.getByTestId("health-checks").getByRole("listitem");
  await expect(checks).toHaveCount(2);
  for (const check of await checks.all()) {
    await expect(check).toContainText("failed");
    await expect(check).toContainText("HTTP 404");
  }

  const timeline = page.getByTestId("timeline");
  await expect(timeline).toContainText("deployment.started");
  await expect(timeline).toContainText("deployment.succeeded");
  await expect(timeline).toContainText("verification.failed");
  await expect(timeline).toContainText("2 failed checks (HTTP 404)");

  // The release can now be rolled back.
  await expect(page.getByRole("button", { name: "Roll back" })).toBeVisible();

  // And it is listed under the Unhealthy filter.
  await page.goto(`/releases?service=${service}&status=unhealthy`);
  await expect(page.getByTestId(`release-row-${releaseId}`)).toBeVisible();
});

test("a deployment that fails in CI never starts health checks", async ({
  page,
  request,
}, testInfo) => {
  const service = uniqueService(testInfo, "cifail");
  const base = { service, version: "v0.9.0", deployment_id: `${service}-1` };
  await sendWebhook(request, { ...base, event: "deployment.started" });
  const { release_id } = await sendWebhook(request, { ...base, event: "deployment.failed" });
  await waitForStatus(request, release_id, "failed");

  await page.goto(`/releases/${release_id}`);
  await expect(page.getByTestId("status-badge").first()).toHaveText("Failed");
  await expect(page.getByTestId("timeline")).toContainText("deployment.failed");
  await expect(page.getByTestId("health-checks")).toHaveCount(0);
});
