// Stage 1 trusted-delivery browser acceptance.
//
// The harness owns every mutable resource it touches: a random non-default
// loopback port, a temporary data directory, a daemon child, one project, and
// a local LLM tripwire.  It sends no Agent message and permits no browser
// request outside the selected daemon origin.  Stdout contains exactly one
// machine-readable record:
//   SUMMARY { ... }

import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { authenticate } from "./browser_auth.mjs";

const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixturePath = path.join(workspaceRoot, "tests", "test_stage1_browser_fixture.py");
const pythonPath = process.env.OPENAI4S_PYTHON
  ? path.resolve(process.env.OPENAI4S_PYTHON)
  : path.join(workspaceRoot, ".venv", "bin", "python");
const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const dataPrefix = "openai4s-stage1-browser-";
const ownerMarker = ".stage1-browser-owner";

function makeSummary(mode = "acceptance") {
  return {
    schema_version: 1,
    name: "stage1_trusted_delivery_browser_acceptance",
    mode,
    feature_flag_enabled: mode === "acceptance",
    base_url: mode === "self_test" ? "<self-test>" : "<unstarted>",
    daemon_binding: null,
    standard_profile_readiness: null,
    delivery: {
      expected_link_count: 100,
      persisted_message_count: null,
      persisted_delivery_count: null,
      projection_source:
        "openai4s.server.completions.completion_message+webui.renderStored+renderMd",
      initial: null,
      reload: null,
      reopen: null,
    },
    deduplication: null,
    delegated_provenance: null,
    immutable_old_link: null,
    agent_requests_sent: 0,
    live_llm_calls_observed: 0,
    external_network_calls: 0,
    measurement_scope:
      "browser requests are origin-blocked; every model base URL targets an owned loopback tripwire; readiness is a local metadata fixture, not a runtime-build claim",
    cleanup: {
      project_delete_status: null,
      project_absent: null,
      artifact_absent: null,
      daemon_stopped: false,
      data_dir_removed: false,
      tripwire_stopped: false,
      ok: false,
    },
    self_test_checks: null,
    failures: [],
    passed: false,
  };
}

let summary = makeSummary();

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function assertion(condition, message) {
  if (!condition) throw new Error(message);
}

function stripUrlSecrets(raw) {
  try {
    const value = new URL(raw);
    if (value.protocol !== "http:" && value.protocol !== "https:") return "<url>";
    return `${value.protocol}//${value.host}/<path>`;
  } catch {
    return "<url>";
  }
}

function sanitizeDiagnostic(value) {
  const firstLine = String(value == null ? "" : value).split(/\r?\n/, 1)[0];
  return firstLine
    .replace(/\x1b\[[0-?]*[ -\/]*[@-~]/g, "")
    .replace(/([?&](?:token|api[_-]?key|secret|password)=)[^&#\s]+/gi, "$1<redacted>")
    .replace(/(\bBearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1<redacted>")
    .replace(/(X-OpenAI4S-Token\s*:\s*)\S+/gi, "$1<redacted>")
    .replace(/https?:\/\/[^\s"'<>]+/gi, (url) => stripUrlSecrets(url))
    .replace(
      new RegExp(`${workspaceRoot.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[^\\s:]*`, "g"),
      "<path>",
    )
    .replace(/(?:\/Users|\/home|\/private\/var|[A-Za-z]:\\)[^\s:]+/g, "<path>")
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/gi, "<dynamic_id>")
    .replace(/\b(?:proj|frame|artifact|version|delivery|run|turn|execution)[_-][A-Za-z0-9._:-]{6,}\b/gi, "<dynamic_id>")
    .replace(/\b[avm]-[A-Za-z0-9._:-]{8,}\b/g, "<dynamic_id>")
    .slice(0, 500);
}

function fail(message) {
  summary.failures.push(sanitizeDiagnostic(message));
}

function serializeSummaryForOutput(candidate, credential = null) {
  let output = JSON.stringify(candidate);
  if (typeof credential === "string" && credential) {
    output = output.split(credential).join("<redacted>");
  }
  return output;
}

function validateSummarySchema(candidate) {
  assertion(candidate && typeof candidate === "object" && !Array.isArray(candidate), "summary must be an object");
  assertion(candidate.schema_version === 1, "summary schema version mismatch");
  assertion(candidate.name === "stage1_trusted_delivery_browser_acceptance", "summary name mismatch");
  assertion(candidate.mode === "acceptance" || candidate.mode === "self_test", "summary mode invalid");
  assertion(typeof candidate.feature_flag_enabled === "boolean", "feature flag fact invalid");
  assertion(typeof candidate.base_url === "string", "summary base_url invalid");
  assertion(candidate.delivery?.expected_link_count === 100, "expected link count changed");
  assertion(candidate.agent_requests_sent === 0, "Agent request count must be zero");
  assertion(Number.isInteger(candidate.live_llm_calls_observed) && candidate.live_llm_calls_observed >= 0, "LLM call count invalid");
  assertion(Number.isInteger(candidate.external_network_calls) && candidate.external_network_calls >= 0, "network count invalid");
  assertion(typeof candidate.measurement_scope === "string" && candidate.measurement_scope.length > 0, "measurement scope missing");
  assertion(Array.isArray(candidate.failures) && candidate.failures.every((item) => typeof item === "string"), "failure list invalid");
  assertion(candidate.cleanup && typeof candidate.cleanup === "object", "cleanup missing");
  for (const field of ["daemon_stopped", "data_dir_removed", "tripwire_stopped", "ok"]) {
    assertion(typeof candidate.cleanup[field] === "boolean", `cleanup.${field} invalid`);
  }
  assertion(typeof candidate.passed === "boolean", "summary passed invalid");
  if (candidate.mode === "acceptance" && candidate.base_url !== "<unstarted>") {
    const base = validateLoopbackUrl(candidate.base_url);
    assertion(base.port !== "8760", "summary names the user daemon port");
  }
  const encoded = JSON.stringify(candidate);
  assertion(!encoded.includes(workspaceRoot), "summary contains workspace path");
  assertion(!/\bBearer\s+(?!<redacted>)/i.test(encoded), "summary contains bearer credential");
  assertion(!/[?&](?:token|api[_-]?key|secret|password)=[^<]/i.test(encoded), "summary contains query credential");
  assertion(!/\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i.test(encoded), "summary contains dynamic UUID");
}

function validateLoopbackUrl(raw) {
  const parsed = new URL(raw);
  if (parsed.protocol !== "http:") throw new Error("acceptance URL must use http");
  if (parsed.hostname !== "127.0.0.1" && parsed.hostname !== "[::1]") {
    throw new Error("acceptance URL must use a numeric loopback host");
  }
  if (!parsed.port || parsed.port === "8760") {
    throw new Error("acceptance URL must use an explicit non-user-daemon port");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== "/") {
    throw new Error("acceptance URL must be an origin root without credentials");
  }
  return parsed;
}

function sameOrDescendant(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

function defaultDataDir() {
  return path.resolve(os.homedir(), ".openai4s");
}

function validateOwnedDataDir(dataDir, markerValue, { requireMarker = true } = {}) {
  const resolved = fs.realpathSync(path.resolve(dataDir));
  const tempRoot = fs.realpathSync(os.tmpdir());
  if (!sameOrDescendant(resolved, tempRoot) || path.basename(resolved).startsWith(dataPrefix) === false) {
    throw new Error("acceptance data directory is not an owned temporary root");
  }
  const normalDefault = defaultDataDir();
  let resolvedDefault = normalDefault;
  try { resolvedDefault = fs.realpathSync(normalDefault); } catch {}
  if (sameOrDescendant(resolved, normalDefault) || sameOrDescendant(resolved, resolvedDefault)) {
    throw new Error("the default OpenAI4S data directory is forbidden");
  }
  const stats = fs.lstatSync(resolved);
  if (!stats.isDirectory() || stats.isSymbolicLink()) {
    throw new Error("acceptance data directory must be a real directory");
  }
  if (requireMarker) {
    const marker = path.join(resolved, ownerMarker);
    const markerStat = fs.lstatSync(marker);
    if (!markerStat.isFile() || markerStat.isSymbolicLink()) {
      throw new Error("acceptance ownership marker is invalid");
    }
    const observed = fs.readFileSync(marker, "utf8");
    if (observed !== markerValue) throw new Error("acceptance ownership marker changed");
  }
  return resolved;
}

function createOwnedDataDir() {
  const markerValue = crypto.randomBytes(32).toString("hex");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), dataPrefix));
  fs.chmodSync(dataDir, 0o700);
  fs.writeFileSync(path.join(dataDir, ownerMarker), markerValue, {
    encoding: "utf8",
    mode: 0o600,
    flag: "wx",
  });
  return { dataDir: validateOwnedDataDir(dataDir, markerValue), markerValue };
}

function removeOwnedDataDir(dataDir, markerValue) {
  const resolved = validateOwnedDataDir(dataDir, markerValue);
  fs.rmSync(resolved, { recursive: true, force: false });
  return !fs.existsSync(resolved);
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
        else if (!Number.isInteger(port) || port <= 0 || port === 8760) reject(new Error("could not allocate a safe loopback port"));
        else resolve(port);
      });
    });
  });
}

function minimalChildEnvironment(extra = {}) {
  const environment = {
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONUTF8: "1",
    OPENAI4S_SKIP_DOTENV: "1",
    OPENAI4S_SECRET_STORE: "plaintext",
    OPENAI4S_UNATTENDED_APPROVAL: "deny",
    ...extra,
  };
  for (const key of ["PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "WINDIR"]) {
    if (process.env[key]) environment[key] = process.env[key];
  }
  return environment;
}

function runFixture(args, extraEnvironment = {}) {
  const child = spawnSync(pythonPath, [fixturePath, ...args], {
    cwd: workspaceRoot,
    encoding: "utf8",
    env: minimalChildEnvironment(extraEnvironment),
    maxBuffer: 16 * 1024 * 1024,
  });
  if (child.error) throw child.error;
  if (child.status !== 0) throw new Error("Stage 1 fixture child failed");
  if (String(child.stderr || "").trim()) throw new Error("Stage 1 fixture child emitted stderr");
  let value;
  try { value = JSON.parse(child.stdout); } catch { throw new Error("Stage 1 fixture child returned invalid JSON"); }
  if (!value || value.schema_version !== 1) throw new Error("Stage 1 fixture schema mismatch");
  return value;
}

async function startTripwire() {
  let calls = 0;
  const server = http.createServer((_request, response) => {
    calls += 1;
    response.writeHead(503, { "content-type": "application/json" });
    response.end('{"error":"model calls are forbidden in Stage 1 acceptance"}');
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const port = address && typeof address === "object" ? address.port : 0;
  if (!port || port === 8760) throw new Error("LLM tripwire did not bind safely");
  return {
    url: `http://127.0.0.1:${port}/v1`,
    count: () => calls,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

function boundedLogCollector(stream) {
  let value = "";
  stream?.setEncoding("utf8");
  stream?.on("data", (chunk) => {
    value = (value + chunk).slice(-64 * 1024);
  });
  return () => value;
}

async function waitUntil(label, operation, timeoutMs = 30000, intervalMs = 80) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    try {
      const value = await operation();
      if (value) return value;
    } catch (error) {
      last = error;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
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

function safeReadToken(dataDir) {
  const tokenPath = path.join(dataDir, "access-token");
  const pathStat = fs.lstatSync(tokenPath);
  if (!pathStat.isFile() || pathStat.isSymbolicLink() || (pathStat.mode & 0o077) !== 0) {
    throw new Error("daemon access-token is not an owner-only regular file");
  }
  let descriptor;
  try {
    descriptor = fs.openSync(tokenPath, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    const opened = fs.fstatSync(descriptor);
    const current = fs.statSync(tokenPath);
    if (!opened.isFile() || opened.dev !== current.dev || opened.ino !== current.ino) {
      throw new Error("daemon access-token identity changed");
    }
    const token = fs.readFileSync(descriptor, "utf8").trim();
    if (!token) throw new Error("daemon access-token is empty");
    return token;
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
}

function validateDaemonBinding(dataDir, markerValue, daemon, port) {
  const resolved = validateOwnedDataDir(dataDir, markerValue);
  const stateRaw = fs.readFileSync(path.join(resolved, "daemon.json"), "utf8");
  const state = JSON.parse(stateRaw);
  const pid = Number(fs.readFileSync(path.join(resolved, "openai4s.pid"), "utf8").trim());
  if (!state || state.pid !== daemon.pid || pid !== daemon.pid || daemon.exitCode !== null) {
    throw new Error("daemon state does not identify the owned live child");
  }
  if (state.host !== "127.0.0.1" || Number(state.port) !== port) {
    throw new Error("daemon state does not match the selected loopback origin");
  }
  return {
    stateSha256: sha256(Buffer.from(stateRaw, "utf8")),
    token: safeReadToken(resolved),
  };
}

async function stopChild(child, timeoutMs = 10000) {
  if (!child || child.exitCode !== null) return true;
  child.kill("SIGTERM");
  const exited = await Promise.race([
    new Promise((resolve) => child.once("exit", () => resolve(true))),
    new Promise((resolve) => setTimeout(() => resolve(false), timeoutMs)),
  ]);
  if (!exited && child.exitCode === null) {
    child.kill("SIGKILL");
    await Promise.race([
      new Promise((resolve) => child.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 3000)),
    ]);
  }
  return child.exitCode !== null;
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch (error) {
    const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
    if (!fallback) throw error;
    return import(fallback);
  }
}

function waitForContextResponse(context, expectedPath, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      context.off("response", listener);
      reject(new Error("clicked Artifact navigation response timed out"));
    }, timeoutMs);
    const listener = (response) => {
      let target;
      try { target = new URL(response.url()); } catch { return; }
      if (target.pathname !== expectedPath || !response.request().isNavigationRequest()) return;
      clearTimeout(timer);
      context.off("response", listener);
      resolve(response);
    };
    context.on("response", listener);
  });
}

async function waitForRenderedLinks(page, count = 100) {
  const selector = '#messages .msg.assistant .md a[href^="/api/v1/artifacts/"]';
  const links = page.locator(selector);
  await waitUntil(
    "persisted completion links",
    async () => (await links.count()) === count,
    30000,
    50,
  );
  return links;
}

async function verifyRenderedLinks(page, context, base, expected, label) {
  const links = await waitForRenderedLinks(page, expected.length);
  const actual = await links.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
  assertion(actual.length === expected.length, `${label}: rendered link count changed`);
  for (let index = 0; index < expected.length; index += 1) {
    assertion(actual[index] === expected[index].url, `${label}: exact version href mismatch`);
  }

  let clicked = 0;
  let status200 = 0;
  let checksumMatches = 0;
  for (let index = 0; index < expected.length; index += 1) {
    const item = expected[index];
    const target = new URL(item.url, base);
    const popupPromise = page.waitForEvent("popup", { timeout: 10000 });
    const responsePromise = waitForContextResponse(context, target.pathname);
    await links.nth(index).click();
    const [popup, response] = await Promise.all([popupPromise, responsePromise]);
    clicked += 1;
    try {
      const landed = new URL(popup.url());
      assertion(landed.origin === base.origin && landed.pathname === target.pathname, `${label}: clicked link left the exact daemon URL`);
      const bytes = Buffer.from(await response.body());
      if (response.status() === 200) status200 += 1;
      if (sha256(bytes) === item.sha256 && bytes.length === item.size_bytes) checksumMatches += 1;
    } finally {
      await popup.close().catch(() => {});
    }
  }
  assertion(clicked === 100, `${label}: not every rendered link was clicked`);
  assertion(status200 === 100, `${label}: an exact-version click did not return 200`);
  assertion(checksumMatches === 100, `${label}: an exact-version click returned wrong bytes`);
  return { rendered: actual.length, clicked, status_200: status200, sha256_matches: checksumMatches };
}

async function openWorkspace(page, baseUrl, projectId, frameId) {
  const deepLink = new URL(
    `projects/${encodeURIComponent(projectId)}/frames/${encodeURIComponent(frameId)}`,
    baseUrl,
  ).toString();
  const response = await page.goto(deepLink, { waitUntil: "domcontentloaded" });
  if (!response || !response.ok()) throw new Error("workspace deep link did not return 200");
  await page.locator("#workspace:not(.hidden)").waitFor({ state: "visible", timeout: 30000 });
  await waitForRenderedLinks(page, 100);
}

async function dumpArtifactReview(page, filename, step) {
  return page.evaluate((fname) => {
    const files = document.getElementById("dock-files");
    const viewer = document.getElementById("dock-viewer");
    const filesBtn = document.getElementById("files-btn");
    return {
      step: undefined,
      fname,
      filesBtn: !!(filesBtn && filesBtn.getBoundingClientRect().height),
      filesHidden: files ? files.classList.contains("hidden") : "missing",
      viewerHidden: viewer ? viewer.classList.contains("hidden") : "missing",
      names: Array.from(document.querySelectorAll("#results-list .a-name")).map((n) => n.textContent),
      vh: document.querySelector(".viewer-head .vh-name")?.textContent || null,
      acts: Array.from(document.querySelectorAll(".viewer-head .vh-acts button")).map((n) => ({
        text: String(n.textContent || "").trim(),
        title: n.getAttribute("title"),
        f16: n.getAttribute("data-f16-provenance"),
      })),
      ctx: Array.from(document.querySelectorAll(".ctx-item")).map((n) => String(n.textContent || "").trim()),
      subs: Array.from(document.querySelectorAll(".prov-subtab")).map((n) => n.textContent),
      cards: document.querySelectorAll(".prov-body .prov-card").length,
      activeTab: window.S && window.S.activeTab,
      provMode: !!(window.S && window.S.provMode),
    };
  }, filename).then((state) => ({ ...state, step }));
}

async function openArtifactReview(page, filename) {
  let step = "files-btn";
  try {
    await page.locator("#files-btn").click();
    step = "dock-files";
    await page.locator("#dock-files:not(.hidden)").waitFor({ state: "visible", timeout: 30000 });
    const name = page.locator("#results-list .a-name").filter({ hasText: filename });
    step = "a-name";
    await name.first().waitFor({ state: "visible", timeout: 30000 });
    await name.first().click();
    step = "vh-name";
    await page.locator(".viewer-head .vh-name").filter({ hasText: filename }).waitFor({ state: "visible", timeout: 30000 });
    // F-16 inserts a dedicated Provenance/溯源 button as the first `.vh-acts`
    // child; F-17's overflow menu still has the same item. Prefer the button so
    // we do not wait for a `.ctx-item` that never appears.
    const provenanceBtn = page.locator(".viewer-head .vh-acts button").filter({
      hasText: /Provenance|溯源/,
    });
    if (await provenanceBtn.count()) {
      step = "provenance-btn";
      await provenanceBtn.first().click();
    } else {
      step = "overflow-menu";
      await page.locator(".viewer-head .vh-acts button").first().click();
      step = "ctx-item";
      await page.locator(".ctx-item").filter({ hasText: /Provenance|溯源/ }).click();
    }
    const review = page.locator(".prov-subtab").filter({ hasText: /Review/ });
    step = "prov-subtab";
    await review.waitFor({ state: "visible", timeout: 30000 });
    await review.click();
    const body = page.locator(".prov-body");
    step = "prov-card";
    await body.locator(".prov-card").first().waitFor({ state: "visible", timeout: 30000 });
    return {
      text: await body.innerText(),
      view_code_links: await body.locator(".prov-link").count(),
    };
  } catch (error) {
    const state = await dumpArtifactReview(page, filename, step);
    throw new Error(`openArtifactReview failed at ${step}: ${String(error && error.message ? error.message : error).split(/\n/, 1)[0]} dump=${JSON.stringify(state)}`);
  }
}

async function selfTest() {
  const candidate = makeSummary("self_test");
  let owned = null;
  try {
    const port = await allocateLoopbackPort();
    assertion(port !== 8760, "self-test selected the user daemon port");
    validateLoopbackUrl(`http://127.0.0.1:${port}/`);
    let defaultRejected = false;
    try { validateOwnedDataDir(defaultDataDir(), "not-owned"); } catch { defaultRejected = true; }
    assertion(defaultRejected, "self-test did not reject the default data directory");
    owned = createOwnedDataDir();
    const envRoot = path.join(owned.dataDir, "fixture-envs");
    const prepared = runFixture(["prepare", "--env-root", envRoot]);
    const readiness = runFixture(["inspect-readiness", "--env-root", envRoot]);
    assertion(JSON.stringify(prepared.required_package_counts) === JSON.stringify({ python: 33, r: 8 }), "fixture dependency counts changed");
    assertion(readiness.ready === true && readiness.state === "ready", "production readiness did not accept fixture metadata");
    assertion((readiness.missing_environments || []).length === 0, "fixture has a missing environment");
    assertion(Object.keys(readiness.missing_packages || {}).length === 0, "fixture has a missing package");
    const secret = "stage1-self-test-credential";
    assertion(!sanitizeDiagnostic(`Bearer ${secret}`).includes(secret), "credential diagnostic redaction failed");
    candidate.cleanup.data_dir_removed = removeOwnedDataDir(owned.dataDir, owned.markerValue);
    owned = null;
    candidate.cleanup.daemon_stopped = true;
    candidate.cleanup.tripwire_stopped = true;
    candidate.cleanup.ok = candidate.cleanup.data_dir_removed;
    candidate.self_test_checks = {
      random_non_default_loopback_port: true,
      default_data_dir_rejected: true,
      owner_marker_cleanup: true,
      credential_redaction: true,
      fixture_package_counts: prepared.required_package_counts,
      production_metadata_readiness_ready: true,
      summary_schema: true,
    };
    candidate.passed = candidate.cleanup.ok;
    validateSummarySchema(candidate);
    let invalidRejected = false;
    try { validateSummarySchema({ ...candidate, agent_requests_sent: 1 }); } catch { invalidRejected = true; }
    assertion(invalidRejected, "summary schema accepted an Agent request");
    return candidate;
  } finally {
    if (owned && fs.existsSync(owned.dataDir)) {
      try { removeOwnedDataDir(owned.dataDir, owned.markerValue); } catch {}
    }
  }
}

async function runAcceptance() {
  let owned = null;
  let tripwire = null;
  let daemon = null;
  let daemonStdout = () => "";
  let daemonStderr = () => "";
  let browser = null;
  let context = null;
  let requestApi = null;
  let projectId = null;
  let frameId = null;
  let cleanupArtifactUrl = null;
  let presentedToken = null;
  let externalNetworkCalls = 0;
  let agentRequestsSent = 0;
  try {
    owned = createOwnedDataDir();
    const envRoot = path.join(owned.dataDir, "fixture-envs");
    const prepared = runFixture(["prepare", "--env-root", envRoot]);
    assertion(prepared.required_package_counts?.python === 33 && prepared.required_package_counts?.r === 8, "standard metadata fixture is incomplete");

    const port = await allocateLoopbackPort();
    const base = validateLoopbackUrl(`http://127.0.0.1:${port}/`);
    const baseUrl = base.toString();
    summary.base_url = `${base.origin}/`;
    tripwire = await startTripwire();
    const daemonEnvironment = minimalChildEnvironment({
      OPENAI4S_DATA_DIR: owned.dataDir,
      OPENAI4S_HOST: "127.0.0.1",
      OPENAI4S_PORT: String(port),
      OPENAI4S_REQUIRE_TOKEN: "1",
      OPENAI4S_STAGE1_TRUSTED_DELIVERY: "1",
      OPENAI4S_ENV_ROOTS: envRoot,
      OPENAI4S_ENV_GENERATIONS_ROOT: path.join(owned.dataDir, ".no-generations"),
      OPENAI4S_ALLOW_NETWORK: "0",
      OPENAI4S_LLM_BASE_URL: tripwire.url,
      OPENAI4S_DEEPSEEK_BASE_URL: tripwire.url,
      OPENAI4S_OPENAI_BASE_URL: tripwire.url,
      OPENAI4S_ANTHROPIC_BASE_URL: tripwire.url,
      OPENAI4S_GEMINI_BASE_URL: tripwire.url,
    });
    daemon = spawn(pythonPath, ["-m", "openai4s", "serve", "--no-browser", "--port", String(port)], {
      cwd: workspaceRoot,
      env: daemonEnvironment,
      stdio: ["ignore", "pipe", "pipe"],
    });
    daemonStdout = boundedLogCollector(daemon.stdout);
    daemonStderr = boundedLogCollector(daemon.stderr);
    daemon.once("error", (error) => fail(`daemon child error: ${error.message || error}`));
    await waitUntil("disposable daemon startup", async () => {
      if (daemon.exitCode !== null) throw new Error("disposable daemon exited during startup");
      const filesReady = fs.existsSync(path.join(owned.dataDir, "daemon.json"))
        && fs.existsSync(path.join(owned.dataDir, "openai4s.pid"))
        && fs.existsSync(path.join(owned.dataDir, "access-token"));
      return filesReady && await waitForTcp(port);
    }, 45000);
    const binding = validateDaemonBinding(owned.dataDir, owned.markerValue, daemon, port);
    presentedToken = binding.token;
    summary.daemon_binding = { verified: true, state_sha256: binding.stateSha256 };

    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ headless: true, executablePath });
    context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
    await context.route("**/*", async (route) => {
      let destination;
      try { destination = new URL(route.request().url()); } catch {
        externalNetworkCalls += 1;
        await route.abort("blockedbyclient");
        return;
      }
      const localDocument = destination.protocol === "data:" || destination.protocol === "blob:" || destination.protocol === "about:";
      if (!localDocument && (destination.protocol !== "http:" || destination.origin !== base.origin)) {
        externalNetworkCalls += 1;
        await route.abort("blockedbyclient");
        return;
      }
      await route.continue();
    });
    await context.routeWebSocket("**/*", async (socket) => {
      let destination;
      try { destination = new URL(socket.url()); } catch {
        externalNetworkCalls += 1;
        await socket.close({ code: 1008, reason: "blocked by Stage 1 acceptance" });
        return;
      }
      if (destination.origin !== base.origin.replace(/^http:/, "ws:")) {
        externalNetworkCalls += 1;
        await socket.close({ code: 1008, reason: "blocked by Stage 1 acceptance" });
        return;
      }
      socket.connectToServer();
    });

    let page = await context.newPage();
    const pageErrors = [];
    const recordPageError = (error) => pageErrors.push(sanitizeDiagnostic(error?.message || error));
    page.on("pageerror", recordPageError);
    presentedToken = await authenticate(page, baseUrl, presentedToken);
    assertion(presentedToken, "disposable daemon authentication returned no token");

    requestApi = async function request(apiPath, { method = "GET", data } = {}) {
      const target = new URL(apiPath, baseUrl);
      if (target.origin !== base.origin) {
        externalNetworkCalls += 1;
        throw new Error("blocked non-daemon API request");
      }
      if (method === "POST" && /^\/api\/v1\/frames\/[^/]+\/message$/.test(target.pathname)) {
        agentRequestsSent += 1;
      }
      const response = await page.request.fetch(target.toString(), {
        method,
        data,
        headers: data === undefined ? undefined : { "content-type": "application/json" },
        maxRedirects: 0,
      });
      const bytes = Buffer.from(await response.body());
      let body = null;
      try { body = JSON.parse(bytes.toString("utf8")); } catch {}
      return { status: response.status(), bytes, body };
    };

    const readinessResponse = await requestApi("/api/v1/environments/status");
    const readiness = readinessResponse.body?.standard_profile_readiness;
    assertion(readinessResponse.status === 200 && readiness?.enabled === true, "Stage 1 readiness projection is not enabled");
    assertion(readiness.ready === true && readiness.state === "ready", "standard profile is not ready");
    assertion((readiness.missing_environments || []).length === 0, "standard profile reports a missing environment");
    assertion(Object.keys(readiness.missing_packages || {}).length === 0, "standard profile reports a missing package");
    assertion(readiness.network_contacted === false && readiness.mutation_performed === false, "readiness was not local and read-only");
    summary.standard_profile_readiness = {
      status: readinessResponse.status,
      state: readiness.state,
      ready: readiness.ready,
      fixture_kind: prepared.fixture_kind,
      runtime_execution_verified: prepared.runtime_execution_verified,
      runtime_markers: prepared.runtime_markers,
      missing_environment_count: readiness.missing_environments.length,
      missing_package_count: Object.values(readiness.missing_packages).reduce((total, rows) => total + rows.length, 0),
      network_contacted: readiness.network_contacted,
      mutation_performed: readiness.mutation_performed,
      required_package_counts: Object.fromEntries(readiness.environments.map((row) => [row.name, row.required_package_count])),
    };

    const suffix = `${Date.now()}-${process.pid}`;
    const project = await requestApi("/api/v1/projects", { method: "POST", data: { name: `stage1-trusted-delivery-${suffix}` } });
    projectId = project.body?.project_id || project.body?.id;
    assertion(project.status < 300 && projectId, "could not create disposable project");
    const frame = await requestApi("/api/v1/frames", { method: "POST", data: { project_id: projectId } });
    frameId = frame.body?.frame_id || frame.body?.id;
    assertion(frame.status < 300 && frameId, "could not create disposable session");

    const seeded = runFixture(
      ["seed", "--data-dir", owned.dataDir, "--project-id", projectId, "--frame-id", frameId, "--count", "100"],
      { OPENAI4S_DATA_DIR: owned.dataDir },
    );
    assertion(seeded.message_count === 100 && seeded.delivery_count === 100, "fixture did not persist 100 atomic deliveries");
    assertion(Array.isArray(seeded.expected) && seeded.expected.length === 100, "fixture expected-link manifest is incomplete");
    summary.delivery.persisted_message_count = seeded.message_count;
    summary.delivery.persisted_delivery_count = seeded.delivery_count;
    cleanupArtifactUrl = seeded.expected[0].url;

    const messages = await requestApi(`/api/v1/frames/${encodeURIComponent(frameId)}/messages?newest_first=1&limit=300`);
    assertion(messages.status === 200 && messages.body?.messages?.length === 100, "messages API did not reopen 100 persisted completions");
    assertion(messages.body.messages.every((row) => row.role === "assistant" && /\/api\/v1\/artifacts\//.test(row.content)), "a persisted completion did not contain an exact-version link");

    await openWorkspace(page, baseUrl, projectId, frameId);
    summary.delivery.initial = await verifyRenderedLinks(page, context, base, seeded.expected, "initial");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#workspace:not(.hidden)").waitFor({ state: "visible", timeout: 30000 });
    summary.delivery.reload = await verifyRenderedLinks(page, context, base, seeded.expected, "reload");

    await page.close();
    page = await context.newPage();
    page.on("pageerror", recordPageError);
    await openWorkspace(page, baseUrl, projectId, frameId);
    summary.delivery.reopen = await verifyRenderedLinks(page, context, base, seeded.expected, "reopen");

    const dedupVersions = await requestApi(`/api/v1/artifacts/${encodeURIComponent(seeded.dedup.artifact_id)}/versions`);
    assertion(dedupVersions.status === 200 && dedupVersions.body?.versions?.length === 1, "same bytes from another Cell created a fake version");
    assertion(seeded.dedup.same_version_id === true && seeded.dedup.version_count === 1 && seeded.dedup.observation_count === 2, "dedup fixture did not preserve both observations");
    assertion(JSON.stringify(seeded.dedup.producing_cell_ids) === JSON.stringify(["cell-dedup-first", "cell-dedup-second"]), "dedup observation producers changed");
    summary.deduplication = {
      version_count: dedupVersions.body.versions.length,
      observation_count: seeded.dedup.observation_count,
      producer_count: new Set(seeded.dedup.producing_cell_ids).size,
      same_version_id: seeded.dedup.same_version_id,
      capture_kinds: seeded.dedup.capture_kinds,
    };

    const codeProducer = seeded.delegated_provenance.code;
    const nativeProducer = seeded.delegated_provenance.native;
    const codeLineage = await requestApi(`/api/v1/artifacts/${encodeURIComponent(codeProducer.artifact_id)}/lineage`);
    const nativeLineage = await requestApi(`/api/v1/artifacts/${encodeURIComponent(nativeProducer.artifact_id)}/lineage`);
    assertion(codeLineage.status === 200, "delegated code lineage route failed");
    // Delegated child Cells are recorded in execution_log under their own
    // delegate frame, so the producer projection now reports the durable
    // record — while the interactions list stays save-only (no fabricated
    // root-Notebook cell entry for a child frame's cell).
    assertion(codeLineage.body?.producer?.kind === "cell" && codeLineage.body.producer.frame_id === codeProducer.frame_id && codeLineage.body.producer.frame_kind === "delegate" && codeLineage.body.producer.producing_cell_id === codeProducer.producing_cell_id && codeLineage.body.producer.cell_recorded === true, "delegated code producer projection is untrue");
    assertion(Array.isArray(codeLineage.body?.interactions) && codeLineage.body.interactions.every((item) => item.kind !== "cell"), "delegated code producer fabricated a root-Notebook cell interaction");
    assertion(codeLineage.body?.capture_observations?.length === 1 && codeLineage.body.capture_observations[0].frame_id === codeProducer.frame_id && codeLineage.body.capture_observations[0].frame_kind === "delegate", "delegated code capture lost its child frame");
    assertion(nativeLineage.status === 200, "delegated native lineage route failed");
    assertion(nativeLineage.body?.producer?.kind === "non_cell" && nativeLineage.body.producer.frame_id === nativeProducer.frame_id && nativeLineage.body.producer.frame_kind === "delegate" && nativeLineage.body.producer.producing_cell_id == null, "delegated native producer projection fabricated a Cell");
    assertion(!Array.isArray(nativeLineage.body?.capture_observations), "delegated native producer fabricated a capture observation");

    const codeReview = await openArtifactReview(page, codeProducer.filename);
    assertion(codeReview.text.includes(codeProducer.producing_cell_id), "delegated code UI hid the real Cell identity");
    assertion(codeReview.text.includes(codeProducer.frame_id) && /delegate frame/.test(codeReview.text), "delegated code UI hid the child frame");
    assertion(codeReview.view_code_links === 0, "delegated code UI fabricated a Notebook view-code link");
    const nativeReview = await openArtifactReview(page, nativeProducer.filename);
    assertion(nativeReview.text.includes(nativeProducer.frame_id) && /delegate frame/.test(nativeReview.text), "delegated native UI hid the child frame");
    assertion(/non-Cell action|非 Cell 动作/.test(nativeReview.text), "delegated native UI fabricated or hid its non-Cell identity");
    assertion(nativeReview.view_code_links === 0, "delegated native UI fabricated a Notebook view-code link");
    summary.delegated_provenance = {
      code_api_child_frame: true,
      code_ui_cell_identity: true,
      code_ui_child_frame: true,
      code_ui_fake_view_code_links: codeReview.view_code_links,
      native_api_non_cell: true,
      native_ui_child_frame: true,
      native_ui_fake_view_code_links: nativeReview.view_code_links,
    };

    const oldResponse = await page.request.get(new URL(seeded.expected[0].url, baseUrl).toString());
    const headResponse = await page.request.get(new URL(`/api/v1/artifacts/${encodeURIComponent(seeded.head_change.artifact_id)}`, baseUrl).toString());
    const oldBytes = Buffer.from(await oldResponse.body());
    const headBytes = Buffer.from(await headResponse.body());
    assertion(oldResponse.status() === 200 && sha256(oldBytes) === seeded.head_change.old_sha256, "old completion link changed after Artifact head moved");
    assertion(headResponse.status() === 200 && sha256(headBytes) === seeded.head_change.new_sha256, "mutable Artifact head did not expose the new bytes");
    assertion(seeded.head_change.old_version_id !== seeded.head_change.new_version_id && seeded.head_change.latest_version_id === seeded.head_change.new_version_id, "fixture did not move the Artifact head");
    summary.immutable_old_link = {
      old_link_status: oldResponse.status(),
      old_sha256_unchanged: sha256(oldBytes) === seeded.head_change.old_sha256,
      head_status: headResponse.status(),
      head_sha256_changed: sha256(headBytes) !== seeded.head_change.old_sha256,
      head_version_changed: seeded.head_change.old_version_id !== seeded.head_change.new_version_id,
    };

    assertion(pageErrors.length === 0, `page errors: ${pageErrors.join(" | ")}`);
  } catch (error) {
    const logs = `${daemonStdout()}\n${daemonStderr()}`;
    const suffix = daemon && daemon.exitCode !== null ? `; daemon exit ${daemon.exitCode}; ${sanitizeDiagnostic(logs)}` : "";
    fail(`${error?.message || error}${suffix}`);
  } finally {
    if (projectId && requestApi) {
      try {
        const deletion = await requestApi(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
        summary.cleanup.project_delete_status = deletion.status;
      } catch (error) { fail(`project cleanup failed: ${error?.message || error}`); }
      try {
        const projects = await requestApi("/api/v1/projects");
        summary.cleanup.project_absent = projects.status === 200
          && Array.isArray(projects.body?.projects)
          && !projects.body.projects.some((project) => (project.project_id || project.id) === projectId);
      } catch (error) { fail(`project cleanup readback failed: ${error?.message || error}`); }
      if (cleanupArtifactUrl) {
        try {
          const artifact = await requestApi(cleanupArtifactUrl);
          summary.cleanup.artifact_absent = artifact.status === 404;
        } catch (error) { fail(`Artifact cleanup readback failed: ${error?.message || error}`); }
      }
    }
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    summary.cleanup.daemon_stopped = await stopChild(daemon).catch(() => false);
    if (tripwire) {
      summary.live_llm_calls_observed = tripwire.count();
      await tripwire.close().catch(() => {});
      summary.cleanup.tripwire_stopped = true;
    }
    // Never remove storage out from under a child whose termination could not
    // be proved.  That failure leaves one owner-marked temp directory for
    // manual recovery instead of turning cleanup into a race with a live
    // daemon; the successful path below still removes the exact fixture.
    if (owned && summary.cleanup.daemon_stopped && fs.existsSync(owned.dataDir)) {
      try { summary.cleanup.data_dir_removed = removeOwnedDataDir(owned.dataDir, owned.markerValue); }
      catch (error) { fail(`data-dir cleanup failed: ${error?.message || error}`); }
    }
  }

  summary.agent_requests_sent = agentRequestsSent;
  summary.external_network_calls = externalNetworkCalls;
  if (agentRequestsSent !== 0) fail("the acceptance harness sent an Agent request");
  if (summary.live_llm_calls_observed !== 0) fail("the daemon attempted a live model call");
  if (externalNetworkCalls !== 0) fail("the browser attempted a non-daemon network request");
  if (summary.cleanup.project_absent !== true || summary.cleanup.artifact_absent !== true) fail("project cleanup did not prove durable resource absence");
  summary.cleanup.ok = summary.cleanup.project_absent === true
    && summary.cleanup.artifact_absent === true
    && summary.cleanup.daemon_stopped === true
    && summary.cleanup.data_dir_removed === true
    && summary.cleanup.tripwire_stopped === true;
  summary.failures = summary.failures.map(sanitizeDiagnostic);
  summary.passed = summary.failures.length === 0 && summary.cleanup.ok;
  try { validateSummarySchema(summary); }
  catch { fail("SUMMARY schema validation failed"); summary.passed = false; }
  process.exitCode = summary.passed ? 0 : 1;
  console.log(`SUMMARY ${serializeSummaryForOutput(summary, presentedToken)}`);
}

if (process.env.OPENAI4S_STAGE1_SELF_TEST === "1") {
  try {
    summary = await selfTest();
  } catch (error) {
    summary = makeSummary("self_test");
    fail(error?.message || error);
    summary.passed = false;
  }
  process.exitCode = summary.passed ? 0 : 1;
  console.log(`SUMMARY ${serializeSummaryForOutput(summary)}`);
} else {
  await runAcceptance();
}
