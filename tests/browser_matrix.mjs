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
// Nothing is stubbed: a DOM mock would test the mock, and the point of this
// file is the parts of the stack a mock replaces.

const REQUESTED = (process.argv.find((a) => a.startsWith("--browser=")) || "").split("=")[1];
const ENGINES = REQUESTED ? [REQUESTED] : ["chromium", "firefox", "webkit"];
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

function record(engine, name, ok, detail = "") {
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

  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 30000 });

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
    await check(engineName, "artifact projection", async () => {
      const listed = await api(`/frames/${frameId}/artifacts`);
      if (listed.status >= 400) throw new Error(`GET artifacts → ${listed.status}`);
      const rows = listed.body.artifacts ?? listed.body ?? [];
      return `${Array.isArray(rows) ? rows.length : 0} artifact(s)`;
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

    // ---- cancel: the real endpoint, on a session with nothing running ----
    await check(engineName, "cancel is answered", async () => {
      const cancelled = await api(`/frames/${frameId}/cancel`, { method: "POST", data: {} });
      if (cancelled.status >= 500) throw new Error(`cancel → ${cancelled.status}`);
      return `HTTP ${cancelled.status}`;
    });

    // ---- recovery: the projection a restart reads ------------------------
    await check(engineName, "recovery projection", async () => {
      const recovery = await api(`/frames/${frameId}/recovery`);
      if (recovery.status >= 500) throw new Error(`recovery → ${recovery.status}`);
      return `HTTP ${recovery.status}`;
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
