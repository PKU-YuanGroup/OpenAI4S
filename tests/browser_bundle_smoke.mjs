// The browser layer of the Linux package acceptance (§11 R2).
//
// The installed-runtime smoke (R1) proves the packaged daemon serves, executes
// Cells, captures an Artifact version and reads it back after a restart. What
// it cannot prove is that a browser can open that workbench and read those
// exact versions: a Vite shell whose bundle fails to load, a viewer that
// silently falls back to the latest version, or an image element that never
// completes all pass every HTTP-level check.
//
// This script is called by `installed_runtime_smoke.py --browser` after its
// restart readback, against the same daemon and data directory. Everything it
// needs comes from the environment, because the caller owns the lifecycle:
//
//   OPENAI4S_BROWSER_URL   the loopback origin of the running daemon
//   OPENAI4S_DATA_DIR     its data directory (for the access token)
//   OPENAI4S_SMOKE_EXPECT R1's expectation file: the session id, and for the
//                         CSV and PNG artifacts their artifact id, exact
//                         version id, SHA-256 and filename
//
// Scene (120 s total, the caller's F16 budget): authenticate through the same
// loopback-token hand-off every other browser harness uses and skip the
// first-run wizard; open the session; open the CSV's FIRST version by its exact
// id and require the rendered table to contain 42 and not the second version's
// 84; open the PNG and require the image to load with a non-zero size; and
// require every HTTP request the page issued to be loopback. A failure prints
// a bounded, credential-redacted message and exits non-zero, which the caller
// records as `browser_open: failed` in the scenario report.
//
// The script never calls a model, writes nothing to the data directory, and
// reads no network destination other than the daemon it was pointed at.

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import { PassThrough } from "node:stream";

import {
  authenticate,
  boundedLogCollector,
  redactSecrets,
  skipFirstRunWizard,
  waitUntil,
} from "./browser_auth.mjs";

const TOTAL_BUDGET_MS = 120_000;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
const PNG_MAGIC = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const EXPECTED_CSV_TEXT = "a,b\n1,42\n";
const SECOND_CSV_MARKER = "84";

class BrowserSmokeError extends Error {}

function requiredEnv(name) {
  const value = (process.env[name] || "").trim();
  if (!value) throw new BrowserSmokeError(`${name} is not set; this script is driven by installed_runtime_smoke.py --browser`);
  return value;
}

function loadExpectation() {
  const path = requiredEnv("OPENAI4S_SMOKE_EXPECT");
  let payload;
  try {
    payload = JSON.parse(fs.readFileSync(path, "utf8"));
  } catch (error) {
    throw new BrowserSmokeError(`the expectation file is unreadable: ${error.message}`);
  }
  if (!payload || typeof payload !== "object") {
    throw new BrowserSmokeError("the expectation file is not an object");
  }
  const session = String(payload.frame_id || "").trim();
  if (!session) throw new BrowserSmokeError("the expectation file names no session");
  const project = String(payload.project_id || "").trim();
  if (!project) throw new BrowserSmokeError("the expectation file names no project");
  const artifacts = {};
  for (const kind of ["csv", "png"]) {
    const entry = payload[kind];
    if (!entry || typeof entry !== "object") {
      throw new BrowserSmokeError(`the expectation file records no ${kind} artifact`);
    }
    for (const field of ["artifact_id", "version_id", "checksum", "filename"]) {
      const value = String(entry[field] || "").trim();
      if (!value) {
        throw new BrowserSmokeError(`the expectation file records no ${kind}.${field}`);
      }
      artifacts[`${kind}_${field}`] = value;
    }
    if (!/^[0-9a-f]{64}$/.test(artifacts[`${kind}_checksum`].toLowerCase())) {
      throw new BrowserSmokeError(`the expectation file records no ${kind} checksum`);
    }
  }
  return { session, project, artifacts };
}

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function apiPath(base, path) {
  return new URL(`api/v1${path}`, base).toString();
}

async function apiJson(request, base, path, options = {}) {
  const response = await request.fetch(apiPath(base, path), {
    method: options.method || "GET",
    data: options.data,
    headers: options.data === undefined ? undefined : { "Content-Type": "application/json" },
    failOnStatusCode: false,
  });
  const text = await response.text();
  if (!response.ok()) {
    throw new BrowserSmokeError(`${options.method || "GET"} ${path} answered HTTP ${response.status()}: ${text.slice(0, 300)}`);
  }
  return text ? JSON.parse(text) : null;
}

async function downloadVersion(request, base, versionId) {
  const response = await request.fetch(
    apiPath(base, `/artifacts/versions/${encodeURIComponent(versionId)}`),
    { failOnStatusCode: false }
  );
  if (!response.ok()) {
    throw new BrowserSmokeError(`artifact version ${versionId} answered HTTP ${response.status()}`);
  }
  return Buffer.from(await response.body());
}

async function ensureDockOpen(page) {
  if (await page.locator("#rightdock.collapsed").count()) {
    await page.locator(".nb-tray").click();
  }
  await page.locator("#rightdock:not(.collapsed)").waitFor({ state: "visible", timeout: 15000 });
}

async function showExactVersion(page, { artifactId, filename, versionId }) {
  await page.evaluate(
    async (target) => {
      await window.openViewer({
        id: target.artifactId,
        filename: target.filename,
        version_id: target.versionId,
      });
    },
    { artifactId, filename, versionId }
  );
  await ensureDockOpen(page);
}

async function main() {
  let playwright;
  try {
    playwright = await import("playwright");
  } catch (error) {
    const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
    if (!fallback) {
      throw new BrowserSmokeError(`playwright is not installed: ${error.message}`);
    }
    playwright = await import(fallback);
  }
  const { chromium } = playwright;

  const baseUrl = requiredEnv("OPENAI4S_BROWSER_URL");
  requiredEnv("OPENAI4S_DATA_DIR");
  const expectation = loadExpectation();
  const base = new URL(baseUrl);
  if (!LOOPBACK_HOSTS.has(base.hostname)) {
    throw new BrowserSmokeError(`OPENAI4S_BROWSER_URL is not a loopback origin: ${base.origin}`);
  }

  const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
  const browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();

  const requests = [];
  page.on("request", (request) => requests.push(request.url()));
  const consoleTailStream = new PassThrough();
  const consoleTail = boundedLogCollector(consoleTailStream, 8192);
  page.on("console", (message) => {
    consoleTailStream.write(`${message.type()}: ${message.text()}\n`);
  });
  const deadline = Date.now() + TOTAL_BUDGET_MS;
  let accessToken = "";

  const within = async (label, action, capMs = null) => {
    const left = deadline - Date.now();
    if (left <= 0) {
      throw new BrowserSmokeError(`the ${TOTAL_BUDGET_MS / 1000}s browser budget expired before ${label}`);
    }
    const budget = Math.max(1000, Math.min(capMs ?? left, left));
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new BrowserSmokeError(`${label} did not finish within ${(budget / 1000).toFixed(0)}s`)),
        budget
      );
    });
    try {
      return await Promise.race([action(), timeout]);
    } finally {
      clearTimeout(timer);
    }
  };

  try {
    await within("authentication", async () => {
      accessToken = await authenticate(page, baseUrl);
      if (!accessToken) {
        throw new BrowserSmokeError("the daemon did not accept an access token from OPENAI4S_DATA_DIR");
      }
      await skipFirstRunWizard(page, baseUrl);
      await page.locator("#dashboard").waitFor({ state: "visible", timeout: 20000 });
    });

    // The session the R1 scenario created and its captured artifacts, opened
    // through the workbench's own navigation (not a bare file URL).
    await within("opening the session", async () => {
      await page.evaluate(
        async ({ frameId, projectId }) => window.openConversation(frameId, projectId),
        { frameId: expectation.session, projectId: expectation.project }
      );
      await waitUntil("the session opened", async () => {
        const current = await page.evaluate(() => (window.S && window.S.currentId) || "");
        return current === expectation.session || page.url().includes(expectation.session);
      }, 20000);
    });

    // The exact CSV version, with the first revision's content and never the
    // same-name rewrite's.
    await within("opening the exact CSV version", async () => {
      await showExactVersion(page, {
        artifactId: expectation.artifacts.csv_artifact_id,
        filename: expectation.artifacts.csv_filename,
        versionId: expectation.artifacts.csv_version_id,
      });
      await waitUntil(
        "the CSV table renders",
        async () => (await page.locator("#dock-viewer table.sheet").count()) > 0,
        20000
      );
      const text = await page.locator("#dock-viewer").innerText();
      assert.ok(
        text.includes("42"),
        "the exact CSV version did not render the first revision's 42; the viewer " +
          "opened another version or the table is empty"
      );
      assert.ok(
        !text.includes(SECOND_CSV_MARKER),
        "the exact CSV version rendered the second revision's 84; the viewer fell back to latest"
      );
    });

    // The exact CSV bytes are the ones R1 captured, not merely something that
    // renders as a table.
    await within("verifying the exact CSV bytes", async () => {
      const body = await downloadVersion(page.request, base, expectation.artifacts.csv_version_id);
      assert.equal(
        sha256(body),
        expectation.artifacts.csv_checksum.toLowerCase(),
        "the exact CSV version served bytes that do not match the captured checksum"
      );
      assert.equal(
        body.toString("utf8"),
        EXPECTED_CSV_TEXT,
        "the exact CSV version does not contain the first revision"
      );
    });

    // The PNG artifact: correct exact bytes, and a real image element that
    // decodes with a non-zero size (a broken src yields a 0x0 natural size).
    await within("opening the captured PNG", async () => {
      await showExactVersion(page, {
        artifactId: expectation.artifacts.png_artifact_id,
        filename: expectation.artifacts.png_filename,
        versionId: expectation.artifacts.png_version_id,
      });
      await waitUntil("the image renders", async () => {
        const image = page.locator("#dock-viewer img").first();
        if ((await image.count()) === 0) return false;
        return image
          .evaluate((node) => node.complete && node.naturalWidth > 0 && node.naturalHeight > 0)
          .catch(() => false);
      }, 20000);
    });

    await within("verifying the exact PNG bytes", async () => {
      const body = await downloadVersion(page.request, base, expectation.artifacts.png_version_id);
      assert.equal(
        sha256(body),
        expectation.artifacts.png_checksum.toLowerCase(),
        "the exact PNG version served bytes that do not match the captured checksum"
      );
      assert.ok(body.subarray(0, 8).equals(PNG_MAGIC), "the captured PNG artifact is not a PNG");
    });

    // Everything the page fetched must be the daemon on loopback. A relative
    // URL resolves against the origin and passes; anything else is a leak the
    // packaged workbench must not make while opening an artifact.
    await within("checking for off-loopback requests", async () => {
      const offending = requests.filter((url) => {
        let parsed;
        try {
          parsed = new URL(url);
        } catch {
          return false;
        }
        if (!/^https?$/.test(parsed.protocol)) return false;
        return !LOOPBACK_HOSTS.has(parsed.hostname);
      });
      assert.deepEqual(offending, [], `the page reached non-loopback origins: ${offending.join(", ")}`);
    });
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    const consoleText = consoleTail();
    if (consoleText) {
      // Kept only for a failure path below; never printed on success.
      process.env.OPENAI4S_BROWSER_CONSOLE_TAIL = consoleText.slice(-4096);
    }
  }
}

let failure = "";
try {
  await main();
} catch (error) {
  const detail = redactSecrets(String(error?.stack || error?.message || error));
  const consoleTail = process.env.OPENAI4S_BROWSER_CONSOLE_TAIL || "";
  const bounded = consoleTail ? `\nconsole tail: ${redactSecrets(consoleTail)}` : "";
  failure = `${detail}${bounded}`;
}
if (failure) {
  process.stderr.write(`browser bundle smoke: FAILED — ${failure.slice(0, 4000)}\n`);
  process.exit(1);
}
process.stdout.write(
  "browser bundle smoke: passed — exact CSV version rendered 42 (not 84), " +
    "PNG decoded with a non-zero size, and no non-loopback request was made\n"
);
process.exit(0);
