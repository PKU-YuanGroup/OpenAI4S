// Local manual e2e for the experimental judgment settings surface.
// Not part of CI. Assumes an isolated daemon is already serving
// OPENAI4S_BROWSER_URL (default http://127.0.0.1:8766/) with
// OPENAI4S_JUDGMENT_FAKE_ENDPOINT pointed at the loopback fake.
let playwright;
try {
  playwright = await import("playwright");
} catch (error) {
  const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
  if (!fallback) throw error;
  playwright = await import(fallback);
}
const { chromium } = playwright;
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { authenticate, waitUntil } from "./browser_auth.mjs";

const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8766/";
const dataDir = process.env.OPENAI4S_DATA_DIR;
if (!dataDir) {
  throw new Error("OPENAI4S_DATA_DIR is required (screenshots land in $OPENAI4S_DATA_DIR/screens)");
}
const screens = path.join(dataDir, "screens");
fs.mkdirSync(screens, { recursive: true });

const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));

await authenticate(page, baseUrl);

let shot = 0;
async function screenshot(label) {
  shot += 1;
  const file = path.join(screens, `${String(shot).padStart(2, "0")}-${label}.png`);
  await page.screenshot({ path: file, fullPage: true });
  console.log("screenshot", file);
}

try {
  await page.evaluate(() => window.openCust("general"));
  await page.locator("[data-judgment]").waitFor({ state: "visible" });
  await page.locator('#cust-content[aria-busy="false"]').waitFor({ state: "attached" });
  const skillToggle = page.locator('[data-judgment-cap="skill_suggest"] .toggle');
  assert.equal(await skillToggle.isDisabled(), true, "capability writes are disabled while the master is off");
  await screenshot("customize-experimental-off");

  await page.locator("[data-judgment-master] .toggle").click();
  await page.locator("[data-judgment-disclosure]").waitFor({ state: "visible" });
  await screenshot("disclosure-dialog");

  const confirm = page.locator("[data-judgment-ack]");
  assert.equal(await confirm.isDisabled(), true, "confirm stays disabled until every item is checked");
  const caps = page.locator("[data-judgment-ack-cap]");
  const n = await caps.count();
  assert.ok(n >= 1, "disclosure lists capabilities from GET");
  for (let i = 0; i < n; i += 1) await caps.nth(i).check();
  await waitUntil("disclosure confirm enabled", async () => !(await confirm.isDisabled()));
  await confirm.click();
  await page.locator("[data-judgment-disclosure]").waitFor({ state: "hidden" });
  await waitUntil("master on", async () =>
    (await page.locator("[data-judgment-master]").getAttribute("data-judgment-master")) === "on",
  );
  await screenshot("master-enabled");

  await skillToggle.click();
  await waitUntil("skill suggestions enabled", async () =>
    (await page.locator('[data-judgment-cap="skill_suggest"]').getAttribute("data-judgment-cap-on")) === "on",
  );
  await page.reload();
  await page.evaluate(() => window.openCust("general"));
  await page.locator('[data-judgment-cap="skill_suggest"][data-judgment-cap-on="on"]').waitFor({ state: "visible" });
  assert.equal(await page.locator("[data-judgment-disclosure]").count(), 0, "the acknowledgement survives reload");
  await skillToggle.click();
  await waitUntil("skill suggestions disabled", async () =>
    (await page.locator('[data-judgment-cap="skill_suggest"]').getAttribute("data-judgment-cap-on")) === "off",
  );
  await screenshot("capability-toggle-persisted");

  const keyBox = page.locator("[data-judgment-key]");
  await keyBox.fill("loopback-test-key");
  await page.locator("[data-judgment-save-key]").click();
  await waitUntil("key configured", async () =>
    (await page.locator("[data-judgment-key-state]").getAttribute("data-judgment-key-state")) ===
      "configured",
  );
  assert.equal(await keyBox.inputValue(), "", "saved key is not echoed back into the field");
  await screenshot("key-configured");

  await page.locator("[data-judgment-test]").click();
  await waitUntil("probe ok", async () =>
    (await page.locator("[data-judgment-test-result]").getAttribute("data-judgment-test-result")) ===
      "ok",
  );
  await screenshot("test-ok");

  await page.locator("[data-judgment-clear-key]").click();
  await waitUntil("key cleared", async () =>
    (await page.locator("[data-judgment-key-state]").getAttribute("data-judgment-key-state")) ===
      "missing",
  );
  assert.equal(await page.locator("[data-judgment-test-result]").getAttribute("data-judgment-test-result"), "idle", "credential edits invalidate a previous successful probe");
  await screenshot("key-cleared");

  await page.locator("[data-judgment-test]").click();
  await waitUntil("probe unavailable", async () =>
    (await page.locator("[data-judgment-test-result]").getAttribute("data-judgment-test-result")) ===
      "unavailable",
  );
  await screenshot("test-unavailable");

  assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
  console.log("browser_judgment: disclosure, capability toggle/reload, key save/clear, and probe ok/unavailable passed");
} finally {
  await browser.close();
}
