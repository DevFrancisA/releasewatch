/**
 * Capture README screenshots from a running stack:
 *   node --experimental-strip-types screenshots.ts <release-id-for-detail-page>
 * Expects seeded data (bench/seed.py) and a rolled-back release to show.
 */
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8000";
const OUT = new URL("../docs/screenshots/", import.meta.url);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, deviceScaleFactor: 2 });

async function shot(path: string, name: string, prep?: () => Promise<void>) {
  await page.goto(BASE + path);
  await page.waitForLoadState("networkidle");
  await prep?.();
  await page.screenshot({ path: fileURLToPath(new URL(name, OUT)) });
  console.log("wrote", name);
}

await shot("/", "dashboard.png", async () => {
  await page.getByRole("button", { name: "30d" }).click();
  await page.waitForLoadState("networkidle");
});
await shot("/releases", "releases.png");

const releaseId = process.argv[2];
if (releaseId) await shot(`/releases/${releaseId}`, "release-detail.png");

await browser.close();
