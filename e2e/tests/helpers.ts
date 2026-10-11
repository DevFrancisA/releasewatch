import { createHmac, randomUUID } from "node:crypto";
import { expect, type APIRequestContext, type TestInfo } from "@playwright/test";

const WEBHOOK_SECRET = process.env.E2E_WEBHOOK_SECRET ?? "dev-webhook-secret";

/**
 * Base URL the *worker* uses to reach health endpoints. Inside docker compose
 * the API is reachable as http://api:8000; override when running elsewhere.
 */
const TARGET = process.env.E2E_TARGET_URL ?? "http://api:8000";
export const HEALTHY_URL = `${TARGET}/healthz`;
export const FAILING_URL = `${TARGET}/api/does-not-exist`;

/** A service name unique to this test run so parallel tests never collide. */
export function uniqueService(info: TestInfo, prefix = "e2e"): string {
  const suffix = `${info.workerIndex}-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`;
  return `${prefix}-${suffix}`;
}

export interface CIEvent {
  event:
    | "deployment.started"
    | "deployment.succeeded"
    | "deployment.failed"
    | "rollback.completed"
    | "rollback.failed";
  service: string;
  version: string;
  environment?: string;
  deployment_id?: string;
  commit_sha?: string;
  health_url?: string;
  actor?: string;
}

/** Send a CI webhook signed the same way a real pipeline would. */
export async function sendWebhook(request: APIRequestContext, payload: CIEvent) {
  const body = JSON.stringify(payload);
  const signature = "sha256=" + createHmac("sha256", WEBHOOK_SECRET).update(body).digest("hex");
  const response = await request.post("/api/webhooks/ci", {
    data: body,
    headers: {
      "Content-Type": "application/json",
      "X-ReleaseWatch-Signature": signature,
      "X-ReleaseWatch-Delivery": randomUUID(),
    },
  });
  expect(response.status(), await response.text()).toBe(202);
  return (await response.json()) as { release_id: number; release_status: string };
}

/** Deploy a release through CI webhooks and return its id. */
export async function deploy(
  request: APIRequestContext,
  service: string,
  version: string,
  healthUrl?: string,
): Promise<number> {
  const base = { service, version, deployment_id: `${service}-${version}`, actor: "ci-bot" };
  await sendWebhook(request, { ...base, event: "deployment.started", commit_sha: "a1b2c3d4e5" });
  const result = await sendWebhook(request, {
    ...base,
    event: "deployment.succeeded",
    health_url: healthUrl,
  });
  return result.release_id;
}

export async function waitForStatus(
  request: APIRequestContext,
  releaseId: number,
  status: string,
): Promise<void> {
  await expect
    .poll(async () => (await (await request.get(`/api/releases/${releaseId}`)).json()).status, {
      timeout: 30_000,
    })
    .toBe(status);
}
