// Real production records and download controls, with explicit read faults.
// Successful payloads always come from the daemon. No live model is required.
import assert from "node:assert/strict";
import fs from "node:fs";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { authenticate, waitUntil } from "./browser_auth.mjs";

async function menu(page, key, session = false) {
  const label = await page.evaluate((key) => window.t(key), key);
  if (session) await page.locator("#session-menu-btn").click();
  else await page.locator("#dock-viewer").getByTitle(await page.evaluate(() => window.t("viewer.act.more")), { exact: true }).click();
  await page.getByRole("menuitem", { name: label, exact: true }).click();
}
async function downloaded(page, action) {
  const waiting = page.waitForEvent("download"); await action();
  const download = await waiting;
  const body = fs.readFileSync(await download.path(), "utf8");
  return { body, filename: download.suggestedFilename(), sha256: crypto.createHash("sha256").update(body).digest("hex") };
}
async function open(page, row, sub = "code") {
  await page.evaluate(async ({ row, sub }) => { await window.openViewer(row); window.S.provSub = sub; }, { row, sub });
  await page.locator('[data-f16-provenance="1"]').click();
}
async function paintSettled(page) {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function holdReads(page, match) {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const captured = [];
  const pending = [];
  const handler = (route) => {
    if (!match(new URL(route.request().url()))) return route.fallback();
    const work = (async () => {
      const response = await route.fetch();
      const body = await response.body();
      captured.push({ url: route.request().url(), body: JSON.parse(body.toString()) });
      await gate;
      await route.fulfill({ response, body });
    })();
    pending.push(work); return work;
  };
  await page.route("**/api/v1/**", handler);
  return { captured, async finish() { release(); await Promise.all(pending); await page.unroute("**/api/v1/**", handler); } };
}

async function movingHeadExport(page, api, row) {
  // Files opens the live head tab, unlike immutable palette/deep-link hits.
  await page.locator("#files-btn").click();
  await page.locator('.files-scope [data-scope="frame"]').click();
  await page.locator(".files-search").fill(row.filename);
  await page.locator(".files-filter-type").fill("");
  await page.locator(".files-filter-type").press("Tab");
  await page.locator('.files-origin [data-origin="all"]').click();
  await page.locator(`#results-list .art[data-artifact-id="${row.id}"]`).click();
  assert.equal(await page.evaluate(() => !!window.S.dockArtifact._exactVersion), false);
  const held = await holdReads(page, (url) => [`/api/v1/artifacts/${row.id}/versions`, `/api/v1/artifacts/${row.id}/lineage`].includes(url.pathname));
  let download;
  try {
    download = downloaded(page, () => menu(page, "menu.exportMetadata"));
    await waitUntil("both export reads captured", () => held.captured.length === 2);
    const edited = await api(`/artifacts/${row.id}/edit`, { method: "POST", data: { expected_version_id: row.version_id, content: "Updated after export began\n" } });
    assert.equal(edited.status, 200);
    await waitUntil("actual WS advances the live Viewer head", () => page.evaluate((old) => window.S.dockArtifact.version_id !== old, row.version_id));
  } finally { await held.finish(); }
  const file = await download;
  const metadata = JSON.parse(file.body);
  assert.equal(metadata.version_id, row.version_id);
  assert.equal(metadata.lineage.version_id, row.version_id);
  assert.equal(metadata.filename, row.filename);
  assert.equal(metadata.size_bytes, row.size_bytes);
  assert.ok(held.captured.some((read) => read.url.endsWith(`/lineage?version=${row.version_id}`)));
  return { versionId: metadata.version_id, sha256: file.sha256 };
}
async function failedExport(page, path, payload, action, { status = 200 } = {}) {
  let downloads = 0; let reads = 0;
  const observe = () => { downloads++; };
  page.on("download", observe);
  const handler = (route) => { reads++; return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) }); };
  await page.route(path, handler);
  try {
    await page.evaluate(() => window.hint(""));
    await action();
    await waitUntil("explicit export error", async () => /^(Error:|错误：)/.test(await page.locator("#composer-hint").textContent()));
    await paintSettled(page);
    assert.ok(reads > 0); assert.equal(downloads, 0, "a failed required read cannot download a partial export");
  } finally { await page.unroute(path, handler); page.off("download", observe); }
}

export async function realArtifactProvenanceCheck(page, api, source) {
  const fid = source.frame_id;
  await page.evaluate(({ fid, pid }) => window.openConversation(fid, pid), { fid, pid: source.project_id });
  const listed = await api(`/frames/${fid}/artifacts`);
  assert.equal(listed.status, 200);
  const row = listed.body.find((row) => row.id === source.artifacts[0].id);
  assert.ok(row && row.version_id && !row.is_user_upload, "reuse the real Ark-generated artifact");
  const version = row.version_id;
  const lineage = await api(`/artifacts/${row.id}/lineage?version=${version}`);
  const versions = await api(`/artifacts/${row.id}/versions`);
  assert.equal(lineage.status, 200); assert.equal(versions.status, 200);
  await open(page, row, "review");
  await waitUntil("real artifact lineage", async () => (await page.locator(".prov-body .prov-card").count()) > 0);
  const meta = await downloaded(page, () => menu(page, "menu.exportMetadata"));
  const parsed = JSON.parse(meta.body);
  assert.equal(parsed.id, row.id); assert.equal(parsed.version_id, version);
  assert.deepEqual(parsed.lineage, lineage.body); assert.deepEqual(parsed.versions, versions.body.versions);
  assert.equal(parsed.filename, row.filename);
  const markdown = await downloaded(page, () => menu(page, "sessionMenu.exportMarkdown", true));
  assert.ok(markdown.body.includes(row.filename));
  return { artifactId: row.id, versionId: version, metadata: { filename: meta.filename, sha256: meta.sha256 }, markdown: { filename: markdown.filename, sha256: markdown.sha256 }, modelRequests: 0 };
}

export async function provenanceChecks(page, api) {
  const project = await api("/projects", { method: "POST", data: { name: "Provenance read boundaries" } });
  assert.equal(project.status, 200); const pid = project.body.project_id || project.body.id;
  const alternateName = `Prov alt ${Date.now().toString(36)}`;
  const alternate = await api("/projects", { method: "POST", data: { name: alternateName } });
  assert.equal(alternate.status, 200);
  const alternateId = alternate.body.project_id || alternate.body.id;
  const frames = [];
  for (let i = 0; i < 2; i++) {
    const made = await api("/frames", { method: "POST", data: { project_id: pid } });
    assert.equal(made.status, 200); frames.push(made.body.frame_id || made.body.id);
  }
  const [fid, other] = frames;
  const uploaded = await api("/uploads", { method: "POST", data: { frame_id: fid, filename: "unrecorded-input.txt", content_text: "Synthetic uploaded text\n" } });
  assert.equal(uploaded.status, 200);
  const produced = await api(`/frames/${other}/kernel/execute`, { method: "POST", data: {
    language: "python", wait: true, code: "from pathlib import Path\nPath('recorded-output.txt').write_text('Recorded output\\n')\nprint('provenance fixture')",
  } });
  assert.equal(produced.status, 200); assert.ok(!produced.body.error, JSON.stringify(produced.body));
  const upload = (await api(`/frames/${fid}/artifacts`)).body.find((a) => a.id === uploaded.body.artifact_id);
  const generated = (await api(`/frames/${other}/artifacts`)).body.find((a) => a.filename === "recorded-output.txt");
  assert.ok(upload.version_id && generated.version_id);
  const generatedLineage = await api(`/artifacts/${generated.id}/lineage?version=${generated.version_id}`);
  assert.equal(generatedLineage.status, 200);
  const producerId = generatedLineage.body.producer.producing_cell_id;
  assert.ok(producerId);
  await page.evaluate(() => window.showDashboard());
  await page.locator("#dash-projects .d-row").filter({ hasText: alternateName }).waitFor();
  await page.evaluate(({ fid, pid }) => window.openConversation(fid, pid), { fid, pid });
  const reads = [];
  const observer = (request) => { if (/\/(lineage|environment)(\?|$)/.test(request.url())) reads.push({ url: request.url(), method: request.method() }); };
  page.on("request", observer);
  try {
    const lineagePath = `**/api/v1/artifacts/${upload.id}/lineage*`;
    const malformed = (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"interactions":[],"dependency_mappings":{}}' });
    await page.route(lineagePath, malformed);
    try {
      await open(page, upload);
      await page.locator(".prov-body .prov-retry").waitFor();
      assert.equal(reads.filter((r) => r.url.includes("/lineage")).length, 1, "one click starts one lineage read");
      assert.doesNotMatch(await page.locator(".prov-body").textContent(), /Generating reproduction|No producing code|未包含生产代码/);
      // A repaint while failed cannot retry or convert the failure to absence.
      await page.evaluate(() => window.renderViewer()); await paintSettled(page);
      assert.equal(reads.length, 1);
    } finally { await page.unroute(lineagePath, malformed); }
    await page.locator(".prov-body .prov-retry").click();
    await waitUntil("confirmed missing source", async () => /No producing code|未包含生产代码/.test(await page.locator(".prov-body").textContent()));
    assert.equal(reads.filter((r) => r.url.includes("/lineage")).length, 2, "explicit retry is one read");
    await page.locator(".prov-subtab").filter({ hasText: /^Review$/ }).click();
    await waitUntil("recorded input boundary", async () => /No inputs were recorded|未记录输入/.test(await page.locator(".prov-body").textContent()));
    const envPath = `**/api/v1/artifacts/${upload.id}/environment*`;
    const badEnv = (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"packages":"invalid"}' });
    await page.route(envPath, badEnv);
    try {
      await page.locator(".prov-subtab").filter({ hasText: /^Environment$/ }).click();
      await page.locator(".prov-body .prov-retry").waitFor();
      await page.evaluate(() => window.renderViewer()); await paintSettled(page);
      assert.equal(reads.filter((r) => r.url.includes("/environment")).length, 1);
    } finally { await page.unroute(envPath, badEnv); }
    await page.locator(".prov-body .prov-retry").click();
    await waitUntil("confirmed missing environment", async () => /No environment was recorded|未记录生产时环境/.test(await page.locator(".prov-body").textContent()));
    assert.equal(await page.locator(".prov-body .prov-retry").count(), 0);
    assert.equal(reads.filter((r) => r.url.includes("/environment")).length, 2);
    assert.ok(reads.every((r) => r.method === "GET"));

    await failedExport(page, `**/api/v1/artifacts/${upload.id}/versions`, { error: "versions unavailable" }, () => menu(page, "menu.exportMetadata"), { status: 503 });
    await failedExport(page, lineagePath, {}, () => menu(page, "menu.exportMetadata"));
    await failedExport(page, `**/api/v1/frames/${fid}/artifacts`, { error: "artifacts unavailable" }, () => menu(page, "sessionMenu.exportMarkdown", true), { status: 503 });
    await failedExport(page, `**/api/v1/frames/${fid}/artifacts`, [null], () => menu(page, "sessionMenu.exportMarkdown", true));
    const metadata = await downloaded(page, () => menu(page, "menu.exportMetadata"));
    assert.equal(JSON.parse(metadata.body).version_id, upload.version_id);
    const markdown = await downloaded(page, () => menu(page, "sessionMenu.exportMarkdown", true));
    assert.ok(markdown.body.includes(upload.filename));

    // The project Files action may show another session's artifact while A
    // remains the current Notebook. It must not acquire A's Cell links/log.
    const heldLineage = await holdReads(page, (url) => url.pathname === `/api/v1/artifacts/${generated.id}/lineage`);
    try {
      await open(page, generated, "review");
      await waitUntil("lineage pending before sidebar navigation", () => heldLineage.captured.length === 1);
      await page.locator("#proj-btn").click();
      await page.locator("#proj-menu .proj-item").filter({ hasText: alternateName }).click();
      assert.equal(await page.evaluate(() => window.S.project), alternateId);
      assert.equal(await page.evaluate(() => window.S.currentId), fid);
    } finally { await heldLineage.finish(); }
    await waitUntil("other session lineage", async () => (await page.locator(".prov-card").count()) > 0);
    assert.equal(await page.evaluate(() => window.S.currentId), fid);
    assert.equal(await page.locator(".prov-link").count(), 0);
    assert.ok((await page.locator(".prov-body").textContent()).includes(other));
    await page.locator(".prov-subtab").filter({ hasText: /^Execution Log$/ }).click();
    await waitUntil("other Notebook is not attributed", async () => /belongs to another session|属于其他会话/.test(await page.locator(".prov-body").textContent())).catch(async (error) => { throw new Error(error.message + "; " + JSON.stringify(await page.evaluate(() => ({ body: document.querySelector(".prov-body")?.textContent, sub: window.S.provSub, current: window.S.currentId, artifact: window.S.dockArtifact })))); });
    await page.evaluate(({ fid, pid }) => window.openConversation(fid, pid), { fid: other, pid });
    await open(page, generated, "review");
    await page.locator(".prov-link").waitFor();
    await page.locator(".prov-link").click();
    await waitUntil("exact producer highlighted", async () => (await page.locator(".notebook-cell.flash").count()) === 1);
    assert.equal(await page.locator(".notebook-cell.flash").getAttribute("data-producing-cell"), producerId);
    const movingHead = await movingHeadExport(page, api, generated);
    return { artifactId: upload.id, versionId: upload.version_id, readonlyRetries: 2, failedExports: 4, exactProducer: producerId, movingHead };
  } finally { page.off("request", observer); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const playwright = await import("playwright");
  const base = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
  const source = process.env.OPENAI4S_PROVENANCE_EVIDENCE ? JSON.parse(fs.readFileSync(process.env.OPENAI4S_PROVENANCE_EVIDENCE, "utf8")) : null;
  const engines = (process.argv.find((arg) => arg.startsWith("--browser="))?.split("=")[1] || "chromium,firefox,webkit").split(",");
  for (const engine of engines) {
    const browser = await playwright[engine].launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    try {
      if (!await authenticate(page, base)) await page.goto(base);
      const api = async (path, { method = "GET", data } = {}) => {
        const response = await page.request.fetch(new URL(`/api/v1${path}`, base).toString(), { method, data });
        return { status: response.status(), body: await response.json() };
      };
      const result = source ? await realArtifactProvenanceCheck(page, api, source) : await provenanceChecks(page, api);
      console.log(JSON.stringify({ engine, ...result, success: true }));
    } finally { await browser.close(); }
  }
}
