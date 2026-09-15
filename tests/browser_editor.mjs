// Conditional text editing through real controls and real daemon routes.
// Faults delay/lose transport responses; successful bodies always come from the daemon.
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { authenticate, waitUntil } from "./browser_auth.mjs";

export async function editorChecks(page, api, frameId, { artifact: provided } = {}) {
  let row = provided;
  if (!row) {
    const uploaded = await api("/uploads", { method: "POST", data: {
      frame_id: frameId, filename: "conditional-notes.txt", content_text: "Synthetic A: 7\n",
    } });
    assert.equal(uploaded.status, 200);
    row = { id: uploaded.body.artifact_id, filename: "conditional-notes.txt", content_type: "text/plain" };
  }
  const id = row.id;
  const versions = async () => {
    const result = await api(`/artifacts/${id}/versions`);
    assert.equal(result.status, 200);
    return result.body.versions;
  };
  const head = async () => (await versions()).find((version) => version.is_latest);
  const liveText = async () => {
    const response = await page.request.get(new URL(`/api/v1/artifacts/${id}`, page.url()).toString());
    assert.equal(response.status(), 200);
    return response.text();
  };
  const open = async () => {
    await page.evaluate(async ({ frameId, row }) => {
      await window.openConversation(frameId);
      window.openViewer({ ...row, version_id: undefined, latest_version_id: undefined, _exactVersion: false });
    }, { frameId, row });
  };
  const viewer = page.locator("#dock-viewer");
  const area = viewer.locator("textarea.edit-area");
  const save = viewer.locator(".edit-acts .solid-btn");
  const ready = () => waitUntil("editor ready", async () => await area.isEnabled() && await save.isEnabled());
  const edit = async () => {
    const label = await page.evaluate(() => window.t("common.edit"));
    await viewer.getByTitle(label, { exact: true }).click();
  };
  const discard = async (reload = true) => {
    page.once("dialog", (dialog) => dialog.accept());
    await viewer.locator(".edit-recovery button").nth(reload ? 3 : 4).click();
    if (reload) await ready();
  };
  let posts = 0;
  const observed = [];
  const onRequest = (request) => {
    if (request.method() === "POST" && request.url().endsWith(`/artifacts/${id}/edit`)) {
      posts += 1; observed.push(request.postDataJSON());
    }
  };
  page.on("request", onRequest);
  try {
    await open();
    // Freeze the original read mid-flight. Even invoking onclick directly must not write.
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let delayed = false;
    const textRoute = "**/api/v1/artifacts/versions/*";
    const pendingReads = [];
    const delay = (route) => {
      const work = (async () => { delayed = true; await gate; if (!page.isClosed()) await route.continue(); })();
      pendingReads.push(work);
      return work;
    };
    await page.route(textRoute, delay);
    try {
      await edit();
      await waitUntil("pending original read", () => delayed);
      assert.equal(await save.isDisabled(), true);
      await save.evaluate((button) => button.onclick?.(new MouseEvent("click")));
      assert.equal(posts, 0);
    } finally { release(); await Promise.all(pendingReads); await page.unroute(textRoute, delay); }
    await ready();
    const original = await area.inputValue();
    const baseline = (await head()).version_id;
    const edited = original + "Edited in the browser: 中文🙂\n";
    await area.fill(edited);
    // A normal conditional save must retain exact old bytes and add just one version.
    const before = await versions();
    await save.click();
    await waitUntil("successful save leaves editor", async () => await area.count() === 0);
    assert.equal(await liveText(), edited);
    assert.equal((await versions()).length, before.length + 1);
    assert.equal(observed[0].expected_version_id, baseline);
    const old = await page.request.get(new URL(`/api/v1/artifacts/versions/${baseline}`, page.url()).toString());
    assert.equal(await old.text(), original);
    await edit(); await ready();
    const currentBase = (await head()).version_id;
    const draft = edited + "Retained local draft\n";
    await area.fill(draft);
    // Real tab navigation followed by a Viewer refresh must restore the same draft.
    await page.locator("#dock-tabs .dock-tab").filter({ hasText: "Notebook" }).click();
    await page.locator("#dock-tabs .dock-tab").filter({ hasText: row.filename }).first().click();
    await ready();
    await page.evaluate(() => window.renderViewer());
    assert.equal(await area.inputValue(), draft);
    // Refusing a page refresh protects in-memory drafts across all tabs.
    const dialogSeen = page.waitForEvent("dialog", { timeout: 10000 });
    void page.evaluate(() => location.reload()).catch(() => {});
    const dialog = await dialogSeen;
    assert.equal(dialog.type(), "beforeunload");
    await dialog.dismiss();
    assert.equal(await area.inputValue(), draft);
    // A real external writer changes the head while this editor retains its old baseline.
    const external = await api(`/artifacts/${id}/edit`, { method: "POST", data: { content: "External version\n", expected_version_id: currentBase } });
    assert.equal(external.status, 200);
    await page.evaluate(() => window.renderViewer());
    assert.equal(await area.inputValue(), draft);
    const beforeConflict = await versions();
    await save.click();
    await waitUntil("conflict result and read-only reconciliation", async () =>
      (await viewer.locator(".edit-status").textContent()).includes("version") || (await viewer.locator(".edit-status").textContent()).includes("版本"));
    await waitUntil("checked version available", () => viewer.locator(".edit-recovery button").nth(1).isEnabled());
    assert.equal(await save.isDisabled(), true);
    assert.equal(await area.inputValue(), draft);
    assert.equal(observed.at(-1).expected_version_id, currentBase);
    assert.deepEqual(await versions(), beforeConflict);
    assert.equal(await liveText(), "External version\n");
    await viewer.locator(".edit-recovery button").nth(1).click();
    assert.equal(await area.count(), 0, "fixed historical tabs cannot render the editor");
    await page.locator("#dock-tabs .dock-tab").filter({ hasText: row.filename }).first().click();
    assert.equal(await area.inputValue(), draft);
    // Explicit discard is the only operation that replaces the baseline.
    await discard();
    assert.equal(await area.inputValue(), "External version\n");
    const lostContent = "Saved but response lost: 中文🙂\n";
    await area.fill(lostContent);
    const postsBeforeLoss = posts;
    const editRoute = `**/api/v1/artifacts/${id}/edit`;
    let committed = false;
    const lose = async (route) => {
      const response = await route.fetch();
      assert.equal(response.status(), 200);
      committed = true;
      await route.abort("failed");
    };
    await page.route(editRoute, lose);
    try {
      await save.click();
      await waitUntil("lost response reconciled", async () => committed && await viewer.locator(".edit-recovery button").nth(1).isEnabled());
      assert.equal(await area.inputValue(), lostContent);
      assert.equal(await liveText(), lostContent);
      assert.equal(await save.isDisabled(), true);
      await save.evaluate((button) => button.onclick?.(new MouseEvent("click")));
      await viewer.locator(".edit-recovery button").nth(2).click();
      await waitUntil("read check done", () => viewer.locator(".edit-recovery button").nth(2).isEnabled());
      assert.equal(posts, postsBeforeLoss + 1, "unknown saves never resend");
    } finally { await page.unroute(editRoute, lose); }
    await discard(false);
    assert.equal(await area.count(), 0);
    const finalVersion = (await head()).version_id;
    // A deleted source must not make its retained draft unreachable.
    const deleted = await api("/uploads", { method: "POST", data: {
      frame_id: frameId, filename: "deleted-draft.txt", content_text: "original",
    } });
    assert.equal(deleted.status, 200);
    const deletedId = deleted.body.artifact_id;
    await page.evaluate((id) => window.openViewer({ id, filename: "deleted-draft.txt", content_type: "text/plain" }), deletedId);
    await edit(); await ready();
    await area.fill("Recover even after source deletion");
    assert.equal((await api(`/artifacts/${deletedId}`, { method: "DELETE" })).status, 200);
    const otherProject = await api("/projects", { method: "POST", data: { name: "Draft recovery navigation" } });
    const otherFrame = await api("/frames", { method: "POST", data: { project_id: otherProject.body.project_id || otherProject.body.id } });
    assert.equal(otherFrame.status, 200);
    await page.evaluate((fid) => window.openConversation(fid), otherFrame.body.frame_id || otherFrame.body.id);
    await page.locator("#files-btn").click();
    const draftList = page.locator(".editor-drafts");
    await draftList.locator("summary").click();
    const deletedDraft = draftList.locator(".editor-draft").filter({ hasText: "deleted-draft.txt" });
    assert.equal(await deletedDraft.locator("textarea").inputValue(), "Recover even after source deletion");
    page.once("dialog", (dialog) => dialog.accept());
    await deletedDraft.locator("button").click();
    assert.equal(await draftList.locator(".editor-draft").count(), 0);
    // Capacity has an actual user-visible release path, without saving unwanted changes.
    await page.evaluate((fid) => window.openConversation(fid), frameId);
    for (let i = 0; i < 11; i++) {
      const filename = `capacity-${i}.txt`;
      const uploaded = await api("/uploads", { method: "POST", data: { frame_id: frameId, filename, content_text: "original" } });
      assert.equal(uploaded.status, 200);
      await page.evaluate(({ id, filename }) => window.openViewer({ id, filename, content_type: "text/plain" }), { id: uploaded.body.artifact_id, filename });
      await edit();
      if (i < 10) { await ready(); await area.fill(`retained draft ${i}`); }
      else { assert.equal(await save.isDisabled(), true); }
    }
    await page.locator("#files-btn").click();
    if (!await draftList.evaluate((node) => node.open)) await draftList.locator("summary").click();
    assert.equal(await draftList.locator(".editor-draft").count(), 10);
    page.once("dialog", (dialog) => dialog.accept());
    await draftList.locator(".editor-draft button").first().click();
    assert.equal(await draftList.locator(".editor-draft").count(), 9);
    await page.locator("#dock-tabs .dock-tab").filter({ hasText: "capacity-10.txt" }).click();
    await ready();
    await page.locator("#files-btn").click();
    assert.equal(await draftList.locator(".editor-draft").count(), 10);
    while (await draftList.locator(".editor-draft").count()) {
      page.once("dialog", (dialog) => dialog.accept());
      await draftList.locator(".editor-draft button").first().click();
    }
    return { artifactId: id, baseline, posts, finalVersion };
  } finally {
    page.off("request", onRequest);
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const playwright = await import("playwright");
  const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
  const engines = (process.argv.find((arg) => arg.startsWith("--browser="))?.split("=")[1] || "chromium,firefox,webkit").split(",");
  const source = process.env.OPENAI4S_EDITOR_EVIDENCE ? JSON.parse(fs.readFileSync(process.env.OPENAI4S_EDITOR_EVIDENCE, "utf8")) : null;
  for (const engine of engines) {
    const browser = await playwright[engine].launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    try {
      if (!await authenticate(page, baseUrl)) await page.goto(baseUrl);
      const api = async (path, { method = "GET", data } = {}) => {
        const response = await page.request.fetch(new URL(`/api/v1${path}`, baseUrl).toString(), { method, data });
        return { status: response.status(), body: await response.json() };
      };
      let frameId = source?.frame_id;
      if (!frameId) {
        const project = await api("/projects", { method: "POST", data: { name: `Editor ${engine}` } });
        const created = await api("/frames", { method: "POST", data: { project_id: project.body.project_id || project.body.id } });
        frameId = created.body.frame_id || created.body.id;
      }
      const result = await editorChecks(page, api, frameId, { artifact: source?.artifacts?.[0] });
      console.log(JSON.stringify({ engine, ...result, success: true }));
    } finally { await browser.close(); }
  }
}
