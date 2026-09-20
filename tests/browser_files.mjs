// Real Files controls against persisted uploads and Python-produced artifacts.
// Delayed reads below retain the daemon's real body; no fabricated API response.
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { authenticate, waitUntil } from "./browser_auth.mjs";

async function openFiles(page, fid, pid) {
  await page.evaluate(({ fid, pid }) => window.openConversation(fid, pid), { fid, pid });
  await page.locator("#files-btn").click();
  await page.locator('.files-scope [data-scope="frame"]').click();
}
async function filter(page, query = "", type = "", origin = "all") {
  await page.locator(".files-search").fill(query);
  await page.locator(".files-filter-type").fill(type);
  await page.locator(".files-filter-type").press("Tab");
  await page.locator(`.files-origin [data-origin="${origin}"]`).click();
}
async function grid(page, expected, more) {
  let snapshot;
  try {
    snapshot = await waitUntil(`${expected} cards/count and more=${more}`, async () => {
      // Read the whole DOM projection in one browser turn. Separate locator
      // awaits could observe cards from one render and empty from the next.
      snapshot = await page.evaluate(() => ({
        ids: [...document.querySelectorAll("#results-list .art")].map((node) => node.dataset.artifactId),
        count: document.querySelector("#results-count")?.textContent,
        more: document.querySelectorAll(".files-load-more").length,
        empty: document.querySelectorAll("#results-list .files-empty").length,
        frame: window.S.currentId, project: window.S.project, scope: window.S.filesScope,
        query: document.querySelector(".files-search")?.value,
        type: document.querySelector(".files-filter-type")?.value,
        note: document.querySelector(".files-index-note")?.textContent,
        emptyText: document.querySelector(".files-empty")?.textContent,
      }));
      return snapshot.ids.length === expected && snapshot.count === String(expected) &&
        snapshot.more === Number(more) && snapshot.empty === Number(expected === 0) ? snapshot : false;
    });
  } catch (error) {
    throw new Error(`${error.message}; expected=${expected}, more=${more}; snapshot=${JSON.stringify(snapshot)}`);
  }
  assert.equal(new Set(snapshot.ids).size, snapshot.ids.length, "no duplicate artifact identities in the actual DOM");
  return snapshot.ids;
}

export async function realArtifactFilesCheck(page, api, { frame_id: fid, project_id: pid, artifacts }) {
  const artifact = artifacts[0];
  const result = await api(`/frames/${fid}/artifacts`);
  assert.equal(result.status, 200);
  const row = (result.body.artifacts || result.body).find((a) => a.id === artifact.id);
  assert.ok(row && !row.is_user_upload, "reuse actual generated Ark artifact provenance");
  assert.equal(typeof row.version_id, "string");
  assert.ok(row.version_id, "record the current immutable version after earlier editor checks");
  await openFiles(page, fid, pid);
  await filter(page, row.filename, row.content_type, "generated");
  assert.deepEqual(await grid(page, 1, false), [row.id]);
  await filter(page, row.filename, row.content_type, "uploaded");
  await grid(page, 0, false);
  await filter(page);
  return { artifactId: row.id, versionId: row.version_id || row.latest_version_id, filename: row.filename };
}

export async function filesChecks(page, api) {
  const project = await api("/projects", { method: "POST", data: { name: "Files pagination fixture" } });
  assert.equal(project.status, 200);
  const pid = project.body.project_id || project.body.id;
  const made = await api("/frames", { method: "POST", data: { project_id: pid } });
  assert.equal(made.status, 200);
  const fid = made.body.id || made.body.frame_id;
  const generated = await api(`/frames/${fid}/kernel/execute`, { method: "POST", data: {
    language: "python", wait: true, code: "from pathlib import Path\nfor i in range(65):\n    suffix = 'csv' if i % 2 == 0 else 'txt'\n    Path(f'report-generated-{i:03d}.{suffix}').write_text('value\\n7\\n')\nprint('Files fixture: 65 generated')",
  } });
  assert.equal(generated.status, 200); assert.ok(!generated.body.error, JSON.stringify(generated.body));
  const upload = async (filename, frame = fid) => {
    const result = await api("/uploads", { method: "POST", data: { frame_id: frame, filename, content_text: "value\n11\n" } });
    assert.equal(result.status, 200); return result.body.artifact_id;
  };
  // Uploads take a workspace writer lease; sequential requests avoid asking
  // the real coordinator to admit simultaneous writes to one session.
  for (let i = 0; i < 60; i++) {
    await upload(`report-uploaded-${String(i).padStart(3, "0")}.${i % 2 === 0 ? "csv" : "txt"}`);
  }
  const snapshot = await api(`/frames/${fid}/artifacts`);
  assert.equal(snapshot.status, 200);
  const rows = snapshot.body.artifacts || snapshot.body;
  assert.equal(rows.length, 125, "persisted fixture has exactly 125 real artifacts");
  assert.equal(rows.filter((a) => !a.is_user_upload).length, 65);
  const hidden = await upload("report-hidden.csv");
  assert.equal((await api(`/artifacts/${hidden}/priority`, { method: "PATCH", data: { priority: -1 } })).status, 200);
  await openFiles(page, fid, pid);
  await filter(page);
  assert.ok(!(await grid(page, 50, true)).includes(hidden));
  await page.locator(".files-load-more").click(); await grid(page, 100, true);
  await page.locator(".files-load-more").click(); await grid(page, 125, false);
  // All three filters are applied via actual controls and checked by identity.
  for (const [origin, count] of [["uploaded", 30], ["generated", 33]]) {
    await filter(page, "report", "text/csv", origin);
    const ids = await grid(page, count, false);
    assert.deepEqual(new Set(ids), new Set(rows.filter((a) => a.filename.includes("report") && a.content_type.includes("text/csv") && !!a.is_user_upload === (origin === "uploaded")).map((a) => a.id)));
  }
  await filter(page, "missing-report", "text/csv", "generated");
  await grid(page, 0, false);
  assert.match(await page.locator(".files-empty").textContent(), /No files match|没有匹配/);
  await filter(page); await grid(page, 50, true);
  await page.locator(".files-load-more").click(); await grid(page, 100, true);
  const refreshed = page.waitForResponse(async (response) => {
    if (new URL(response.url()).pathname !== `/api/v1/frames/${fid}/artifacts` || response.status() !== 200) return false;
    const body = await response.json();
    return (body.artifacts || body).some((a) => a.filename === "new-refresh.txt");
  });
  const newId = await upload("new-refresh.txt");
  // The real WS update plus read refresh must retain 100 loaded slots.
  await waitUntil("new artifact in the session's source", () => page.evaluate((id) => window.S.artifacts.some((a) => a.id === id), newId));
  await refreshed;
  await grid(page, 100, true);
  await page.locator(".files-load-more").click();
  assert.ok((await grid(page, 126, false)).includes(newId));
  // Scope changes discard cursors; project uses the real artifact-index.
  await page.locator('.files-scope [data-scope="project"]').click(); await grid(page, 50, true);
  await page.locator(".files-load-more").click(); await grid(page, 100, true);
  await page.locator(".files-load-more").click(); await grid(page, 126, false);
  const other = await api("/frames", { method: "POST", data: { project_id: pid } });
  const otherId = other.body.id || other.body.frame_id;
  const only = await upload("other-session.txt", otherId);
  const duplicate = await upload("report-uploaded-000.csv", otherId);
  // A delayed real B artifact read cannot paint A's old cards, even if filters
  // change while the new frame is current. Release the read without fabrication.
  await page.locator('.files-scope [data-scope="frame"]').click();
  let release; const gate = new Promise((resolve) => { release = resolve; });
  let reading = false; const pending = [];
  const pattern = `**/api/v1/frames/${otherId}/artifacts`;
  const delay = (route) => { const work = (async () => { reading = true; await gate; await route.continue(); })(); pending.push(work); return work; };
  await page.route(pattern, delay);
  const opening = page.evaluate((id) => window.openConversation(id), otherId);
  try {
    await waitUntil("B artifact read pending", () => reading);
    await page.locator("#files-btn").click();
    // An empty filter would match A's entire old source; it must still paint
    // nothing while B is pending, rather than merely filtering A out by name.
    await filter(page);
    await grid(page, 0, false);
    await filter(page, "other-session");
    await grid(page, 0, false);
  } finally { release(); await Promise.all(pending); await page.unroute(pattern, delay); await opening; }
  assert.deepEqual(await grid(page, 1, false), [only]);
  await filter(page); await grid(page, 2, false);
  await page.locator('.files-scope [data-scope="project"]').click();
  await filter(page, "report-uploaded-000.csv", "text/csv", "uploaded");
  const original = rows.find((a) => a.filename === "report-uploaded-000.csv");
  assert.deepEqual(new Set(await grid(page, 2, false)), new Set([original.id, duplicate]));
  await filter(page);
  await openFiles(page, fid, pid); await grid(page, 50, true);
  return { frameId: fid, firstPages: [50, 100, 125], refreshed: 126, combinationCounts: [30, 33] };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const playwright = await import("playwright");
  const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
  const source = process.env.OPENAI4S_FILES_EVIDENCE ? JSON.parse(fs.readFileSync(process.env.OPENAI4S_FILES_EVIDENCE, "utf8")) : null;
  const engines = (process.argv.find((arg) => arg.startsWith("--browser="))?.split("=")[1] || "chromium,firefox,webkit").split(",");
  for (const engine of engines) {
    const browser = await playwright[engine].launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    try {
      if (!await authenticate(page, baseUrl)) await page.goto(baseUrl);
      const api = async (path, { method = "GET", data } = {}) => {
        const response = await page.request.fetch(new URL(`/api/v1${path}`, baseUrl).toString(), { method, data });
        return { status: response.status(), body: await response.json() };
      };
      const result = source ? await realArtifactFilesCheck(page, api, source) : await filesChecks(page, api);
      console.log(JSON.stringify({ engine, ...result, success: true }));
    } finally { await browser.close(); }
  }
}
