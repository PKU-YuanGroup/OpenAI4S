// The workbench, in three engines, against a running daemon.
//
//   node tests/browser_matrix.mjs                       # all three
//   node tests/browser_matrix.mjs --browser=webkit      # one
//   OPENAI4S_BROWSER_URL=http://127.0.0.1:8791/ node tests/browser_matrix.mjs
//
// `browser_smoke.mjs` drives Chromium only, and Chromium is the engine least
// likely to surprise anyone: it is what the app is developed against. The
// defects a matrix finds are the ones a single engine cannot — a WebSocket
// close code Firefox reports differently, a fetch abort WebKit surfaces as a
// different error, a CSS or DOM API one engine has and another does not.
//
// Everything here talks to a real daemon over real HTTP and a real WebSocket.
// Controlled delivery/read faults exercise the real UI; successful rows and
// mutations come from the daemon, and DOM assertions observe its stored state.

const REQUESTED = (process.argv.find((a) => a.startsWith("--browser=")) || "").split("=")[1];
const ENGINES = REQUESTED ? [REQUESTED] : ["chromium", "firefox", "webkit"];
import { authenticate, redactSecrets } from "./browser_auth.mjs";
import { editorChecks } from "./browser_editor.mjs";
import { filesChecks } from "./browser_files.mjs";
import { navigationChecks } from "./browser_navigation.mjs";
import { provenanceChecks } from "./browser_provenance.mjs";

const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";

let playwright;
try {
  playwright = await import("playwright");
} catch (error) {
  const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
  if (!fallback) throw error;
  playwright = await import(fallback);
}

const results = [];

function record(engine, name, ok, rawDetail = "") {
  // Every detail is printed twice (here and in the FAILED summary) to CI
  // output, and an error message is free text: redact at the one sink.
  const detail = redactSecrets(rawDetail);
  results.push({ engine, name, ok, detail });
  const mark = ok ? "ok  " : "FAIL";
  console.log(`  [${mark}] ${engine.padEnd(9)} ${name}${detail ? ` — ${detail}` : ""}`);
}

async function check(engine, name, fn) {
  try {
    const detail = await fn();
    record(engine, name, true, detail || "");
  } catch (error) {
    record(engine, name, false, String(error && error.message ? error.message : error));
  }
}

async function waitUntil(predicate, timeoutMs = 15000, intervalMs = 80) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await predicate()) return true;
    if (Date.now() > deadline) return false;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

async function runEngine(engineName) {
  const launcher = playwright[engineName];
  if (!launcher) throw new Error(`playwright has no ${engineName}`);
  const browser = await launcher.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  const api = async (path, { method = "GET", data } = {}) => {
    const response = await page.request.fetch(new URL(`api/v1${path}`, baseUrl).toString(), {
      method,
      data,
      headers: data === undefined ? undefined : { "Content-Type": "application/json" },
    });
    return { status: response.status(), body: await response.json().catch(() => null) };
  };
  // The engine's own `fetch`, from inside the page. `page.request` is
  // Playwright's Node-side client sharing only the cookie jar -- it fires no
  // page request at all -- so a check that goes through it says nothing about
  // this engine's networking. The checks that claim engine coverage use this.
  const pageApi = async (path, { method = "GET", data } = {}) =>
    page.evaluate(
      async ([url, verb, body]) => {
        const response = await fetch(url, {
          method: verb,
          credentials: "same-origin",
          headers: body === null ? undefined : { "Content-Type": "application/json" },
          body: body === null ? undefined : JSON.stringify(body),
        });
        const text = await response.text();
        let parsed = null;
        try { parsed = JSON.parse(text); } catch {}
        return { status: response.status, body: parsed };
      },
      [new URL(`api/v1${path}`, baseUrl).toString(), method, data === undefined ? null : data],
    );
  // A frame id no session has. Each check below that is about *this* session
  // is also run against it and must fail there: a 400 before any lookup, a
  // 404 counted as "answered", or `200 []` counted as a projection all passed
  // for a session that does not exist, so the checks proved nothing.
  const UNKNOWN_FRAME = "f-matrix-no-such-session";
  const refusesUnknownFrame = async (probe) => {
    let passed = false;
    try {
      await probe(UNKNOWN_FRAME);
      passed = true;
    } catch {}
    if (passed) throw new Error(`the check also passes for ${UNKNOWN_FRAME}, so it proves nothing`);
  };

  try {
    // The gate is on by default; see browser_auth.mjs. `authenticate` performs a
    // real top-level navigation to /?token=… and lands on / after the 303, so
    // the app is already loaded when it returns — navigating again is not just
    // redundant, it ABORTS the in-flight startup fetches of the page it
    // replaces. Chromium and Firefox drop an aborted fetch quietly; WebKit
    // surfaces it as a page-level error ("Fetch API cannot load … due to access
    // control checks"), so this file failed its own "no uncaught page errors"
    // check on one engine out of three, for a request the app had handled and a
    // navigation this harness had caused. The listener above is attached before
    // authenticate deliberately — an error during the very first load is the
    // kind worth catching — so the fix is to stop creating the abort.
    const bootstrapped = await authenticate(page, baseUrl);
    if (!bootstrapped) {
      // No readable token file, so nothing navigated. The gate cannot be off
      // (0.3.0), so the checks below report the resulting 401s.
      await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    }

    // ---- the shell loads and its script actually ran --------------------
    await check(engineName, "app shell boots", async () => {
      const ready = await waitUntil(async () => page.evaluate(() => typeof window.t === "function" || typeof t === "function").catch(() => false));
      if (!ready) throw new Error("app.js never defined its globals");
      return `title=${await page.title()}`;
    });

    // ---- session: create one over the real API ---------------------------
    let frameId = null;
    await check(engineName, "session create", async () => {
      const projects = await api("/projects", { method: "POST", data: { name: `matrix-${engineName}` } });
      if (projects.status >= 400) throw new Error(`POST /projects → ${projects.status}`);
      const project = projects.body.project_id || projects.body.id;
      const frames = await api("/frames", { method: "POST", data: { project_id: project } });
      if (frames.status >= 400) throw new Error(`POST /frames → ${frames.status}`);
      frameId = frames.body.frame_id || frames.body.root_frame_id || frames.body.id;
      if (!frameId) throw new Error(`no frame id in ${JSON.stringify(frames.body).slice(0, 160)}`);
      return frameId;
    });

    // ---- websocket: the engine's own implementation ----------------------
    await check(engineName, "websocket connects and receives", async () => {
      const opened = await page.evaluate(
        ([base, frame]) =>
          new Promise((resolve) => {
            const url = new URL(`api/v1/ws?frame=${encodeURIComponent(frame)}`, base);
            url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
            const socket = new WebSocket(url.toString());
            const done = (value) => {
              try { socket.close(); } catch {}
              resolve(value);
            };
            socket.onopen = () => done("open");
            socket.onerror = () => done("error");
            setTimeout(() => done("timeout"), 8000);
          }),
        [baseUrl, frameId || "none"],
      );
      if (opened !== "open") throw new Error(`socket ${opened}`);
      return "open";
    });

    // ---- artifacts: the list renders through the real projection ---------
    // `200 []` is also what a frame that does not exist gets, so an empty list
    // is not evidence. Put one artifact into this session and require the
    // projection to return it, bound to this session.
    await check(engineName, "artifact projection", async () => {
      const probe = async (fid) => {
        const filename = `matrix-${engineName}.txt`;
        const uploaded = await api("/uploads", {
          method: "POST",
          data: { filename, content_text: `matrix ${engineName}\n`, frame_id: fid },
        });
        const artifactId = uploaded.body && (uploaded.body.artifact_id || uploaded.body.id);
        if (uploaded.status !== 200 || !artifactId) throw new Error(`POST /uploads → ${uploaded.status}`);
        const listed = await pageApi(`/frames/${encodeURIComponent(fid)}/artifacts`);
        if (listed.status !== 200) throw new Error(`GET artifacts → ${listed.status}`);
        if (!Array.isArray(listed.body)) throw new Error(`artifacts is not a list: ${JSON.stringify(listed.body).slice(0, 120)}`);
        const row = listed.body.find((item) => (item.artifact_id || item.id) === artifactId);
        if (!row) throw new Error(`the uploaded ${artifactId} is missing from ${listed.body.length} listed artifact(s)`);
        if (row.root_frame_id !== fid) throw new Error(`${artifactId} is bound to ${row.root_frame_id}, not ${fid}`);
        if (row.filename !== filename) throw new Error(`${artifactId} lists as ${row.filename}`);
        return `${listed.body.length} artifact(s), ${artifactId} listed`;
      };
      const detail = await probe(frameId);
      await refusesUnknownFrame(probe);
      return detail;
    });

    await check(engineName, "conditional editor survives races and lost responses", async () => {
      const result = await editorChecks(page, api, frameId);
      return `posts=${result.posts} version=${result.finalVersion}`;
    });

    await check(engineName, "Files cards, filters, pages and refresh agree", async () => {
      const result = await filesChecks(page, api);
      return `pages=${result.firstPages.join("/")} refreshed=${result.refreshed}`;
    });

    await check(engineName, "navigation owns sessions, folders and loading", async () => {
      const result = await navigationChecks(page, api);
      return `pages=${result.pages.join("/")} creates=${result.framePosts} cancels=${result.cancels}`;
    });

    await check(engineName, "provenance reads and exports preserve evidence", async () => {
      const result = await provenanceChecks(page, api);
      return `read retries=${result.readonlyRetries} refused exports=${result.failedExports} producer=${result.exactProducer}`;
    });

    // ---- consent: the privacy control, in this engine --------------------
    await check(engineName, "consent toggle serialises and reconciles", async () => {
      await api("/telemetry/consent", { method: "PUT", data: { enabled: false } });
      const outcome = await page.evaluate(async () => {
        const host = document.createElement("div");
        document.body.appendChild(host);
        await telemetryRow(host);
        const toggle = host.querySelector("button.toggle");
        const realFetch = window.fetch.bind(window);
        const isPut = (i, o) => String(i).includes("/telemetry/consent") && o && String(o.method).toUpperCase() === "PUT";
        let puts = 0;
        window.fetch = async (i, o) => {
          if (!isPut(i, o)) return realFetch(i, o);
          puts += 1;
          await new Promise((r) => setTimeout(r, 120));
          return realFetch(i, o);
        };
        toggle.click();
        toggle.click();
        toggle.click();
        await new Promise((r) => setTimeout(r, 2500));
        window.fetch = realFetch;
        const server = await realFetch("/api/v1/telemetry/consent").then((r) => r.json());
        return { puts, ui: toggle.classList.contains("on"), server: server.enabled };
      });
      if (outcome.ui !== outcome.server) throw new Error(`ui ${outcome.ui} != server ${outcome.server}`);
      if (outcome.puts > 3) throw new Error(`${outcome.puts} requests for 3 clicks — not serialised`);
      return `puts=${outcome.puts} state=${outcome.server}`;
    });

    await check(engineName, "consent rolls back a failed write", async () => {
      await api("/telemetry/consent", { method: "PUT", data: { enabled: false } });
      const outcome = await page.evaluate(async () => {
        const host = document.createElement("div");
        document.body.appendChild(host);
        await telemetryRow(host);
        const toggle = host.querySelector("button.toggle");
        const before = toggle.classList.contains("on");
        const realFetch = window.fetch.bind(window);
        window.fetch = (i, o) =>
          String(i).includes("/telemetry/consent") && o && String(o.method).toUpperCase() === "PUT"
            ? Promise.reject(new TypeError("network down"))
            : realFetch(i, o);
        toggle.click();
        await new Promise((r) => setTimeout(r, 1200));
        window.fetch = realFetch;
        const server = await realFetch("/api/v1/telemetry/consent").then((r) => r.json());
        return { before, after: toggle.classList.contains("on"), server: server.enabled };
      });
      if (outcome.after !== outcome.before) throw new Error("the control did not roll back");
      if (outcome.server !== outcome.before) throw new Error("the server changed after a failed write");
      return "rolled back";
    });

    // ---- cancel: a real running cell, stopped by its exact identity ------
    // This used to POST `{}` and pass on any status below 500. The route
    // rejects a missing identity with 400 before it looks the session up, so
    // every engine reported "HTTP 400" and cancel was never reached. Now a
    // real cell runs, is cancelled by `{execution_id, owner}`, must leave the
    // queue, and must be stored as interrupted.
    await check(engineName, "cancel stops a running cell", async () => {
      const snapshot = async (fid) => {
        const queue = await api(`/frames/${encodeURIComponent(fid)}/execution-queue`);
        if (queue.status !== 200) throw new Error(`GET execution-queue → ${queue.status}`);
        if (queue.body.root_frame_id !== fid) throw new Error(`execution-queue names ${queue.body.root_frame_id}`);
        return queue.body;
      };
      const probe = async (fid) => {
        // Precondition first, so the unknown-frame run fails here without
        // starting anything: `kernel/execute` does not itself refuse a frame
        // id no session has.
        const idle = await snapshot(fid);
        if (idle.owner) throw new Error(`the session is already owned by ${idle.owner.execution_id}`);
        const executionId = `matrix-cancel-${engineName}-${Date.now()}`;
        const code = `import time\n# ${executionId}\ntime.sleep(60)\n`;
        const accepted = await pageApi(`/frames/${encodeURIComponent(fid)}/kernel/execute`, {
          method: "POST",
          data: { execution_id: executionId, language: "python", code, wait: false },
        });
        if (accepted.status !== 202) throw new Error(`POST kernel/execute → ${accepted.status}`);
        const owner = accepted.body && accepted.body.owner;
        if (accepted.body.execution_id !== executionId || !owner || !owner.kind || !owner.id) {
          throw new Error(`execute accepted no addressable owner: ${JSON.stringify(accepted.body).slice(0, 160)}`);
        }
        // Cancel a *running* cell, not one still waiting for its worker.
        const running = await waitUntil(async () => {
          const current = (await snapshot(fid)).owner;
          if (!current || current.execution_id !== executionId || current.status !== "running") return false;
          const kernel = await api(`/frames/${encodeURIComponent(fid)}/kernel`);
          return kernel.status === 200 && kernel.body.alive === true;
        }, 60000, 250);
        if (!running) throw new Error(`${executionId} never ran with a live kernel`);
        await new Promise((r) => setTimeout(r, 750));

        const stale = await pageApi(`/frames/${encodeURIComponent(fid)}/cancel`, {
          method: "POST",
          data: { execution_id: `${executionId}-stale`, owner: { kind: owner.kind, id: `${owner.id}-stale` } },
        });
        if (stale.status !== 200 || stale.body.ok !== false) {
          throw new Error(`a stale identity was not refused: ${stale.status} ${JSON.stringify(stale.body).slice(0, 120)}`);
        }
        const stillRunning = (await snapshot(fid)).owner;
        if (!stillRunning || stillRunning.execution_id !== executionId) {
          throw new Error("a stale-identity cancel stopped the running cell");
        }

        const cancelled = await pageApi(`/frames/${encodeURIComponent(fid)}/cancel`, {
          method: "POST",
          data: { execution_id: executionId, owner, reason: "browser matrix" },
        });
        if (cancelled.status !== 200) throw new Error(`POST cancel → ${cancelled.status}`);
        const answer = cancelled.body;
        if (answer.ok !== true || answer.execution_id !== executionId || answer.root_frame_id !== fid) {
          throw new Error(`cancel did not take: ${JSON.stringify(answer).slice(0, 200)}`);
        }
        const released = await waitUntil(async () => {
          const current = (await snapshot(fid)).owner;
          return !current || current.execution_id !== executionId;
        }, 20000, 200);
        if (!released) throw new Error(`${executionId} still owns the session after cancel`);
        let stored = null;
        const recorded = await waitUntil(async () => {
          const log = await api(`/frames/${encodeURIComponent(fid)}/execution-log`);
          if (log.status !== 200) return false;
          stored = (log.body.entries || []).find((entry) => String(entry.source || "").includes(executionId));
          return Boolean(stored && stored.status && stored.status !== "running");
        }, 20000, 250);
        if (!recorded) throw new Error(`${executionId} has no finished execution-log row`);
        if (stored.status !== "interrupted") throw new Error(`${executionId} was stored as ${stored.status}`);
        return `${answer.scope}, interrupted=${answer.interrupted}, stored ${stored.status}`;
      };
      const detail = await probe(frameId);
      await refusesUnknownFrame(probe);
      return detail;
    });

    // ---- recovery: the projection a restart reads ------------------------
    // It passed on any status below 500, including the 404 an unknown session
    // gets. It has to be this session's projection.
    await check(engineName, "recovery projection", async () => {
      const probe = async (fid) => {
        const recovery = await pageApi(`/frames/${encodeURIComponent(fid)}/recovery`);
        if (recovery.status !== 200) throw new Error(`recovery → ${recovery.status}`);
        if (!recovery.body || recovery.body.root_frame_id !== fid) {
          throw new Error(`recovery names ${recovery.body && recovery.body.root_frame_id}, not ${fid}`);
        }
        if (typeof recovery.body.state !== "string" || !recovery.body.state) {
          throw new Error("recovery carries no state");
        }
        return `state=${recovery.body.state}`;
      };
      const detail = await probe(frameId);
      await refusesUnknownFrame(probe);
      return detail;
    });

    // ---- composer: typing + Enter is the only way to send ----------------
    // The other browser files send by calling window.send() from
    // page.evaluate, which is exactly how a workbench whose Enter handler was
    // never attached stayed green in every gate. This check presses the key
    // in each engine and watches for the POST the handler has to produce; it
    // runs last because the turn it starts has no model behind it here.
    await check(engineName, "composer Enter sends", async () => {
      const opened = await page.evaluate(async (fid) => {
        if (typeof openConversation !== "function") return "no openConversation";
        await openConversation(fid);
        return "ok";
      }, frameId);
      if (opened !== "ok") throw new Error(opened);
      const composer = page.locator("#composer");
      await composer.waitFor({ state: "visible", timeout: 15000 });
      let posted = 0;
      const onRequest = (request) => {
        if (request.method() === "POST" && request.url().includes(`/frames/${frameId}/message`)) posted += 1;
      };
      page.on("request", onRequest);
      try {
        await composer.fill("matrix enter");
        await composer.press("Enter");
        if (!(await waitUntil(() => posted > 0, 10000))) throw new Error("Enter did not POST /message");
        if (!(await waitUntil(async () => (await composer.inputValue()) === "", 10000))) {
          throw new Error("the composer was not cleared after Enter");
        }
        // One keystroke, one dispatch: a second POST here is the double-send.
        await new Promise((r) => setTimeout(r, 500));
        if (posted !== 1) throw new Error(`Enter produced ${posted} POST /message requests`);
      } finally {
        page.off("request", onRequest);
      }
      return `POST /message ×${posted}`;
    });

    await check(engineName, "no uncaught page errors", async () => {
      if (pageErrors.length) throw new Error(pageErrors.slice(0, 3).join(" | "));
      return "clean";
    });
  } finally {
    await browser.close();
  }
}

console.log(`browser matrix against ${baseUrl}`);
for (const engine of ENGINES) {
  try {
    await runEngine(engine);
  } catch (error) {
    record(engine, "engine launch", false, String(error && error.message ? error.message : error));
  }
}

const failed = results.filter((r) => !r.ok);
console.log(
  `\n${results.length - failed.length}/${results.length} checks passed across ${ENGINES.length} engine(s)`,
);
if (failed.length) {
  for (const item of failed) console.log(`  FAILED ${item.engine} ${item.name}: ${item.detail}`);
  process.exit(1);
}
