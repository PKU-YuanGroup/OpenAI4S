// Auto Mode status browser acceptance (issue #217).
//
// The read-only Auto Mode block in the session options menu and its Audit
// view, against a real gateway and real SQLite state, in English and Chinese.
//
// The harness owns everything it touches: a temporary data directory, two
// daemon lifetimes on random non-default loopback ports (Stage 2 off with an
// autonomous deployment preset, then Stage 2 on), a local LLM tripwire, and
// the seeded state -- which the production Store writes through
// tests/test_auto_mode_browser_fixture.py, with a real session export/import
// for the quarantined case. It sends no Agent message, blocks every browser
// request outside the daemon origin, and fails if any request the page made
// while the Auto Mode surface was in use was not a GET. Canonical WebSocket
// events are injected on the page's real socket (Playwright routeWebSocket)
// as refresh hints. Stdout carries exactly one record:
//   SUMMARY { ... }

import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { authenticate, boundedLogCollector, minimalChildEnvironment, redactSecrets } from "./browser_auth.mjs";

const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixturePath = path.join(workspaceRoot, "tests", "test_auto_mode_browser_fixture.py");
const pythonPath = process.env.OPENAI4S_PYTHON
  ? path.resolve(process.env.OPENAI4S_PYTHON)
  : path.join(workspaceRoot, ".venv", "bin", "python");
const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const dataPrefix = "openai4s-auto-mode-browser-";
const ownerMarker = ".auto-mode-browser-owner";
const SECRET = "SECRET-AUTO-MODE-FIELD";
const BANNED = /\bOn\b|\bEnabled\b|已开启/;

const COPY = {
  en: {
    storageOff: "Storage off",
    storageAvailable: "Storage available",
    imported: "Imported session, read only",
    worked: "Autonomous; result review Auto-fix; approvals Auto review of asks. Set by deployment.",
    savedFrame: "Autonomous; result review Auto-fix; approvals Auto review of asks. Saved on this conversation.",
    quarantined: "Off; result review Off; approvals You. Held at the safe default after import.",
    builtIn: "Off; result review Off; approvals You. Built-in default, no saved override.",
    legacy: "Off; result review Review only; approvals You. Inherited from Auto review.",
    project: "Off; result review Review only; approvals You. Saved on this project.",
    bumped: "Off; result review Review only; approvals You. Saved on this conversation.",
    noRun: "No Auto Run.",
    importedRun: "Finished. Unverified · imported history.",
    budgetRun: "In progress. Paused · Budget exhausted. This run: result review Auto-fix; approvals Auto review of asks.",
    measureRun: "In progress. 无法验证 token 预算. This run: result review Auto-fix; approvals Auto review of asks.",
    safetyRun: "Finished. Failed · Safety boundary. This run: result review Auto-fix; approvals Auto review of asks.",
    roundsRun:
      "In progress. Reviewing the candidate · not verified. This run: result review Auto-fix; approvals Auto review of asks. Review round 2 · repair round 1.",
    issuesRun: "Finished. Completed · unverified · 2 unresolved issues. This run: result review Review only; approvals You.",
    branchA: "In progress. Running · not verified. This run: result review Auto-fix; approvals Auto review of asks.",
    branchB: "In progress. Candidate · provisional / not verified. This run: result review Auto-fix; approvals Auto review of asks.",
    noUsage: "Deployment ceilings · No usage recorded",
    nearCells: "25 of 30, 5 remaining · Near ceiling",
    atReview: "2 of 2, 0 remaining · At ceiling",
    reservedRepair: "0 of 2, 1 remaining · 1 reserved",
    tokenNotFrozen: "Token ceiling not frozen",
    exhausted: "Exhausted: Reviewer attempts per candidate.",
    circuitBudget: "Circuit tripped · Paused · Budget exhausted",
    circuitMeasure: "Circuit tripped · 无法验证 token 预算",
    unavailable: "Status unavailable",
    notFound: "Session not found",
    loading: "Loading…",
    audit: "Audit",
    noAudits: "No audits",
    auditsUnavailable: "Audits unavailable",
  },
  zh: {
    storageOff: "存储未开启",
    storageAvailable: "存储可用",
    imported: "导入会话，只读",
    worked: "自主；结果审核 自动修复；审批 自动复核询问。由部署配置指定。",
    savedFrame: "自主；结果审核 自动修复；审批 自动复核询问。已保存在此会话。",
    quarantined: "关闭；结果审核 关闭；审批 由你。导入后固定为安全默认。",
    builtIn: "关闭；结果审核 关闭；审批 由你。内置默认，没有已保存的覆盖。",
    legacy: "关闭；结果审核 仅审核；审批 由你。继承自自动审核。",
    project: "关闭；结果审核 仅审核；审批 由你。已保存在此项目。",
    noRun: "没有自动运行。",
    importedRun: "已结束。未验证 · 导入的历史。",
    budgetRun: "进行中。Paused · Budget exhausted。本次运行：结果审核 自动修复；审批 自动复核询问。",
    measureRun: "进行中。无法验证 token 预算。本次运行：结果审核 自动修复；审批 自动复核询问。",
    safetyRun: "已结束。失败 · 安全边界。本次运行：结果审核 自动修复；审批 自动复核询问。",
    roundsRun: "进行中。正在审核候选 · 未验证。本次运行：结果审核 自动修复；审批 自动复核询问。审核第 2 轮 · 修复第 1 轮。",
    issuesRun: "已结束。已完成 · 未验证 · 2 个未解决的问题。本次运行：结果审核 仅审核；审批 由你。",
    noUsage: "部署上限 · 尚未记录用量",
    nearCells: "已用 25/30，剩余 5 · 接近上限",
    atReview: "已用 2/2，剩余 0 · 已到上限",
    reservedRepair: "已用 0/2，剩余 1 · 预留 1",
    tokenNotFrozen: "令牌上限尚未冻结",
    exhausted: "已耗尽：每个候选的审核尝试。",
    circuitBudget: "熔断已触发 · Paused · Budget exhausted",
    circuitMeasure: "熔断已触发 · 无法验证 token 预算",
    audit: "审计",
  },
};

const summary = {
  schema_version: 1,
  name: "auto_mode_status_browser_acceptance",
  phases: [],
  checks: {},
  requests: { auto_mode_gets: 0, audit_gets: 0, non_get_while_in_use: 0 },
  hints_injected: 0,
  agent_requests_sent: 0,
  live_llm_calls_observed: 0,
  external_network_calls: 0,
  cleanup: { daemons_stopped: false, data_dir_removed: false, tripwire_stopped: false, ok: false },
  failures: [],
  passed: false,
};

function assertion(condition, message) {
  if (!condition) throw new Error(message);
}

function check(name, condition, detail = "") {
  summary.checks[name] = !!condition;
  if (!condition) throw new Error(`${name}${detail ? `: ${detail}` : ""}`);
}

function sanitizeDiagnostic(value) {
  return redactSecrets(String(value == null ? "" : value).split(/\r?\n/, 1)[0])
    .replace(new RegExp(workspaceRoot.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"), "<repo>")
    .replace(/(?:\/Users|\/home|\/private\/var|\/var\/folders|[A-Za-z]:\\)[^\s:"']+/g, "<path>")
    .slice(0, 600);
}

function fail(message) {
  summary.failures.push(sanitizeDiagnostic(message));
}

function sameOrDescendant(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

function validateOwnedDataDir(dataDir, markerValue) {
  const resolved = fs.realpathSync(path.resolve(dataDir));
  const tempRoot = fs.realpathSync(os.tmpdir());
  if (!sameOrDescendant(resolved, tempRoot) || !path.basename(resolved).startsWith(dataPrefix)) {
    throw new Error("data directory is not an owned temporary root");
  }
  const home = path.resolve(os.homedir(), ".openai4s");
  if (sameOrDescendant(resolved, home)) throw new Error("the default OpenAI4S data directory is forbidden");
  const marker = path.join(resolved, ownerMarker);
  if (fs.readFileSync(marker, "utf8") !== markerValue) throw new Error("ownership marker changed");
  return resolved;
}

function createOwnedDataDir() {
  const markerValue = crypto.randomBytes(32).toString("hex");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), dataPrefix));
  fs.chmodSync(dataDir, 0o700);
  fs.writeFileSync(path.join(dataDir, ownerMarker), markerValue, { mode: 0o600, flag: "wx" });
  return { dataDir: validateOwnedDataDir(dataDir, markerValue), markerValue };
}

async function allocateLoopbackPort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = address && typeof address === "object" ? address.port : 0;
      server.close((error) => {
        if (error) reject(error);
        else if (!port || port === 8760) reject(new Error("could not allocate a safe loopback port"));
        else resolve(port);
      });
    });
  });
}

async function startTripwire() {
  let calls = 0;
  const server = http.createServer((_request, response) => {
    calls += 1;
    response.writeHead(503, { "content-type": "application/json" });
    response.end('{"error":"model calls are forbidden in the Auto Mode status acceptance"}');
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const port = address && typeof address === "object" ? address.port : 0;
  return { url: `http://127.0.0.1:${port}/v1`, count: () => calls, close: () => new Promise((resolve) => server.close(resolve)) };
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitUntil(label, operation, timeoutMs = 20000, intervalMs = 50) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    try {
      const value = await operation();
      if (value) return value;
    } catch (error) {
      last = error;
    }
    await sleep(intervalMs);
  }
  throw new Error(`${label} timed out${last ? `: ${last.message || last}` : ""}`);
}

async function waitForTcp(port) {
  return await new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    socket.setTimeout(500);
    socket.once("connect", () => { socket.destroy(); resolve(true); });
    socket.once("timeout", () => { socket.destroy(); resolve(false); });
    socket.once("error", () => resolve(false));
  });
}

async function stopChild(child, timeoutMs = 10000) {
  if (!child || child.exitCode !== null) return true;
  child.kill("SIGTERM");
  const exited = await Promise.race([
    new Promise((resolve) => child.once("exit", () => resolve(true))),
    sleep(timeoutMs).then(() => false),
  ]);
  if (!exited && child.exitCode === null) {
    child.kill("SIGKILL");
    await Promise.race([new Promise((resolve) => child.once("exit", resolve)), sleep(3000)]);
  }
  return child.exitCode !== null;
}

function runFixture(dataDir, scenario, rootFrameId, projectId) {
  const child = spawnSync(
    pythonPath,
    [fixturePath, "seed", "--data-dir", dataDir, "--scenario", scenario, "--root-frame-id", rootFrameId, "--project-id", projectId],
    { cwd: workspaceRoot, encoding: "utf8", env: minimalChildEnvironment({ OPENAI4S_DATA_DIR: dataDir }), maxBuffer: 4 * 1024 * 1024 },
  );
  if (child.error) throw child.error;
  if (child.status !== 0) throw new Error(`fixture ${scenario} failed: ${sanitizeDiagnostic(child.stderr)}`);
  const value = JSON.parse(child.stdout);
  assertion(value && value.schema_version === 1 && value.scenario === scenario, `fixture ${scenario} answered an unknown shape`);
  return value;
}

async function startDaemon(dataDir, port, tripwire, extra) {
  for (const name of ["daemon.json", "openai4s.pid"]) fs.rmSync(path.join(dataDir, name), { force: true });
  const env = minimalChildEnvironment({
    OPENAI4S_DATA_DIR: dataDir,
    OPENAI4S_HOST: "127.0.0.1",
    OPENAI4S_PORT: String(port),
    OPENAI4S_KERNEL_SANDBOX: "off",
    OPENAI4S_ALLOW_NETWORK: "0",
    OPENAI4S_LLM_BASE_URL: tripwire.url,
    OPENAI4S_DEEPSEEK_BASE_URL: tripwire.url,
    OPENAI4S_OPENAI_BASE_URL: tripwire.url,
    OPENAI4S_ANTHROPIC_BASE_URL: tripwire.url,
    OPENAI4S_GEMINI_BASE_URL: tripwire.url,
    ...extra,
  });
  const child = spawn(pythonPath, ["-m", "openai4s", "serve", "--no-browser", "--port", String(port)], {
    cwd: workspaceRoot,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const stdout = boundedLogCollector(child.stdout);
  const stderr = boundedLogCollector(child.stderr);
  await waitUntil("daemon startup", async () => {
    if (child.exitCode !== null) throw new Error(`daemon exited: ${sanitizeDiagnostic(stderr())}`);
    const ready = fs.existsSync(path.join(dataDir, "daemon.json")) && fs.existsSync(path.join(dataDir, "access-token"));
    return ready && (await waitForTcp(port));
  }, 45000, 100);
  const state = JSON.parse(fs.readFileSync(path.join(dataDir, "daemon.json"), "utf8"));
  assertion(state.pid === child.pid && Number(state.port) === port, "daemon state does not name the owned child");
  const token = fs.readFileSync(path.join(dataDir, "access-token"), "utf8").trim();
  return { child, token, logs: () => `${stdout()}\n${stderr()}` };
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch (error) {
    if (!process.env.OPENAI4S_PLAYWRIGHT_MODULE) throw error;
    return import(process.env.OPENAI4S_PLAYWRIGHT_MODULE);
  }
}

/** A browser context bound to one daemon: origin fence, socket handles, request log. */
async function openContext(browser, base, token, locale) {
  const context = await browser.newContext({ locale, serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
  const sockets = [];
  await context.route("**/*", async (route) => {
    let destination;
    try { destination = new URL(route.request().url()); } catch {
      summary.external_network_calls += 1;
      await route.abort("blockedbyclient").catch(() => {});
      return;
    }
    const local = ["data:", "blob:", "about:"].includes(destination.protocol);
    if (!local && destination.origin !== base.origin) {
      summary.external_network_calls += 1;
      await route.abort("blockedbyclient").catch(() => {});
      return;
    }
    // A navigation can settle a request this handler is still looking at;
    // that is not a failure of the page under test.
    await route.continue().catch(() => {});
  });
  await context.routeWebSocket(/.*/, (ws) => {
    let target;
    try { target = new URL(ws.url()); } catch { target = null; }
    if (!target || target.host !== base.host) {
      summary.external_network_calls += 1;
      ws.close({ code: 1008, reason: "blocked" });
      return;
    }
    const server = ws.connectToServer();
    const entry = { ws, views: [] };
    // Watching the page's own messages stops their automatic forwarding, so
    // each one is passed on. `view_session` is the last step of the
    // workbench's openConversation: the conversation is fully open.
    ws.onMessage((message) => {
      try {
        const parsed = JSON.parse(String(message));
        if (parsed && parsed.type === "view_session") entry.views.push(parsed.root_frame_id);
      } catch {
        /* pings and other frames */
      }
      server.send(message);
    });
    sockets.push(entry);
    ws.onClose(() => {
      const at = sockets.indexOf(entry);
      if (at >= 0) sockets.splice(at, 1);
    });
  });
  const page = await context.newPage();
  const requests = [];
  page.on("request", (request) => {
    let url;
    try { url = new URL(request.url()); } catch { return; }
    if (url.origin !== base.origin || !url.pathname.startsWith("/api/")) return;
    const method = request.method();
    requests.push({ method, path: url.pathname + url.search });
    if (method === "POST" && /\/frames\/[^/]+\/message$/.test(url.pathname)) summary.agent_requests_sent += 1;
  });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(sanitizeDiagnostic(error?.message || error)));
  await authenticate(page, base.toString(), token);
  return { context, page, sockets, requests, pageErrors };
}

async function apiCall(context, base, method, apiPath, data, headers) {
  const response = await context.request.fetch(new URL(apiPath, base).toString(), {
    method,
    data,
    headers: headers || (data === undefined ? undefined : { "content-type": "application/json" }),
    maxRedirects: 0,
  });
  const bytes = Buffer.from(await response.body());
  let body = null;
  try { body = JSON.parse(bytes.toString("utf8")); } catch {}
  return { status: response.status(), bytes, body };
}

async function createProject(context, base, name) {
  const project = await apiCall(context, base, "POST", "/api/v1/projects", { name });
  const id = project.body?.project_id || project.body?.id;
  assertion(project.status < 300 && id, `could not create project ${name}`);
  return id;
}

async function createFrame(context, base, projectId) {
  const frame = await apiCall(context, base, "POST", "/api/v1/frames", { project_id: projectId });
  const id = frame.body?.frame_id || frame.body?.id;
  assertion(frame.status < 300 && id, "could not create a session");
  return id;
}

/**
 * Wait for the document's own socket to subscribe to `frameId`. The previous
 * document's socket can outlive a navigation for a moment and has often viewed
 * the same conversation, so only a socket opened after `before` counts.
 */
async function waitSubscribed(handle, frameId, before) {
  await waitUntil(
    "conversation subscribed",
    async () => handle.sockets.some((entry) => !before.has(entry) && entry.views.includes(frameId)),
    30000,
  );
}

async function openConversation(handle, base, projectId, frameId) {
  const before = new Set(handle.sockets);
  const url = new URL(`projects/${encodeURIComponent(projectId)}/frames/${encodeURIComponent(frameId)}`, base).toString();
  const response = await handle.page.goto(url, { waitUntil: "domcontentloaded" });
  assertion(response && response.ok(), "workspace deep link did not load");
  await handle.page.locator("#workspace:not(.hidden)").waitFor({ state: "visible", timeout: 30000 });
  await handle.page.locator("#session-options-btn").waitFor({ state: "visible", timeout: 30000 });
  await waitSubscribed(handle, frameId, before);
}

function block(page) {
  return page.locator(".ctx-menu .am-status");
}

async function openStatus(handle, phase = /^(ready|failed)$/) {
  const { page } = handle;
  await page.keyboard.press("Escape").catch(() => {});
  await page.locator("#session-options-btn").click();
  const status = block(page);
  await status.waitFor({ state: "attached", timeout: 15000 });
  await waitUntil("status block phase", async () => phase.test((await status.getAttribute("data-phase")) || ""), 15000);
  return status;
}

async function lineValue(page, kind) {
  return ((await block(page).locator(`.am-line[data-line="${kind}"] .am-v`).textContent()) || "").trim();
}

async function lines(page) {
  return {
    availability: await lineValue(page, "availability"),
    selection: await lineValue(page, "selection"),
    run: await lineValue(page, "run"),
  };
}

async function closeStatus(page) {
  await page.keyboard.press("Escape");
  await block(page).waitFor({ state: "detached", timeout: 5000 });
}

function inject(handle, event) {
  const socket = handle.sockets[handle.sockets.length - 1];
  assertion(socket, "no live WebSocket to inject a hint on");
  socket.ws.send(JSON.stringify(event));
  summary.hints_injected += 1;
}

/**
 * Hold the next matching request: the server answers it now, the page sees
 * that answer only on `release()`. Later matches pass straight through.
 *
 * The handler is removed only after it has fulfilled. `page.unroute()` while
 * a handler still owns a route hands that route on to the next handler, so
 * the "held" request completed at once -- a hold that held nothing.
 */
async function holdNext(page, match) {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let seen;
  const observed = new Promise((resolve) => { seen = resolve; });
  let taken = false;
  let delivered = false;
  const handler = async (route) => {
    if (taken) {
      await route.fallback().catch(() => {});
      return;
    }
    taken = true;
    const response = await route.fetch();
    seen();
    await gate;
    await route.fulfill({ response }).catch(() => {});
    delivered = true;
    await page.unroute(match, handler).catch(() => {});
  };
  await page.route(match, handler);
  return { observed, release, delivered: () => delivered };
}

// One matcher function per path: `page.unroute(url, handler)` matches the URL
// argument by identity, so a fresh closure per call never unroutes anything
// and a fault injected for one check silently stays in place for the next.
const matchers = new Map();

function matcher(pathname) {
  if (!matchers.has(pathname)) matchers.set(pathname, (url) => url.pathname === pathname);
  return matchers.get(pathname);
}

/**
 * Intercept every matching request until `stop()`. For each one the test
 * decides when the server answers it (`fetch()`, a snapshot of SQLite at that
 * moment) and, separately, when the page receives that answer (`deliver()`).
 * That is how two overlapping reads are made to cross: the server serves them
 * in one order and the page sees them in the other.
 */
async function intercept(page, match) {
  const entries = [];
  const handler = async (route) => {
    const entry = { route, response: null, delivered: false };
    entry.fetch = async () => {
      entry.response = await route.fetch();
      return entry.response;
    };
    entry.deliver = async () => {
      await route.fulfill({ response: entry.response }).catch(() => {});
      entry.delivered = true;
    };
    entries.push(entry);
  };
  await page.route(match, handler);
  return {
    entries,
    next: (index) => waitUntil(`intercepted request ${index}`, async () => entries[index] || null, 15000, 25),
    stop: () => page.unroute(match, handler).catch(() => {}),
  };
}

function statusPath(frameId) {
  return matcher(`/api/v1/frames/${frameId}/auto-mode`);
}

function auditPath(frameId) {
  return matcher(`/api/v1/frames/${frameId}/auto-audits`);
}

/** Everything the page asked the daemon for since `mark`; only GETs are allowed. */
function assertReadOnly(handle, mark, label) {
  const writes = handle.requests.slice(mark).filter((item) => item.method !== "GET");
  summary.requests.non_get_while_in_use += writes.length;
  check(`${label}: no write`, writes.length === 0, JSON.stringify(writes.slice(0, 3)));
}

function countReads(handle, mark) {
  const slice = handle.requests.slice(mark);
  summary.requests.auto_mode_gets += slice.filter((item) => /\/auto-mode$/.test(item.path)).length;
  summary.requests.audit_gets += slice.filter((item) => /\/auto-audits\?/.test(item.path)).length;
}

async function staticChecks(handle, base, lang, frames, phase) {
  const copy = COPY[lang];
  const { page } = handle;
  const mark = handle.requests.length;
  for (const item of frames) {
    await openConversation(handle, base, item.projectId, item.frameId);
    const status = await openStatus(handle);
    const shown = await lines(page);
    const prefix = `${phase}/${lang}/${item.name}`;
    for (const [key, expected] of Object.entries(item.expect(copy))) {
      if (key === "budget") continue;
      check(`${prefix}/${key}`, shown[key] === expected, `${JSON.stringify(shown[key])} != ${JSON.stringify(expected)}`);
    }
    const text = (await status.textContent()) || "";
    check(`${prefix}/no On or 已开启`, !BANNED.test(text), text.slice(0, 200));
    const budget = item.expect(copy).budget;
    if (budget) await budget(status, `${prefix}/budget`);
    await closeStatus(page);
  }
  assertReadOnly(handle, mark, `${phase}/${lang} static`);
  countReads(handle, mark);
}

async function budgetText(status) {
  return ((await status.locator(".am-budget").textContent()) || "").replace(/\s+/g, " ");
}

async function meterText(status, field) {
  return ((await status.locator(`.am-meter[data-field="${field}"] .am-mv`).textContent()) || "").trim();
}

async function phaseA(browser, owned, tripwire) {
  const port = await allocateLoopbackPort();
  const base = new URL(`http://127.0.0.1:${port}/`);
  const daemon = await startDaemon(owned.dataDir, port, tripwire, { OPENAI4S_AUTO_MODE: "autonomous" });
  summary.phases.push({ name: "stage2_off_deployment_autonomous", started: true });
  const handles = [];
  try {
    const setup = await openContext(browser, base, daemon.token, "en-US");
    handles.push(setup);
    const projectId = await createProject(setup.context, base, "auto-mode-status-a");
    const worked = await createFrame(setup.context, base, projectId);
    const saved = await createFrame(setup.context, base, projectId);
    const source = await createFrame(setup.context, base, projectId);
    runFixture(owned.dataDir, "frame_autonomous", saved, projectId);
    runFixture(owned.dataDir, "source_for_import", source, projectId);
    const exported = await apiCall(setup.context, base, "GET", `/api/v1/frames/${source}/session/export`);
    check("A/export", exported.status === 200 && exported.bytes.length > 0, `HTTP ${exported.status}`);
    const imported = await apiCall(setup.context, base, "POST", "/api/v1/sessions/import", exported.bytes, {
      "content-type": "application/vnd.openai4s.session+zip",
    });
    check("A/import quarantined", imported.status === 201 && imported.body?.trust_state === "quarantined", `HTTP ${imported.status}`);
    const frames = [
      {
        name: "worked_example",
        projectId,
        frameId: worked,
        expect: (c) => ({
          availability: c.storageOff,
          selection: c.worked,
          run: c.noRun,
          budget: async (status, label) => {
            check(`${label}/no usage`, (await status.locator(".am-budget-table > summary").textContent())?.trim() === c.noUsage);
            check(`${label}/closed`, (await status.locator(".am-budget-table").getAttribute("open")) === null);
            check(`${label}/no alerts`, (await status.locator(".am-alerts").count()) === 0);
          },
        }),
      },
      { name: "saved_frame_autonomous", projectId, frameId: saved, expect: (c) => ({ availability: c.storageOff, selection: c.savedFrame, run: c.noRun }) },
      {
        name: "quarantined_import",
        projectId: imported.body.project_id,
        frameId: imported.body.root_frame_id,
        expect: (c) => ({ availability: c.imported, selection: c.quarantined, run: c.importedRun }),
      },
    ];
    for (const [lang, locale] of [["en", "en-US"], ["zh", "zh-CN"]]) {
      const handle = lang === "en" ? setup : await openContext(browser, base, daemon.token, locale);
      if (lang !== "en") handles.push(handle);
      await staticChecks(handle, base, lang, frames, "A");
    }
    // The legacy row is still there, by its own name, above the block.
    await openConversation(setup, base, projectId, worked);
    await openStatus(setup);
    const items = await setup.page.locator(".ctx-menu > .ctx-item").allTextContents();
    check("A/legacy Auto review row kept", items.some((text) => text.includes("Auto-review")), JSON.stringify(items));
    await closeStatus(setup.page);
    for (const handle of handles) check(`A/page errors (${handle.pageErrors.length})`, handle.pageErrors.length === 0, handle.pageErrors.join(" | "));
    return { worked, projectId };
  } finally {
    for (const handle of handles) await handle.context.close().catch(() => {});
    const stopped = await stopChild(daemon.child);
    summary.phases[summary.phases.length - 1].stopped = stopped;
    if (!stopped) throw new Error("phase A daemon did not stop");
  }
}

async function phaseB(browser, owned, tripwire, previous) {
  const port = await allocateLoopbackPort();
  const base = new URL(`http://127.0.0.1:${port}/`);
  const daemon = await startDaemon(owned.dataDir, port, tripwire, { OPENAI4S_STAGE2_AUTO_RUN_STORAGE: "1" });
  summary.phases.push({ name: "stage2_on", started: true });
  const handles = [];
  try {
    const setup = await openContext(browser, base, daemon.token, "en-US");
    handles.push(setup);
    const { context } = setup;
    const projectId = await createProject(context, base, "auto-mode-status-b");
    const otherProject = await createProject(context, base, "auto-mode-status-b-project");
    const id = {
      legacy: await createFrame(context, base, projectId),
      project: await createFrame(context, base, otherProject),
      budget: await createFrame(context, base, projectId),
      measure: await createFrame(context, base, projectId),
      safety: await createFrame(context, base, projectId),
      rounds: await createFrame(context, base, projectId),
      issues: await createFrame(context, base, projectId),
      audits: await createFrame(context, base, projectId),
      branch: await createFrame(context, base, projectId),
      bump: await createFrame(context, base, projectId),
    };
    runFixture(owned.dataDir, "legacy_review", id.legacy, projectId);
    runFixture(owned.dataDir, "project_review_only", id.project, otherProject);
    runFixture(owned.dataDir, "budget_meters", id.budget, projectId);
    runFixture(owned.dataDir, "measurement_unavailable", id.measure, projectId);
    runFixture(owned.dataDir, "terminal_safety", id.safety, projectId);
    runFixture(owned.dataDir, "run_rounds", id.rounds, projectId);
    runFixture(owned.dataDir, "completed_with_issues", id.issues, projectId);
    runFixture(owned.dataDir, "audits", id.audits, projectId);
    runFixture(owned.dataDir, "branch_runs", id.branch, projectId);
    runFixture(owned.dataDir, "frame_autonomous", id.bump, projectId);

    const budgetChecks = (c) => async (status, label) => {
      check(`${label}/table stays closed`, (await status.locator(".am-budget-table").getAttribute("open")) === null);
      check(`${label}/warnings shown without opening`, await status.locator('.am-alert[data-field="max_extra_cells"][data-flag="near"]').isVisible()
        && await status.locator('.am-alert[data-field="max_review_rounds"][data-flag="at"]').isVisible());
      check(`${label}/audit above the ceilings`, await status.locator(".am-actions + .am-budget").count() === 1);
      check(`${label}/near`, (await meterText(status, "max_extra_cells")) === c.nearCells, await meterText(status, "max_extra_cells"));
      check(`${label}/at`, (await meterText(status, "max_review_rounds")) === c.atReview, await meterText(status, "max_review_rounds"));
      check(`${label}/reserved`, (await meterText(status, "max_repair_rounds")) === c.reservedRepair, await meterText(status, "max_repair_rounds"));
      check(`${label}/token`, (await meterText(status, "extra_token_multiplier")) === c.tokenNotFrozen);
      const text = await budgetText(status);
      check(`${label}/exhausted names`, text.includes(c.exhausted), text.slice(0, 300));
      check(`${label}/circuit`, text.includes(c.circuitBudget), text.slice(0, 300));
    };
    const frames = [
      { name: "worked_example_stage2_on", projectId: previous.projectId, frameId: previous.worked, expect: (c) => ({ availability: c.storageAvailable, selection: c.builtIn, run: c.noRun }) },
      { name: "legacy", projectId, frameId: id.legacy, expect: (c) => ({ availability: c.storageAvailable, selection: c.legacy, run: c.noRun }) },
      { name: "project", projectId: otherProject, frameId: id.project, expect: (c) => ({ selection: c.project, run: c.noRun }) },
      { name: "budget", projectId, frameId: id.budget, expect: (c) => ({ run: c.budgetRun, budget: budgetChecks(c) }) },
      {
        name: "measurement_unavailable",
        projectId,
        frameId: id.measure,
        expect: (c) => ({
          run: c.measureRun,
          budget: async (status, label) => check(`${label}/server string`, (await budgetText(status)).includes(c.circuitMeasure)),
        }),
      },
      { name: "safety_boundary", projectId, frameId: id.safety, expect: (c) => ({ run: c.safetyRun }) },
      { name: "run_rounds", projectId, frameId: id.rounds, expect: (c) => ({ run: c.roundsRun }) },
      { name: "completed_with_issues", projectId, frameId: id.issues, expect: (c) => ({ run: c.issuesRun }) },
    ];
    for (const [lang, locale] of [["en", "en-US"], ["zh", "zh-CN"]]) {
      const handle = lang === "en" ? setup : await openContext(browser, base, daemon.token, locale);
      if (lang !== "en") handles.push(handle);
      await staticChecks(handle, base, lang, frames, "B");
    }

    const handle = setup;
    const { page } = handle;
    const c = COPY.en;
    const mark = handle.requests.length;

    // Loading, then a failed read, an explicit retry, an unknown schema and 404.
    await openConversation(handle, base, previous.projectId, previous.worked);
    const clickMark = handle.requests.length;
    const slow = await holdNext(page, statusPath(previous.worked));
    await page.locator("#session-options-btn").click();
    await slow.observed;
    await block(page).waitFor({ state: "attached" });
    const loadingRun = await lineValue(page, "run");
    check("B/loading", loadingRun === c.loading, `${JSON.stringify(loadingRun)} phase=${await block(page).getAttribute("data-phase")} requests=${JSON.stringify(handle.requests.slice(clickMark).map((item) => item.method + " " + item.path.replace(/f-[0-9a-f]+/g, "F")))}`);
    slow.release();
    await waitUntil("loaded", async () => (await block(page).getAttribute("data-phase")) === "ready");
    check("B/held read delivered", slow.delivered());
    await closeStatus(page);

    let failures = 1;
    const flaky = async (route) => {
      if (failures > 0) {
        failures -= 1;
        await route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"x","code":"auto_mode_storage_unavailable"}' });
        return;
      }
      await route.fallback().catch(() => {});
    };
    await page.route(statusPath(previous.worked), flaky);
    await openStatus(handle);
    const failed = await lines(page);
    check("B/503 three lines", failed.availability === c.unavailable && failed.selection === c.unavailable && failed.run === c.unavailable, JSON.stringify(failed));
    await block(page).locator(".am-retry").click();
    await waitUntil("retried", async () => (await block(page).getAttribute("data-phase")) === "ready");
    check("B/retry recovers", (await lineValue(page, "availability")) === c.storageAvailable);
    await page.unroute(statusPath(previous.worked), flaky);
    await closeStatus(page);

    const unknownSchema = async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({ response, json: { ...body, schema_version: 2 } });
    };
    await page.route(statusPath(previous.worked), unknownSchema);
    await openStatus(handle);
    check("B/unsupported schema", (await lineValue(page, "selection")) === c.unavailable);
    await page.unroute(statusPath(previous.worked), unknownSchema);
    await closeStatus(page);

    const missing = async (route) =>
      route.fulfill({ status: 404, contentType: "application/json", body: '{"error":"frame not found","code":"frame_not_found"}' });
    await page.route(statusPath(previous.worked), missing);
    await openStatus(handle);
    check("B/404", (await lineValue(page, "run")) === c.notFound);
    await page.unroute(statusPath(previous.worked), missing);
    await closeStatus(page);

    // Out of order, with no event-cursor movement between them: two hint
    // reads overlap, the server answers the later-issued one first (before a
    // selection save) and the earlier-issued one second (after it), and the
    // page receives them in that order. The earlier-issued read carries the
    // newer selection revision at the same cursor and must win; issue order
    // alone would keep the stale selection.
    await openConversation(handle, base, projectId, id.bump);
    await openStatus(handle);
    check("B/bump before", (await lineValue(page, "selection")) === c.savedFrame);
    const crossing = await intercept(page, statusPath(id.bump));
    inject(handle, { type: "auto_audit_completed", root_frame_id: id.bump, subject_kind: "result_review" });
    const earlier = await crossing.next(0);
    inject(handle, { type: "repair_completed", root_frame_id: id.bump });
    const later = await crossing.next(1);
    await later.fetch();
    runFixture(owned.dataDir, "selection_bump", id.bump, projectId);
    await earlier.fetch();
    await later.deliver();
    await sleep(300);
    check("B/later-issued read with the older revision shown first", (await lineValue(page, "selection")) === c.savedFrame);
    await earlier.deliver();
    await waitUntil("newer revision shown", async () => (await lineValue(page, "selection")) === c.bumped, 10000).catch(() => {});
    check("B/earlier-issued read with a newer revision at the same cursor wins", (await lineValue(page, "selection")) === c.bumped,
      JSON.stringify(await lineValue(page, "selection")));
    await crossing.stop();
    // And the converse: an older answer arriving last cannot take it back.
    const older = await holdNext(page, statusPath(id.bump));
    inject(handle, { type: "auto_audit_started", root_frame_id: id.bump, subject_kind: "result_review" });
    await older.observed;
    runFixture(owned.dataDir, "frame_autonomous", id.bump, projectId);
    inject(handle, { type: "repair_started", root_frame_id: id.bump });
    await waitUntil("newest selection shown", async () => (await lineValue(page, "selection")) === c.savedFrame, 10000);
    older.release();
    await waitUntil("older read delivered", async () => older.delivered(), 10000);
    await sleep(300);
    check("B/older read cannot overwrite a newer selection revision", (await lineValue(page, "selection")) === c.savedFrame);
    await closeStatus(page);

    // A hint for another conversation and a non-canonical type read nothing.
    await openStatus(handle);
    const quiet = handle.requests.length;
    inject(handle, { type: "auto_run_started", root_frame_id: id.legacy });
    inject(handle, { type: "review_started", root_frame_id: id.bump });
    await sleep(400);
    check("B/foreign hints ignored", handle.requests.slice(quiet).filter((item) => /\/auto-mode$/.test(item.path)).length === 0);
    await closeStatus(page);

    // Branch change: a read of branch A is answered, then branch B is
    // activated (the conversation reopens) and its read is held. The stale A
    // answer reaching the page first must not paint A under the new opening.
    await openConversation(handle, base, projectId, id.branch);
    await openStatus(handle);
    check("B/branch A", (await lineValue(page, "run")) === c.branchA);
    const branchReads = await intercept(page, statusPath(id.branch));
    inject(handle, { type: "auto_run_started", root_frame_id: id.branch });
    const staleA = await branchReads.next(0);
    await staleA.fetch();
    const activated = runFixture(owned.dataDir, "branch_activate", id.branch, projectId);
    inject(handle, { type: "branch_activation_state", root_frame_id: id.branch, branch_id: activated.branch_id });
    // The reopen re-keys the block and reads again if it is still on screen;
    // if the reopen closed the menu, opening it is that read.
    const freshB = await waitUntil("branch B read", async () => {
      if (branchReads.entries[1]) return branchReads.entries[1];
      if (!(await block(page).count())) {
        await page.locator("#session-options-btn").click().catch(() => {});
        await block(page).waitFor({ state: "attached", timeout: 3000 }).catch(() => {});
      }
      return null;
    }, 15000, 200);
    await freshB.fetch();
    await staleA.deliver();
    await sleep(400);
    const afterStale = await lineValue(page, "run");
    check("B/stale branch read dropped", afterStale !== c.branchA, JSON.stringify(afterStale));
    await freshB.deliver();
    await waitUntil("branch B shown", async () => (await lineValue(page, "run")) === c.branchB, 10000).catch(() => {});
    check("B/new branch shown", (await lineValue(page, "run")) === c.branchB, JSON.stringify(await lineValue(page, "run")));
    await branchReads.stop();
    await closeStatus(page);

    // Reconnect: the socket closes under an open block, and the block reads again.
    await openConversation(handle, base, previous.projectId, previous.worked);
    await openStatus(handle);
    const beforeReconnect = handle.requests.length;
    const socket = handle.sockets[handle.sockets.length - 1];
    await socket.ws.close({ code: 1001, reason: "acceptance reconnect" });
    await waitUntil("reconnect read", async () =>
      handle.requests.slice(beforeReconnect).some((item) => item.path === `/api/v1/frames/${previous.worked}/auto-mode`), 15000, 100);
    try {
      await waitUntil("reconnect read painted", async () =>
        (await block(page).count()) > 0 && (await block(page).getAttribute("data-phase")) === "ready", 15000, 100);
    } catch (error) {
      const count = await block(page).count();
      const phase = count ? await block(page).getAttribute("data-phase") : null;
      const text = count ? ((await block(page).textContent()) || "").slice(0, 160) : "";
      const recent = handle.requests.slice(beforeReconnect).map((item) => item.method + " " + item.path.replace(/f-[0-9a-f]+/g, "F")).slice(0, 30);
      throw new Error(`${error.message}; blocks=${count} phase=${phase} text=${text} sockets=${handle.sockets.length} requests=${JSON.stringify(recent)}`);
    }
    const reconnected = await lineValue(page, "availability");
    check("B/reconnect re-read", reconnected === c.storageAvailable, JSON.stringify(reconnected));
    await closeStatus(page);

    // Reopen: a reload paints only what a new GET says.
    const beforeReload = new Set(handle.sockets);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#session-options-btn").waitFor({ state: "visible", timeout: 30000 });
    await waitSubscribed(handle, previous.worked, beforeReload);
    const afterReload = handle.requests.length;
    await openStatus(handle);
    check("B/reopen read", handle.requests.slice(afterReload).some((item) => /\/auto-mode$/.test(item.path)));
    check("B/reopen lines", (await lineValue(page, "selection")) === c.builtIn);
    await closeStatus(page);

    // The Audit view: paging, filters, redaction, error codes, refresh hints.
    await openConversation(handle, base, projectId, id.audits);
    await openStatus(handle);
    await block(page).locator(".am-audit").click();
    const modal = page.locator("#modal:not(.hidden) .am-audits");
    await modal.waitFor({ state: "visible", timeout: 10000 });
    const rows = modal.locator(".am-audit");
    const listReady = async () => (await modal.locator(".am-audit-list").getAttribute("data-phase")) === "ready";
    await waitUntil("first audit page", async () => (await listReady()) && (await rows.count()) === 20);
    check("B/audit newest first", (await rows.first().getAttribute("data-audit-id")) === "audit-permission-09");
    await modal.locator(".am-more").click();
    await waitUntil("second audit page", async () => (await rows.count()) === 25);
    check("B/audit last page has no more", (await modal.locator(".am-more").count()) === 0);
    await modal.locator('.am-filter[data-kind="permission_review"]').click();
    await waitUntil("permission filter", async () => (await listReady()) && (await rows.count()) === 10);
    check("B/permission filter pressed", (await modal.locator('.am-filter[data-kind="permission_review"]').getAttribute("aria-pressed")) === "true");
    check("B/permission filter rows", (await modal.locator('.am-audit[data-kind="result_review"]').count()) === 0);
    await modal.locator('.am-filter[data-kind="result_review"]').click();
    await waitUntil("result filter", async () => (await listReady()) && (await rows.count()) === 15);
    check("B/result filter has one page", (await modal.locator(".am-more").count()) === 0);

    // A hint of another kind than the filter reads nothing; a terminal re-reads.
    const hintMark = handle.requests.length;
    inject(handle, { type: "auto_audit_completed", root_frame_id: id.audits, subject_kind: "permission_review" });
    await sleep(400);
    check("B/audit hint of another kind ignored", handle.requests.slice(hintMark).filter((item) => /\/auto-audits\?/.test(item.path)).length === 0);
    inject(handle, { type: "auto_run_terminal", root_frame_id: id.audits });
    await waitUntil("terminal re-read", async () => handle.requests.slice(hintMark).some((item) => /\/auto-audits\?.*subject_kind=result_review/.test(item.path)));

    const rejectKind = async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.get("subject_kind") === "permission_review") {
        await route.fulfill({ status: 400, contentType: "application/json", body: '{"error":"bad kind","code":"invalid_subject_kind"}' });
        return;
      }
      await route.fallback().catch(() => {});
    };
    await page.route(auditPath(id.audits), rejectKind);
    const kindMark = handle.requests.length;
    await modal.locator('.am-filter[data-kind="permission_review"]').click();
    await waitUntil("kind reset to all", async () => (await modal.locator('.am-filter[data-kind="all"]').getAttribute("aria-pressed")) === "true" && (await listReady()) && (await rows.count()) === 20);
    const kindReads = handle.requests.slice(kindMark).filter((item) => /\/auto-audits\?/.test(item.path)).map((item) => item.path);
    check("B/rejected kind sent once", kindReads.filter((p) => p.includes("subject_kind=permission_review")).length === 1, JSON.stringify(kindReads));
    check("B/then both kinds", kindReads.at(-1) === `/api/v1/frames/${id.audits}/auto-audits?limit=20`, JSON.stringify(kindReads));
    await page.unroute(auditPath(id.audits), rejectKind);

    const inject_secrets = async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.audits = body.audits.map((row, index) => (index === 0 ? {
        ...row,
        prompt: SECRET,
        system_prompt: SECRET,
        assessment_prompt: SECRET,
        hidden_rationale: SECRET,
        permission_request: { body: SECRET },
        authorization: SECRET,
        capability: { grant: SECRET },
        rationale_summary: "Bounded rationale for the acceptance.",
        findings: [{ finding_id: "finding-acceptance", severity: "major", category: "evidence", status: "open", claim: "Recompute the dose-response fit.", cell_ids: ["cell-7"], prompt: SECRET }],
      } : row));
      await route.fulfill({ response, json: body });
    };
    await page.route(auditPath(id.audits), inject_secrets);
    await modal.locator('.am-filter[data-kind="result_review"]').click();
    await waitUntil("redaction page", async () => (await listReady()) && (await rows.count()) === 15);
    const modalText = (await modal.textContent()) || "";
    check("B/audit allowlist", !modalText.includes(SECRET));
    check("B/audit finding shown", modalText.includes("Recompute the dose-response fit.") && modalText.includes("cell-7"));
    await page.unroute(auditPath(id.audits), inject_secrets);

    let auditFailures = 1;
    const auditDown = async (route) => {
      if (auditFailures > 0) {
        auditFailures -= 1;
        await route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"x","code":"auto_mode_storage_unavailable"}' });
        return;
      }
      await route.fallback().catch(() => {});
    };
    await page.route(auditPath(id.audits), auditDown);
    await modal.locator('.am-filter[data-kind="all"]').click();
    await waitUntil("audits unavailable", async () => ((await modal.locator(".am-audit-list").textContent()) || "").includes(c.auditsUnavailable));
    await modal.locator(".am-retry").click();
    await waitUntil("audits retried", async () => (await listReady()) && (await rows.count()) === 20);
    await page.unroute(auditPath(id.audits), auditDown);
    await page.keyboard.press("Escape");
    await page.locator("#modal.hidden").waitFor({ state: "attached", timeout: 5000 });

    assertReadOnly(handle, mark, "B dynamic");
    countReads(handle, mark);
    for (const item of handles) check(`B/page errors (${item.pageErrors.length})`, item.pageErrors.length === 0, item.pageErrors.join(" | "));
  } finally {
    for (const item of handles) await item.context.close().catch(() => {});
    const stopped = await stopChild(daemon.child);
    summary.phases[summary.phases.length - 1].stopped = stopped;
    if (!stopped) throw new Error("phase B daemon did not stop");
  }
}

async function main() {
  let owned = null;
  let tripwire = null;
  let browser = null;
  try {
    owned = createOwnedDataDir();
    tripwire = await startTripwire();
    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ headless: true, executablePath });
    const previous = await phaseA(browser, owned, tripwire);
    await phaseB(browser, owned, tripwire, previous);
    summary.cleanup.daemons_stopped = summary.phases.every((phase) => phase.stopped === true);
  } catch (error) {
    fail(error?.stack ? `${error.message}` : error);
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (tripwire) {
      summary.live_llm_calls_observed = tripwire.count();
      await tripwire.close().catch(() => {});
      summary.cleanup.tripwire_stopped = true;
    }
    summary.cleanup.daemons_stopped = summary.phases.length > 0 && summary.phases.every((phase) => phase.stopped === true);
    if (owned && summary.cleanup.daemons_stopped) {
      try {
        fs.rmSync(validateOwnedDataDir(owned.dataDir, owned.markerValue), { recursive: true, force: false });
        summary.cleanup.data_dir_removed = !fs.existsSync(owned.dataDir);
      } catch (error) {
        fail(`data-dir cleanup failed: ${error?.message || error}`);
      }
    }
  }
  if (summary.agent_requests_sent !== 0) fail("the acceptance sent an Agent request");
  if (summary.live_llm_calls_observed !== 0) fail("a daemon attempted a model call");
  if (summary.external_network_calls !== 0) fail("the browser attempted a request outside the daemon origin");
  if (summary.requests.non_get_while_in_use !== 0) fail("the Auto Mode surface issued a write");
  summary.cleanup.ok = summary.cleanup.daemons_stopped && summary.cleanup.data_dir_removed && summary.cleanup.tripwire_stopped;
  summary.passed = summary.failures.length === 0 && summary.cleanup.ok;
  process.exitCode = summary.passed ? 0 : 1;
  console.log(`SUMMARY ${redactSecrets(JSON.stringify(summary))}`);
}

await main();
