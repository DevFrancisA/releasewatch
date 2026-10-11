import { expect, test } from "@playwright/test";
import {
  deploy,
  FAILING_URL,
  HEALTHY_URL,
  sendWebhook,
  uniqueService,
  waitForStatus,
} from "./helpers";

test("manual rollback shows target and tracks status until CI completes it", async ({
  page,
  request,
}, testInfo) => {
  const service = uniqueService(testInfo, "rollback");
  const v1 = await deploy(request, service, "v1.0.0", HEALTHY_URL);
  await waitForStatus(request, v1, "healthy");
  const v2 = await deploy(request, service, "v1.1.0", HEALTHY_URL);
  await waitForStatus(request, v2, "healthy");

  await page.goto(`/releases/${v2}`);
  await expect(page.getByTestId("status-badge").first()).toHaveText("Healthy");

  await page.getByRole("button", { name: "Roll back" }).click();
  const confirm = page.getByRole("button", { name: "Confirm rollback" });
  await expect(confirm).toBeDisabled();
  await page.getByLabel("Reason").fill("p99 latency regression");
  await confirm.click();

  const status = page.getByTestId("rollback-status");
  await expect(status).toContainText("Rolling back");
  await expect(status).toContainText("Waiting for CI to finish rolling back to v1.0.0");
  await expect(page.getByText("p99 latency regression").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Roll back" })).toHaveCount(0);
  await expect(page.getByTestId("timeline")).toContainText("rollback.requested");

  // CI reports the rollback finished; the open page picks it up by polling.
  await sendWebhook(request, {
    event: "rollback.completed",
    service,
    version: "v1.1.0",
    deployment_id: `${service}-v1.1.0`,
  });
  await expect(status).toContainText("Rolled back to v1.0.0");
  await expect(page.getByTestId("timeline")).toContainText("rollback.completed");

  // The target link navigates to the release we rolled back to.
  await status.getByRole("link", { name: "v1.0.0" }).click();
  await expect(page).toHaveURL(new RegExp(`/releases/${v1}$`));
  await expect(page.getByTestId("status-badge").first()).toHaveText("Healthy");
});

test("failed rollback returns the release to unhealthy", async ({ page, request }, testInfo) => {
  const service = uniqueService(testInfo, "rbfail");
  const releaseId = await deploy(request, service, "v2.0.0");
  await waitForStatus(request, releaseId, "healthy");
  const response = await request.post(`/api/releases/${releaseId}/rollback`, {
    data: { reason: "bad migration" },
  });
  expect(response.ok()).toBeTruthy();

  await sendWebhook(request, {
    event: "rollback.failed",
    service,
    version: "v2.0.0",
    deployment_id: `${service}-v2.0.0`,
  });

  await page.goto(`/releases/${releaseId}`);
  await expect(page.getByTestId("rollback-status")).toContainText("Rollback failed");
  await expect(page.getByTestId("status-badge").first()).toHaveText("Unhealthy");
});

test("auto-rollback kicks in when health checks fail", async ({ page, request }, testInfo) => {
  const service = uniqueService(testInfo, "auto");
  const put = await request.put(`/api/services/${service}`, {
    data: { name: service, auto_rollback: true },
  });
  expect(put.ok()).toBeTruthy();

  const good = await deploy(request, service, "v5.0.0", HEALTHY_URL);
  await waitForStatus(request, good, "healthy");
  const bad = await deploy(request, service, "v5.1.0", FAILING_URL);

  await page.goto(`/releases/${bad}`);
  const status = page.getByTestId("rollback-status");
  await expect(status).toContainText("Rolling back");
  await expect(status).toContainText("v5.0.0");
  await expect(page.getByText(/Automatic: 2 of 2 health checks failed/).first()).toBeVisible();

  const timeline = page.getByTestId("timeline");
  await expect(timeline).toContainText("verification.failed");
  await expect(timeline).toContainText("rollback.requested");
});
