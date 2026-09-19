// Real project/session/folder state, with controlled delivery order and read faults.
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { authenticate, waitUntil } from "./browser_auth.mjs";

async function holdResponse(page, match) {
  let release;
  let captured = false;
  let ready = false;
  let finished = false;
  let body;
  const token = `navigation-${Math.random()}`;
  let fault = null;
  const gate = new Promise((resolve) => { release = resolve; });
  const pending = [];
  const handler = (route) => {
    if (captured || !match(new URL(route.request().url()), route.request().method())) return route.fallback();
    captured = true;
    const work = (async () => {
      const response = await route.fetch();
      body = await response.body();
      ready = true;
      await gate;
      await route.fulfill({ ...(fault || { response, body }), headers: {
        ...response.headers(), "x-navigation-test-delivery": token,
        ...(fault ? { "content-type": "application/json" } : {}),
      } });
    })();
    pending.push(work);
    return work;
  };
  await page.route("**/api/v1/**", handler);
  return {
    waiting: () => ready,
    body: () => JSON.parse(body.toString()),
    async finish(value = null) {
      if (finished) return;
      finished = true;
      fault = value; release();
      await Promise.all(pending);
      await page.unroute("**/api/v1/**", handler);
      if (captured) {
        await page.waitForFunction((id) => window.__navigationReadObserver.seen.has(id), token);
        // The API's parsing, ownership checks and signal paints follow text().
        await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      }
    },
  };
}

export async function navigationChecks(page, api) {
  await page.evaluate(() => {
    const observer = { fetch: window.fetch, seen: new Set() };
    window.__navigationReadObserver = observer;
    window.fetch = async (...args) => {
      const response = await observer.fetch.apply(window, args);
      const token = response.headers.get("x-navigation-test-delivery");
      if (token) {
        const text = response.text.bind(response);
        response.text = async () => { const body = await text(); observer.seen.add(token); return body; };
      }
      return response;
    };
  });
  const tag = Date.now().toString(36);
  const projects = [];
  for (const letter of ["A", "B"]) {
    const name = `Nav ${letter} ${tag}`;
    const made = await api("/projects", { method: "POST", data: { name } });
    assert.equal(made.status, 200);
    const pid = made.body.project_id || made.body.id;
    const frames = [];
    for (let i = 0; i < 101; i++) {
      const result = await api("/frames", { method: "POST", data: { project_id: pid } });
      assert.equal(result.status, 200);
      const fid = result.body.id || result.body.frame_id;
      assert.equal((await api(`/frames/${fid}`, { method: "PATCH", data: { name: `${letter} session ${i}` } })).status, 200);
      frames.push(fid);
    }
    const folder = await api(`/projects/${pid}/folders`, { method: "POST", data: { name: `Folder ${letter}` } });
    assert.equal(folder.status, 200);
    projects.push({ pid, name, frames, folder: `Folder ${letter}` });
  }
  const [a, b] = projects;
  const upload = await api("/uploads", { method: "POST", data: { frame_id: a.frames[0], filename: "navigation-evidence.txt", content_text: "Retained frame Files snapshot" } });
  assert.equal(upload.status, 200);
  await page.evaluate(() => window.showDashboard());
  await page.locator("#dash-projects .d-row").filter({ hasText: b.name }).waitFor();
  const sessionRead = (p, cursor = false) => (url) => url.pathname === "/api/v1/frames" && url.searchParams.get("project_id") === p.pid && url.searchParams.has("cursor") === cursor;
  const select = async (p) => {
    await page.locator("#proj-btn").click();
    await page.locator("#proj-menu .proj-item").filter({ hasText: p.name }).click();
  };
  const rows = async (p, count) => {
    await waitUntil(`${p.name} owns ${count} sidebar rows`, async () => {
      const ids = await page.locator("#session-list .session").evaluateAll((nodes) => nodes.map((node) => node.dataset.frameId));
      return ids.length === count && new Set(ids).size === count && ids.every((id) => p.frames.includes(id)) &&
        await page.locator("#session-list .folder-name").allTextContents().then((names) => names.length === 1 && names[0] === p.folder);
    });
  };
  let framePosts = 0;
  let cancels = 0;
  const monitor = (request) => {
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/v1/frames") framePosts++;
    if (request.method() === "POST" && /\/(cancel|interrupt)$/.test(url.pathname)) cancels++;
  };
  page.on("request", monitor);
  try {
    await page.evaluate(({ fid, pid }) => window.openConversation(fid, pid), { fid: a.frames[0], pid: a.pid });
    await rows(a, 100);
    await page.locator("#files-btn").click();
    await page.locator('.files-scope [data-scope="frame"]').click();
    // This scene owns its preconditions even if a previous matrix check failed
    // before clearing the user's persistent Files filters.
    await page.locator(".files-filter-type").fill("");
    await page.locator(".files-filter-type").press("Tab");
    await page.locator('.files-origin [data-origin="all"]').click();
    await page.locator(".files-search").fill("navigation-evidence.txt");
    await page.locator(`.art[data-artifact-id="${upload.body.artifact_id}"]`).waitFor();
    // Real page-two responses arrive after B has acquired its own loading flag.
    const oldPage = await holdResponse(page, sessionRead(a, true));
    let newPage;
    try {
      await page.locator("#session-more").click();
      await waitUntil("A page two pending", oldPage.waiting);
      await select(b); await rows(b, 100);
      await page.locator(`.art[data-artifact-id="${upload.body.artifact_id}"]`).waitFor();
      newPage = await holdResponse(page, sessionRead(b, true));
      await page.locator("#session-more").click();
      await waitUntil("B page two pending", newPage.waiting);
      await oldPage.finish({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "injected late read failure" }) });
      await rows(b, 100);
      assert.equal(await page.locator("#session-more").isDisabled(), true);
      await newPage.finish(); newPage = null;
      await rows(b, 101);
    } finally { await oldPage.finish(); if (newPage) await newPage.finish(); }

    // ABA uses a persisted rename to distinguish the old real response from
    // the newer visit; matching the project ID alone would restore old bytes.
    const oldA = await holdResponse(page, sessionRead(a));
    try {
      await select(a); await waitUntil("first A read pending", oldA.waiting);
      assert.ok(oldA.body().frames.every((row) => row.name !== "Latest A visit"));
      await select(b); await rows(b, 100);
      const id = a.frames[100];
      assert.equal((await api(`/frames/${id}`, { method: "PATCH", data: { name: "Latest A visit" } })).status, 200);
      await select(a); await rows(a, 100);
      await page.locator(`.session[data-frame-id="${id}"] .s-name`).filter({ hasText: "Latest A visit" }).waitFor();
      await oldA.finish();
      await rows(a, 100);
      assert.equal(await page.locator(`.session[data-frame-id="${id}"] .s-name`).textContent(), "Latest A visit");
    } finally { await oldA.finish(); }

    // An old folder read's success cannot repaint after another project wins.
    const oldFolder = await holdResponse(page, (url) => url.pathname === `/api/v1/projects/${b.pid}/folders`);
    try {
      await select(b); await waitUntil("B folders pending", oldFolder.waiting);
      await select(a); await rows(a, 100);
      await oldFolder.finish(); await rows(a, 100);
    } finally { await oldFolder.finish(); }

    // Project opens use real dashboard clicks. A failed/malformed read must
    // show Retry, never infer an empty project and POST a new session.
    for (const fault of [
      { status: 503, contentType: "application/json", body: JSON.stringify({ error: "injected read failure" }) },
      { status: 200, contentType: "application/json", body: JSON.stringify({ unexpected: [] }) },
    ]) {
      await page.locator("#back-home").click();
      const held = await holdResponse(page, sessionRead(a));
      try {
        await page.locator("#dash-projects .d-row").filter({ hasText: a.name }).click();
        // 60s, not the 20s default: this polls for a request the click has
        // already triggered to reach the handler, and on a loaded runner the
        // whole navigation scene has been measured at 77s against 23.6s
        // nominal. A pending read that has not arrived in a minute is a real
        // failure; one that has not arrived in twenty seconds is a slow box.
        await waitUntil("project session read pending", held.waiting, 60000);
        await held.finish(fault);
        await page.locator('[data-read-error="sessions"]').waitFor();
        assert.equal(framePosts, 0);
        await page.locator('[data-read-error="sessions"] button').click();
        await rows(a, 100);
        assert.equal(await page.locator('[data-read-error="sessions"]').count(), 0);
      } finally { await held.finish(); }
    }
    assert.equal(framePosts, 0);
    // Browser history can open a project while a conversation is still on
    // screen. A failed directory read must give that retained frame fresh
    // history and Files reads under the new navigation generation.
    await page.evaluate(({ fid, pid }) => window.openConversation(fid, pid), { fid: a.frames[0], pid: a.pid });
    await page.locator("#files-btn").click();
    await page.locator('.files-scope [data-scope="frame"]').click();
    await page.locator(".files-search").fill("navigation-evidence.txt");
    await page.locator(`.art[data-artifact-id="${upload.body.artifact_id}"]`).waitFor();
    const oldHistory = await holdResponse(page, (url) => url.pathname === `/api/v1/frames/${a.frames[0]}/messages`);
    await page.evaluate(({ fid, pid }) => { void window.openConversation(fid, pid); }, { fid: a.frames[0], pid: a.pid });
    await waitUntil("retained conversation history pending", oldHistory.waiting);
    const failedProject = await holdResponse(page, sessionRead(b));
    const recoveredReads = new Set();
    const observeRetained = (response) => {
      const path = new URL(response.url()).pathname;
      if (response.status() === 200 && path.startsWith(`/api/v1/frames/${a.frames[0]}/`)) recoveredReads.add(path.split("/").at(-1));
    };
    page.on("response", observeRetained);
    try {
      await page.evaluate((pid) => {
        history.pushState(null, "", `/projects/${pid}`);
        window.dispatchEvent(new PopStateEvent("popstate"));
      }, b.pid);
      await waitUntil("history navigation directory pending", failedProject.waiting);
      recoveredReads.clear();
      await failedProject.finish({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "retained project directory failed" }) });
      await page.locator('[data-read-error="sessions"]').waitFor();
      await waitUntil("failed project open recovers retained history and Files", () => recoveredReads.has("messages") && recoveredReads.has("artifacts"));
      await page.locator(`.art[data-artifact-id="${upload.body.artifact_id}"]`).waitFor();
      await oldHistory.finish();
      assert.equal(await page.evaluate(() => S.currentId), a.frames[0]);
      assert.equal(await page.evaluate(() => S.project), b.pid);
      assert.equal(new URL(page.url()).pathname, `/projects/${b.pid}`);
      assert.equal(framePosts, 0);
    } finally {
      await failedProject.finish();
      await oldHistory.finish();
      page.off("response", observeRetained);
    }
    // A second deliberate New click must win even while the first accepted
    // frame is published but its directory read is still pending. Hold real
    // response bytes and exercise both completion orders through actual buttons.
    const creationRead = (url, method) => method === "POST" && url.pathname === "/api/v1/frames";
    for (const order of ["first-read", "second-creation"]) {
      await page.evaluate(({ fid, pid }) => window.openConversation(fid, pid), { fid: a.frames[0], pid: a.pid });
      const firstRead = await holdResponse(page, sessionRead(a));
      let secondCreation;
      try {
        await page.locator("#new-session").click();
        await waitUntil("first published creation awaiting directory", firstRead.waiting);
        const first = await page.evaluate(() => S.currentId);
        assert.notEqual(first, a.frames[0]);
        secondCreation = await holdResponse(page, creationRead);
        await page.locator("#tab-new").click();
        await waitUntil("second creation response held", secondCreation.waiting);
        const second = secondCreation.body().id;
        assert.ok(second && first !== second);
        if (order === "first-read") {
          await firstRead.finish();
          await secondCreation.finish();
        } else {
          await secondCreation.finish();
          await waitUntil("second creation opens before old directory", () => page.url().includes(`/frames/${second}`));
          await firstRead.finish();
        }
        await waitUntil("latest New intent owns the opened frame", async () =>
          await page.evaluate((fid) => S.currentId === fid, second) && page.url().includes(`/frames/${second}`));
        // Both accepted destinations remain durable; nothing was deleted or
        // silently retried to make the newer intent visible.
        for (const fid of [first, second]) assert.equal((await api(`/frames/${fid}`)).status, 200);
      } finally { await firstRead.finish(); if (secondCreation) await secondCreation.finish(); }
    }
    assert.equal(framePosts, 4);

    // Failed New leaves the previous frame usable. Its reads must recover
    // under the new generation; a stale Files snapshot cannot satisfy this.
    await page.evaluate(({ fid, pid }) => window.openConversation(fid, pid), { fid: a.frames[0], pid: a.pid });
    if (!await page.locator(".files-search").isVisible()) await page.locator("#files-btn").click();
    await page.locator(`.art[data-artifact-id="${upload.body.artifact_id}"]`).waitFor();
    // Sidebar B deliberately retains frame A and its real A address.
    await select(b); await rows(b, 100);
    await page.locator(`.art[data-artifact-id="${upload.body.artifact_id}"]`).waitFor();
    const retainedUrl = page.url();
    let refreshed = 0;
    const observeRecovery = (response) => {
      if (new URL(response.url()).pathname === `/api/v1/frames/${a.frames[0]}/artifacts` && response.status() === 200) refreshed++;
    };
    const rejectCreation = (route) => route.request().method() === "POST"
      ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "injected creation failure" }) })
      : route.fallback();
    page.on("response", observeRecovery);
    await page.route("**/api/v1/frames", rejectCreation);
    try {
      await page.locator("#new-session").click();
      await waitUntil("failed creation refreshes retained frame files", () => refreshed > 0);
      await page.locator(`.art[data-artifact-id="${upload.body.artifact_id}"]`).waitFor();
      assert.equal(await page.evaluate(() => S.currentId), a.frames[0]);
      await page.locator("#composer-hint").filter({ hasText: "injected creation failure" }).waitFor();
      assert.equal(page.url(), retainedUrl);
      assert.equal(await page.evaluate(() => S.project), b.pid);
      await rows(b, 100);
      assert.equal(framePosts, 5);
    } finally {
      await page.unroute("**/api/v1/frames", rejectCreation);
      page.off("response", observeRecovery);
    }
    assert.equal(cancels, 0);
    return { projects: projects.map((p) => p.pid), pages: [100, 101], framePosts, cancels, faults: 5, creationOrders: 2 };
  } finally {
    page.off("request", monitor);
    await page.evaluate(() => { window.fetch = window.__navigationReadObserver.fetch; delete window.__navigationReadObserver; });
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const playwright = await import("playwright");
  const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
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
      console.log(JSON.stringify({ engine, ...await navigationChecks(page, api), success: true }));
    } finally { await browser.close(); }
  }
}
