// Real local-folder project flow; no LLM or external service is contacted.
// Run against an isolated ./start.sh with OPENAI4S_NOTEBOOK_REPL=1 and science.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright";
import { authenticate, waitUntil } from "./browser_auth.mjs";

const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
const fixture = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "oai4s-folder-browser-")));
const source = path.join(fixture, "experiment");
fs.mkdirSync(path.join(source, "nested"), { recursive: true });
const csv = "group,value\na,2\na,4\nb,10\n";
fs.writeFileSync(path.join(source, "nested", "measurements.csv"), csv);
fs.writeFileSync(path.join(source, "notes.html"), "<script>window.projectPreviewExecuted=true</script>");
fs.writeFileSync(path.join(source, ".env"), "SYNTHETIC_SECRET=hidden\n");
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
let projectId;
let emptyProjectId;
const api = async (route, method = "GET", data) => {
  const response = await page.request.fetch(new URL(`/api/v1${route}`, baseUrl).toString(), {
    method, data, timeout: 90000,
  });
  const body = await response.json();
  assert.ok(response.ok(), `${method} ${route}: ${response.status()} ${JSON.stringify(body)}`);
  return body;
};

try {
  if (!await authenticate(page, baseUrl)) await page.goto(baseUrl);
  await page.waitForLoadState("networkidle");
  await page.locator("#dash-new-project").click();
  await page.locator("#pm-name").fill("Local folder browser test");
  await page.locator("#pm-folder").fill(fixture);
  await page.locator("#pm-browse").click();
  await page.locator("#pm-folder-picker .project-folder-entry").filter({ hasText: "experiment/" }).click();
  await page.locator("#pm-folder-picker .solid-btn").click();
  assert.equal(await page.locator("#pm-folder").inputValue(), source);
  const createdResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/projects");
  await page.locator("#pm-create").click();
  const created = await (await createdResponse).json();
  projectId = created.project_id;
  assert.equal(created.folder_path, source);
  await waitUntil("project session open", () => page.evaluate((pid) => window.S?.project === pid && !!window.S.currentId, projectId));
  const frameId = await page.evaluate(() => window.S.currentId);
  await page.locator("#proj-modal").waitFor({ state: "hidden" });
  assert.equal(await page.locator("#proj-folder-path").textContent(), source);

  await page.locator("#proj-btn").click();
  await page.locator("#proj-menu .proj-item").filter({ hasText: /Project files|项目文件/ }).click();
  await page.locator("#modal .project-folder-entry").filter({ hasText: "nested/" }).click();
  await page.locator("#modal .project-folder-entry").filter({ hasText: "measurements.csv" }).click();
  await page.locator(".project-file-preview").waitFor();
  assert.equal(await page.locator(".project-file-preview").textContent(), csv);
  await page.locator("#modal .project-folder-input").fill("");
  await page.locator("#modal .project-folder-input").press("Enter");
  await page.locator("#modal .project-folder-entry").filter({ hasText: "notes.html" }).click();
  await page.locator(".project-file-preview").waitFor();
  assert.ok((await page.locator(".project-file-preview").textContent()).includes("<script>"));
  assert.equal(await page.evaluate(() => window.projectPreviewExecuted), undefined);
  const listing = await api(`/projects/${projectId}/files`);
  assert.ok(!listing.entries.some((entry) => entry.name === ".env"));
  const denied = await page.request.get(new URL(`/api/v1/projects/${projectId}/file?path=../outside`, baseUrl).toString());
  assert.equal(denied.status(), 400);

  // Closing and reloading verifies persistent binding, rather than UI state.
  await page.reload();
  await waitUntil("session restored", () => page.evaluate((fid) => window.S?.currentId === fid, frameId));
  assert.equal((await api(`/projects/${projectId}`)).folder_path, source);
  const executed = await api(`/frames/${frameId}/kernel/execute`, "POST", {
    language: "python", wait: true,
    code: [
      "import pandas as pd",
      "from pathlib import Path",
      "item = host.project_import_file('nested/measurements.csv')",
      "data = pd.read_csv(item['path'])",
      "means = data.groupby('group')['value'].mean()",
      "assert means.to_dict() == {'a': 3.0, 'b': 10.0}",
      "means.to_csv('group-means.csv')",
      "print('PROJECT_FOLDER_ANALYSIS_OK')",
    ].join("\n"),
  });
  assert.ok(!executed.error, JSON.stringify(executed));
  const log = await api(`/frames/${frameId}/execution-log`);
  const cell = log.entries.find((entry) => entry.source.includes("PROJECT_FOLDER_ANALYSIS_OK"));
  assert.ok(cell && !cell.error, JSON.stringify(cell));
  assert.ok(JSON.stringify(cell).includes("PROJECT_FOLDER_ANALYSIS_OK"));
  const artifacts = await api(`/frames/${frameId}/artifacts`);
  const rows = artifacts.artifacts || artifacts;
  assert.ok(rows.some((row) => row.filename === "group-means.csv"));
  const imported = rows.find((row) => row.filename === "project-inputs/nested/measurements.csv");
  assert.ok(imported, "the input copy also has an immutable Artifact version");
  assert.equal(imported.checksum, createHash("sha256").update(csv).digest("hex"));
  assert.equal(fs.readFileSync(path.join(source, "nested", "measurements.csv"), "utf8"), csv);
  assert.equal(fs.existsSync(path.join(source, "group-means.csv")), false);

  // Durable cell/file completion should save without opening the history UI.
  let archived;
  await waitUntil("automatic project history", async () => {
    const history = await api(`/projects/${projectId}/history`);
    archived = history.sessions.find((row) => row.session_id === frameId && row.cell_count > 0 && row.file_count > 0);
    return history.status === "saved" && !!archived;
  });
  const firstRevision = archived.revision;
  const archivedPath = "workspace/group-means.csv";
  const downloadUrl = (pid, revision) => new URL(`/api/v1/projects/${pid}/history/${frameId}/file?revision=${revision}&path=${encodeURIComponent(archivedPath)}`, baseUrl).toString();
  const firstDownload = await page.request.get(downloadUrl(projectId, firstRevision));
  assert.ok(firstDownload.ok());
  const originalOutput = await firstDownload.body();
  assert.ok(originalOutput.toString().includes("a,3.0"));
  assert.ok(fs.existsSync(path.join(source, ".openai4s", "settings.json")));
  assert.ok(fs.existsSync(path.join(source, ".openai4s", "sessions", frameId, "transcript.md")));
  assert.equal(fs.readFileSync(path.join(source, ".openai4s", ".gitignore"), "utf8"), "*\n");
  const projectInputs = await api(`/projects/${projectId}/files`);
  assert.ok(!projectInputs.entries.some((entry) => entry.name === ".openai4s"));

  const changed = await api(`/frames/${frameId}/kernel/execute`, "POST", {
    language: "python", wait: true,
    code: "(means * 2).to_csv('group-means.csv')\nprint('HISTORY_SECOND_REVISION')",
  });
  assert.ok(!changed.error, JSON.stringify(changed));
  await page.locator("#proj-btn").click();
  await page.locator("#proj-menu .proj-item").filter({ hasText: /Local history|本地历史/ }).click();
  await page.locator("#project-history-save").click();
  await page.locator(".project-history-state.saved").waitFor();
  await page.locator(".project-history-session button").first().click();
  await page.locator(".project-history-revision").waitFor();
  const latestRevision = await page.locator(".project-history-revision").inputValue();
  assert.notEqual(latestRevision, firstRevision);
  await page.locator(".project-history-revision").selectOption(firstRevision);
  await waitUntil("historical revision selected", () => page.locator(".project-history-revision").inputValue().then((value) => value === firstRevision));
  await page.locator('.project-history-content [data-tab="cells"]').click();
  await waitUntil("historical cell shown", () => page.locator(".project-history-panel").textContent().then((value) => value.includes("PROJECT_FOLDER_ANALYSIS_OK") && !value.includes("HISTORY_SECOND_REVISION")));
  await page.locator('.project-history-content [data-tab="files"]').click();
  const outputRow = page.locator(".project-history-file").filter({ hasText: archivedPath });
  assert.equal(await outputRow.locator("a").count(), 1);
  const historicResponse = await page.request.get(new URL(await outputRow.locator("a").getAttribute("href"), baseUrl).toString());
  assert.deepEqual(await historicResponse.body(), originalOutput);
  if (process.env.OPENAI4S_BROWSER_SCREENSHOT) {
    await page.screenshot({ path: process.env.OPENAI4S_BROWSER_SCREENSHOT });
  }
  const latestDownload = await page.request.get(downloadUrl(projectId, latestRevision));
  assert.ok((await latestDownload.body()).toString().includes("a,6.0"));
  await page.reload();
  await waitUntil("conversation restored after history", () => page.evaluate((fid) => window.S?.currentId === fid, frameId));

  // A refused automatic session creation must keep both the old chat and its
  // folder selected; a sidebar-only switch would silently analyze A as B.
  const otherSource = path.join(fixture, "other-project");
  fs.mkdirSync(otherSource);
  const emptyProject = await api("/projects", "POST", { name: "Empty project failure test", folder_path: otherSource });
  emptyProjectId = emptyProject.project_id;
  await page.reload();
  await waitUntil("original session restored before failure test", () => page.evaluate((fid) => window.S?.currentId === fid, frameId));
  let refusedCreation = false;
  const creationRoute = "**/api/v1/frames";
  const refuseNewSession = async (route) => {
    if (route.request().method() === "POST" && route.request().postDataJSON()?.project_id === emptyProjectId) {
      refusedCreation = true;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "injected session creation failure" }) });
    } else await route.continue();
  };
  await page.route(creationRoute, refuseNewSession);
  await page.locator("#proj-btn").click();
  await page.locator("#proj-menu .proj-item").filter({ hasText: "Empty project failure test" }).click();
  await waitUntil("failed creation restored the original project", async () => refusedCreation && await page.evaluate(({ fid, pid }) => window.S?.currentId === fid && window.S.project === pid, { fid: frameId, pid: projectId }));
  assert.equal(await page.locator("#proj-folder-path").textContent(), source);
  await page.unroute(creationRoute, refuseNewSession);
  await api(`/projects/${emptyProjectId}`, "DELETE");
  emptyProjectId = null;

  await api(`/projects/${projectId}`, "DELETE");
  projectId = null;
  assert.equal(fs.readFileSync(path.join(source, "nested", "measurements.csv"), "utf8"), csv);
  assert.ok(fs.existsSync(path.join(source, ".openai4s", "sessions", frameId, "transcript.md")));
  const reopened = await api("/projects", "POST", { name: "Reopened local history", folder_path: source });
  projectId = reopened.project_id;
  await page.goto(new URL(`/projects/${projectId}`, baseUrl).toString());
  await waitUntil("relinked project opens", () => page.evaluate((pid) => window.S?.project === pid && !!window.S.currentId, projectId));
  const retained = await api(`/projects/${projectId}/history/${frameId}?revision=${firstRevision}`);
  assert.equal(retained.can_continue, false);
  assert.equal(retained.read_only, true);
  assert.deepEqual(await (await page.request.get(downloadUrl(projectId, firstRevision))).body(), originalOutput);
  await page.locator("#proj-btn").click();
  await page.locator("#proj-menu .proj-item").filter({ hasText: /Local history|本地历史/ }).click();
  await page.locator(".project-history-session").filter({ hasText: /[1-9][0-9]* (?:cells|个代码单元)/ }).first().locator("button").click();
  await page.locator(".project-history-revision").waitFor();
  assert.equal(await page.locator(".project-history-continue button").count(), 0);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ success: true, checks: ["folder picker", "project persistence", "nested preview", "secret and traversal refusal", "Python import and CSV analysis", "artifact output", "automatic local history", "history frontend and file versions", "failed project switch preserves context", "source and history survive project deletion", "relink preserves read-only archive"] }));
} catch (error) {
  console.error(JSON.stringify({
    folderPicker: await page.locator("#pm-folder-picker").textContent().catch(() => ""),
    historyErrors: await page.locator(".project-folder-error").allTextContents().catch(() => []),
    pageErrors: errors,
  }));
  throw error;
} finally {
  if (emptyProjectId) await api(`/projects/${emptyProjectId}`, "DELETE").catch(() => {});
  if (projectId) await api(`/projects/${projectId}`, "DELETE").catch(() => {});
  await browser.close();
  fs.rmSync(fixture, { recursive: true, force: true });
}
