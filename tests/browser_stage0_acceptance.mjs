// Stage 0 browser acceptance for facts that already exist in the product.
//
// This is deliberately disposable-only: it refuses the normal user daemon,
// creates one project, and deletes that exact project in finally. It never
// sends an Agent message, starts a kernel, calls a live model, or permits a
// request outside the selected loopback origin. It prints exactly one stdout
// record:
//   SUMMARY { ... }

import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { authenticate } from "./browser_auth.mjs";

const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function makeSummary(mode = "acceptance") {
  return {
    schema_version: 1,
    name: "stage0_browser_acceptance",
    mode,
    base_url: mode === "self_test" ? "<self-test>" : "<unvalidated>",
    agent_requests_sent: 0,
    live_llm_calls_observed: null,
    measurement_scope:
      "the harness counts its own Agent requests; provider calls are not observable at the browser boundary",
    external_network_calls: 0,
    daemon_binding: null,
    resource_identity_sha256: {
      project: null,
      frame: null,
      artifact: null,
    },
    frame_kernel_preflight: null,
    before_refresh: null,
    after_refresh: null,
    refresh_facts_unchanged: false,
    completion_projection_source: null,
    completion_link_click: null,
    cleanup: {
      attempted: false,
      delete_status: null,
      project_list_status: null,
      project_absent: null,
      artifact_readback_status: null,
      ok: true,
    },
    self_test_checks: null,
    current_gaps: [
      {
        id: "completion_artifact_url_unversioned",
        current_path: "/api/artifacts/<artifact_id>",
        current_status: 404,
        canonical_path: "/api/v1/artifacts/<artifact_id>",
        canonical_status: 200,
        planned_fix_stage: 1,
      },
      {
        id: "ketcher_placeholder",
        current_path: "/ketcher",
        current_state: "placeholder",
        planned_fix_stage: 9,
      },
      {
        id: "notebook_default_read_only",
        current_state: "passive_status_only",
        planned_live_notebook_stage: 8,
      },
    ],
    failures: [],
    passed: false,
  };
}

let summary = makeSummary();

function stripUrlSecrets(raw) {
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "<url>";
    return `${parsed.protocol}//${parsed.host}/<path>`;
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
      new RegExp(
        `${workspaceRoot.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[^\\s:]*`,
        "g",
      ),
      "<path>",
    )
    .replace(/(?:\/Users|\/home|\/private\/var|[A-Za-z]:\\)[^\s:]+/g, "<path>")
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/gi, "<dynamic_id>")
    .replace(/\b(?:proj|frame|artifact|version|run|turn|execution)[_-][A-Za-z0-9._:-]{6,}\b/gi, "<dynamic_id>")
    .replace(/\ba-[A-Za-z0-9._:-]{8,}\b/g, "<dynamic_id>")
    .slice(0, 500);
}

function serializeSummaryForOutput(candidate, presentedToken = null) {
  const serialized = JSON.stringify(candidate);
  if (typeof presentedToken !== "string" || presentedToken.length === 0) {
    return serialized;
  }
  // This is the final output boundary, after every validation/error branch.
  // A schema failure must not be able to re-serialize an earlier credential.
  return serialized.split(presentedToken).join("<redacted>");
}

function fail(message) {
  summary.failures.push(sanitizeDiagnostic(message));
}

function requireFact(condition, message) {
  if (!condition) fail(message);
}

function assertion(condition, message) {
  if (!condition) throw new Error(message);
}

function validateSummarySchema(candidate) {
  assertion(candidate && typeof candidate === "object" && !Array.isArray(candidate), "summary must be an object");
  assertion(candidate.schema_version === 1, "summary schema_version must be 1");
  assertion(candidate.name === "stage0_browser_acceptance", "summary name mismatch");
  assertion(candidate.mode === "acceptance" || candidate.mode === "self_test", "summary mode invalid");
  assertion(typeof candidate.base_url === "string", "summary base_url must be a string");
  assertion(candidate.agent_requests_sent === 0, "summary agent_requests_sent must be zero");
  assertion(candidate.live_llm_calls_observed === null, "summary must not invent a provider-call count");
  assertion(typeof candidate.measurement_scope === "string" && candidate.measurement_scope.length > 0, "summary measurement_scope missing");
  assertion(Number.isInteger(candidate.external_network_calls) && candidate.external_network_calls >= 0, "summary external_network_calls must be a non-negative integer");
  assertion(candidate.daemon_binding === null || (candidate.daemon_binding.verified === true && /^[0-9a-f]{64}$/.test(candidate.daemon_binding.state_sha256)), "summary daemon binding invalid");
  assertion(candidate.resource_identity_sha256 && typeof candidate.resource_identity_sha256 === "object", "summary resource hashes missing");
  for (const kind of ["project", "frame", "artifact"]) {
    const digest = candidate.resource_identity_sha256[kind];
    assertion(digest === null || /^[0-9a-f]{64}$/.test(digest), `summary ${kind} hash invalid`);
  }
  assertion(Array.isArray(candidate.current_gaps) && candidate.current_gaps.length === 3, "summary current_gaps invalid");
  assertion(Array.isArray(candidate.failures) && candidate.failures.every((item) => typeof item === "string"), "summary failures invalid");
  assertion(candidate.cleanup && typeof candidate.cleanup === "object", "summary cleanup missing");
  assertion(typeof candidate.cleanup.attempted === "boolean", "summary cleanup.attempted invalid");
  for (const field of ["delete_status", "project_list_status", "artifact_readback_status"]) {
    assertion(candidate.cleanup[field] === null || Number.isInteger(candidate.cleanup[field]), `summary cleanup.${field} invalid`);
  }
  assertion(candidate.cleanup.project_absent === null || typeof candidate.cleanup.project_absent === "boolean", "summary cleanup.project_absent invalid");
  assertion(typeof candidate.cleanup.ok === "boolean", "summary cleanup.ok invalid");
  assertion(typeof candidate.refresh_facts_unchanged === "boolean", "summary refresh flag invalid");
  assertion(typeof candidate.passed === "boolean", "summary passed invalid");
  if (candidate.mode === "acceptance" && candidate.base_url !== "<unvalidated>") {
    const parsed = new URL(candidate.base_url);
    assertion(parsed.protocol === "http:", "summary base_url must remain http");
    assertion(parsed.username === "" && parsed.password === "", "summary base_url contains userinfo");
    assertion(parsed.search === "" && parsed.hash === "", "summary base_url contains query or hash");
    assertion(parsed.pathname === "/", "summary base_url contains a path");
  }
  const serialized = JSON.stringify(candidate);
  assertion(!/\bBearer\s+(?!<redacted>)/i.test(serialized), "summary contains a bearer credential");
  assertion(!/[?&](?:token|api[_-]?key|secret|password)=[^<]/i.test(serialized), "summary contains a query credential");
  assertion(!serialized.includes(workspaceRoot), "summary contains the workspace path");
  assertion(!/\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i.test(serialized), "summary contains a dynamic UUID");
}

function validateDisposableBaseUrl(environment = process.env) {
  if (environment.OPENAI4S_BROWSER_DISPOSABLE !== "1") {
    throw new Error("OPENAI4S_BROWSER_DISPOSABLE=1 is required");
  }
  const raw = environment.OPENAI4S_BROWSER_URL;
  if (!raw) throw new Error("OPENAI4S_BROWSER_URL must name a disposable loopback daemon");
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("OPENAI4S_BROWSER_URL is not a valid URL");
  }
  if (parsed.protocol !== "http:") throw new Error("OPENAI4S_BROWSER_URL must use http");
  if (parsed.username || parsed.password) throw new Error("OPENAI4S_BROWSER_URL must not contain userinfo");
  if (parsed.search || parsed.hash) throw new Error("OPENAI4S_BROWSER_URL must not contain a query or hash");
  if (parsed.pathname !== "/") throw new Error("OPENAI4S_BROWSER_URL must not contain a path");
  if (parsed.hostname !== "127.0.0.1" && parsed.hostname !== "[::1]") {
    throw new Error("OPENAI4S_BROWSER_URL must use a numeric loopback host");
  }
  if (!parsed.port) throw new Error("OPENAI4S_BROWSER_URL must include an explicit disposable port");
  if (parsed.port === "8760") throw new Error("the user daemon port 8760 is forbidden");
  parsed.pathname = "/";
  return parsed;
}

function sameOrDescendant(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

function loopbackFamily(hostname) {
  if (hostname === "127.0.0.1") return "ipv4";
  if (hostname === "::1" || hostname === "[::1]") return "ipv6";
  return null;
}

function pidIsLive(pid, signalProbe = process.kill) {
  try {
    signalProbe(pid, 0);
    return true;
  } catch (error) {
    // POSIX EPERM means the process exists but this identity may not signal it.
    // ESRCH alone proves that no process currently owns the PID.
    if (error && error.code === "EPERM") return true;
    if (error && error.code === "ESRCH") return false;
    throw new Error("daemon pid liveness could not be verified");
  }
}

function parseLinuxProcessStartToken(raw) {
  // /proc field 2 (comm) may itself contain spaces and parentheses. Fields
  // after its final ')' begin at field 3; starttime is field 22, index 19.
  const close = raw.lastIndexOf(")");
  if (close < 0) return null;
  const fields = raw.slice(close + 2).trim().split(/\s+/);
  return fields.length >= 20 && fields[19] ? fields[19] : null;
}

function linuxProcessStartToken(pid) {
  let raw;
  try {
    raw = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
  } catch {
    return null;
  }
  return parseLinuxProcessStartToken(raw);
}

function validateDisposableDataDir(
  base,
  environment = process.env,
  pidLiveness = pidIsLive,
  processStartToken = linuxProcessStartToken,
) {
  if (Object.prototype.hasOwnProperty.call(environment, "OPENAI4S_TOKEN")) {
    throw new Error("OPENAI4S_TOKEN override is forbidden for disposable acceptance");
  }
  const configured = environment.OPENAI4S_DATA_DIR;
  if (!configured || !String(configured).trim()) {
    throw new Error("OPENAI4S_DATA_DIR must be set explicitly before authentication");
  }

  let resolved;
  try {
    resolved = fs.realpathSync(path.resolve(String(configured)));
  } catch {
    throw new Error("OPENAI4S_DATA_DIR must resolve to an existing directory");
  }
  const defaultConfigured = path.resolve(os.homedir(), ".openai4s");
  let defaultResolved = defaultConfigured;
  try {
    defaultResolved = fs.realpathSync(defaultConfigured);
  } catch {
    // A missing default directory is still compared by its resolved spelling.
  }
  if (
    sameOrDescendant(resolved, defaultConfigured) ||
    sameOrDescendant(resolved, defaultResolved)
  ) {
    throw new Error("the default OpenAI4S data directory is forbidden");
  }
  if (!fs.statSync(resolved).isDirectory()) {
    throw new Error("OPENAI4S_DATA_DIR must resolve to a directory");
  }

  const statePath = path.join(resolved, "daemon.json");
  const pidPath = path.join(resolved, "openai4s.pid");
  const tokenPath = path.join(resolved, "access-token");
  let stateRaw;
  let state;
  try {
    stateRaw = fs.readFileSync(statePath, "utf8");
    state = JSON.parse(stateRaw);
  } catch {
    throw new Error("disposable daemon state is missing or invalid");
  }
  let pid;
  try {
    pid = Number(fs.readFileSync(pidPath, "utf8").trim());
  } catch {
    throw new Error("disposable daemon pidfile is missing or invalid");
  }
  if (
    !state ||
    typeof state !== "object" ||
    !Number.isInteger(state.pid) ||
    !(state.pid_start === null || (typeof state.pid_start === "string" && state.pid_start.length > 0)) ||
    !Object.prototype.hasOwnProperty.call(state, "pid_start") ||
    !Number.isInteger(state.port) ||
    !Number.isInteger(state.started_at) ||
    typeof state.host !== "string" ||
    !Number.isInteger(pid) ||
    pid <= 0 ||
    state.pid !== pid
  ) {
    throw new Error("disposable daemon state and pidfile do not match");
  }
  let pidLive;
  try {
    pidLive = pidLiveness(pid);
  } catch {
    throw new Error("disposable daemon pid liveness could not be verified");
  }
  if (!pidLive) {
    throw new Error("disposable daemon pid is not live");
  }
  if (state.pid_start !== null) {
    const currentStart = processStartToken(pid);
    if (currentStart === null) {
      throw new Error("disposable daemon start token could not be verified");
    }
    if (currentStart !== state.pid_start) {
      throw new Error("disposable daemon pid identity does not match its start token");
    }
  }
  if (state.port !== Number(base.port)) {
    throw new Error("disposable daemon state port does not match the browser URL");
  }
  if (
    loopbackFamily(String(state.host || "")) === null ||
    loopbackFamily(String(state.host || "")) !== loopbackFamily(base.hostname)
  ) {
    throw new Error("disposable daemon state host does not match the browser URL");
  }

  let tokenPathStat;
  try {
    tokenPathStat = fs.lstatSync(tokenPath);
  } catch {
    throw new Error("disposable daemon access-token file is missing");
  }
  if (!tokenPathStat.isFile() || tokenPathStat.isSymbolicLink()) {
    throw new Error("disposable daemon access-token must be a regular file");
  }
  if (path.dirname(fs.realpathSync(tokenPath)) !== resolved) {
    throw new Error("disposable daemon access-token escapes its data directory");
  }
  let tokenFd;
  let token;
  try {
    tokenFd = fs.openSync(
      tokenPath,
      fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0),
    );
    const openedStat = fs.fstatSync(tokenFd);
    const currentPathStat = fs.statSync(tokenPath);
    if (
      !openedStat.isFile() ||
      openedStat.dev !== currentPathStat.dev ||
      openedStat.ino !== currentPathStat.ino
    ) {
      throw new Error("disposable daemon access-token identity changed");
    }
    if ((openedStat.mode & 0o077) !== 0) {
      throw new Error("disposable daemon access-token permissions are too broad");
    }
    token = fs.readFileSync(tokenFd, "utf8").trim();
  } catch (error) {
    if (error && /^disposable daemon/.test(String(error.message || ""))) throw error;
    throw new Error("disposable daemon access-token could not be opened safely");
  } finally {
    if (tokenFd !== undefined) fs.closeSync(tokenFd);
  }
  if (!token) throw new Error("disposable daemon access-token is empty");
  return {
    dataDir: resolved,
    stateSha256: sha256(Buffer.from(stateRaw, "utf8")),
    token,
  };
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function resourceIdentitySha256(kind, identifier) {
  return sha256(Buffer.from(`stage0-browser:${kind}:${identifier}`, "utf8"));
}

function normalizedArtifactPath(rawPath, artifactId) {
  return String(rawPath).split(encodeURIComponent(artifactId)).join("<artifact_id>");
}

function productionCompletionMessage(artifactId, filename) {
  const python = process.env.OPENAI4S_PYTHON
    ? path.resolve(process.env.OPENAI4S_PYTHON)
    : path.join(workspaceRoot, ".venv", "bin", "python");
  const program = [
    "import json, sys",
    "from openai4s.server.completions import completion_message",
    "payload = json.load(sys.stdin)",
    "message = completion_message(",
    "    {'output': {'summary': 'Stage 0 deterministic completion.'}},",
    "    [{'artifact_id': payload['artifact_id'], 'filename': payload['filename']}],",
    "    require_fallback=False,",
    ")",
    "json.dump({'message': message}, sys.stdout)",
  ].join("\n");
  const child = spawnSync(python, ["-c", program], {
    cwd: workspaceRoot,
    encoding: "utf8",
    input: JSON.stringify({ artifact_id: artifactId, filename }),
    env: {
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONUTF8: "1",
      OPENAI4S_SKIP_DOTENV: "1",
    },
    maxBuffer: 1024 * 1024,
  });
  if (child.error) throw child.error;
  if (child.status !== 0) {
    throw new Error("production completion_message child failed");
  }
  if (child.stderr !== "") {
    throw new Error("production completion_message child emitted stderr");
  }
  const parsed = JSON.parse(child.stdout);
  if (!parsed || typeof parsed.message !== "string" || !parsed.message) {
    throw new Error("production completion_message returned no message");
  }
  return parsed.message;
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

function expectSelfTestRejection(operation, message) {
  let rejected = false;
  try {
    operation();
  } catch {
    rejected = true;
  }
  assertion(rejected, message);
}

function disposableBindingSelfTest() {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "openai4s-stage0-self-test-"));
  const port = 18999;
  try {
    const state = {
      pid: process.pid,
      pid_start: null,
      host: "127.0.0.1",
      port,
      started_at: 1,
    };
    fs.writeFileSync(path.join(dataDir, "daemon.json"), JSON.stringify(state), {
      mode: 0o600,
    });
    fs.writeFileSync(path.join(dataDir, "openai4s.pid"), String(process.pid), {
      mode: 0o600,
    });
    fs.writeFileSync(path.join(dataDir, "access-token"), "self-test-token", {
      mode: 0o600,
    });
    const environment = {
      OPENAI4S_BROWSER_DISPOSABLE: "1",
      OPENAI4S_BROWSER_URL: `http://127.0.0.1:${port}/`,
      OPENAI4S_DATA_DIR: dataDir,
    };
    const base = validateDisposableBaseUrl(environment);
    const binding = validateDisposableDataDir(base, environment);
    assertion(binding.dataDir === fs.realpathSync(dataDir), "disposable data-dir binding failed");
    assertion(/^[0-9a-f]{64}$/.test(binding.stateSha256), "daemon state digest missing");
    assertion(binding.token === "self-test-token", "validated token was not captured once");

    const signalError = (code) => {
      const error = new Error(`signal probe ${code}`);
      error.code = code;
      throw error;
    };
    const permissionDeniedBinding = validateDisposableDataDir(
      base,
      environment,
      (probedPid) => pidIsLive(probedPid, () => signalError("EPERM")),
    );
    assertion(
      permissionDeniedBinding.stateSha256 === binding.stateSha256,
      "EPERM did not preserve the verified live-PID binding",
    );
    expectSelfTestRejection(
      () =>
        validateDisposableDataDir(base, environment, (probedPid) =>
          pidIsLive(probedPid, () => signalError("ESRCH")),
        ),
      "ESRCH was not treated as a missing daemon pid",
    );
    expectSelfTestRejection(
      () =>
        validateDisposableDataDir(base, environment, (probedPid) =>
          pidIsLive(probedPid, () => signalError("EIO")),
        ),
      "an unknown liveness-probe failure did not fail closed",
    );
    const procFields = Array.from({ length: 20 }, (_unused, index) =>
      String(index + 3),
    );
    assertion(
      parseLinuxProcessStartToken(
        `7 (worker name with ) punctuation) ${procFields.join(" ")}`,
      ) === "22",
      "Linux process start-token parser did not preserve field 22",
    );
    const tokenBoundState = { ...state, pid_start: "verified-start-token" };
    fs.writeFileSync(
      path.join(dataDir, "daemon.json"),
      JSON.stringify(tokenBoundState),
      { mode: 0o600 },
    );
    const tokenBound = validateDisposableDataDir(
      base,
      environment,
      pidIsLive,
      () => "verified-start-token",
    );
    assertion(
      tokenBound.stateSha256 !== binding.stateSha256,
      "start-token-bound daemon state did not receive its own digest",
    );
    expectSelfTestRejection(
      () => validateDisposableDataDir(base, environment, pidIsLive, () => null),
      "an unreadable declared process start token did not fail closed",
    );
    expectSelfTestRejection(
      () =>
        validateDisposableDataDir(
          base,
          environment,
          pidIsLive,
          () => "different-start-token",
        ),
      "a mismatched process start token did not fail closed",
    );
    fs.writeFileSync(path.join(dataDir, "daemon.json"), JSON.stringify(state), {
      mode: 0o600,
    });

    expectSelfTestRejection(
      () => validateDisposableDataDir(base, { ...environment, OPENAI4S_TOKEN: "override" }),
      "OPENAI4S_TOKEN override was not rejected",
    );
    expectSelfTestRejection(
      () =>
        validateDisposableDataDir(base, {
          ...environment,
          OPENAI4S_DATA_DIR: path.join(os.homedir(), ".openai4s"),
        }),
      "default data directory was not rejected",
    );
    const wrongPort = validateDisposableBaseUrl({
      ...environment,
      OPENAI4S_BROWSER_URL: `http://127.0.0.1:${port + 1}/`,
    });
    expectSelfTestRejection(
      () => validateDisposableDataDir(wrongPort, environment),
      "daemon state port mismatch was not rejected",
    );
    const deadPid = 99999999;
    fs.writeFileSync(
      path.join(dataDir, "daemon.json"),
      JSON.stringify({ ...state, pid: deadPid }),
      { mode: 0o600 },
    );
    fs.writeFileSync(path.join(dataDir, "openai4s.pid"), String(deadPid), {
      mode: 0o600,
    });
    expectSelfTestRejection(
      () => validateDisposableDataDir(base, environment),
      "dead daemon pid was not rejected",
    );
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

async function redactionAndSchemaSelfTest() {
  const secret = "stage0-secret-probe";
  const dynamic = "123e4567-e89b-42d3-a456-426614174000";
  const raw =
    `failure at ${workspaceRoot}/tests/private.js ` +
    `http://alice:password@127.0.0.1:19999/private/path?token=${secret}#frag ` +
    `Bearer ${secret} X-OpenAI4S-Token: ${secret} proj_stage0-private ${dynamic}\n` +
    "at hidden stack";
  const cleaned = sanitizeDiagnostic(raw);
  for (const forbidden of [secret, "alice", "password", "private/path", workspaceRoot, dynamic, "proj_stage0-private", "at hidden stack"]) {
    assertion(!cleaned.includes(forbidden), "redaction self-test failed");
  }
  const projected = productionCompletionMessage(
    "artifact-stage0-self-test",
    "stage0-self-test.txt",
  );
  assertion(
    projected.includes("/api/artifacts/artifact-stage0-self-test"),
    "production completion child self-test returned an unexpected link",
  );
  disposableBindingSelfTest();
  const visited = [];
  const explicitToken = "captured-self-test-token";
  const returnedToken = await authenticate(
    {
      goto: async (url) => visited.push(url),
      url: () => "http://127.0.0.1:18999/",
    },
    "http://127.0.0.1:18999/",
    explicitToken,
  );
  assertion(returnedToken === explicitToken, "authenticate did not retain the captured token");
  assertion(
    visited.length === 1 && new URL(visited[0]).searchParams.get("token") === explicitToken,
    "authenticate did not use the captured token",
  );
  const selfTestSummary = makeSummary("self_test");
  selfTestSummary.self_test_checks = {
    redaction: true,
    schema: true,
    disposable_binding: true,
    captured_token_authentication: true,
    production_completion: true,
    pid_liveness_portability: true,
  };
  selfTestSummary.failures = [];
  selfTestSummary.passed = true;
  validateSummarySchema(selfTestSummary);
  const invalid = { ...selfTestSummary, external_network_calls: "0" };
  let rejected = false;
  try {
    validateSummarySchema(invalid);
  } catch {
    rejected = true;
  }
  assertion(rejected, "schema self-test failed to reject an invalid summary");
  expectSelfTestRejection(
    () =>
      validateSummarySchema({
        ...selfTestSummary,
        cleanup: { ...selfTestSummary.cleanup, project_absent: "yes" },
      }),
    "schema self-test accepted a non-boolean project absence claim",
  );
  const doubleFault = {
    ...selfTestSummary,
    external_network_calls: "0",
    failures: [explicitToken],
  };
  expectSelfTestRejection(
    () => validateSummarySchema(doubleFault),
    "schema self-test accepted the dual-fault summary",
  );
  const doubleFaultOutput = serializeSummaryForOutput(doubleFault, explicitToken);
  assertion(
    !doubleFaultOutput.includes(explicitToken) &&
      doubleFaultOutput.includes("<redacted>"),
    "final SUMMARY serialization reintroduced a captured token after schema failure",
  );
  selfTestSummary.self_test_checks.credential_double_fault_redaction = true;
  return selfTestSummary;
}

async function runAcceptance() {
  let browser = null;
  let context = null;
  let requestApi = null;
  let projectId = null;
  let artifactId = null;
  let presentedToken = null;
  let externalNetworkCalls = 0;
  let agentRequestsSent = 0;
  try {
    // This validation must precede Playwright loading and authenticate(), whose
    // first operation is reading the daemon token.
    const base = validateDisposableBaseUrl();
    const binding = validateDisposableDataDir(base);
    process.env.OPENAI4S_DATA_DIR = binding.dataDir;
    const baseUrl = base.toString();
    summary.base_url = `${base.origin}/`;
    summary.daemon_binding = {
      verified: true,
      state_sha256: binding.stateSha256,
    };

    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ headless: true, executablePath });
    context = await browser.newContext({
      serviceWorkers: "block",
      viewport: { width: 1280, height: 900 },
    });
    await context.route("**/*", async (route) => {
      let destination;
      try {
        destination = new URL(route.request().url());
      } catch {
        externalNetworkCalls += 1;
        await route.abort("blockedbyclient");
        return;
      }
      const localOnlyProtocol =
        destination.protocol === "data:" ||
        destination.protocol === "blob:" ||
        destination.protocol === "about:";
      if (!localOnlyProtocol && (destination.protocol !== "http:" || destination.origin !== base.origin)) {
        externalNetworkCalls += 1;
        await route.abort("blockedbyclient");
        return;
      }
      await route.continue();
    });
    await context.routeWebSocket("**/*", async (socket) => {
      let destination;
      try {
        destination = new URL(socket.url());
      } catch {
        externalNetworkCalls += 1;
        await socket.close({ code: 1008, reason: "blocked by Stage 0 acceptance" });
        return;
      }
      const allowedWsOrigin = base.origin.replace(/^http:/, "ws:");
      if (destination.origin !== allowedWsOrigin) {
        externalNetworkCalls += 1;
        await socket.close({ code: 1008, reason: "blocked by Stage 0 acceptance" });
        return;
      }
      socket.connectToServer();
    });

    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(sanitizeDiagnostic(error && error.message ? error.message : error)));

    presentedToken = await authenticate(page, baseUrl, binding.token);
    if (!presentedToken) {
      throw new Error("disposable daemon authentication returned no token");
    }

    requestApi = async function request(apiPath, { method = "GET", data } = {}) {
      const target = new URL(apiPath, baseUrl);
      if (target.origin !== base.origin) {
        externalNetworkCalls += 1;
        throw new Error("blocked non-base-origin API request");
      }
      if (
        method === "POST" &&
        /^\/api\/v1\/frames\/[^/]+\/message$/.test(target.pathname)
      ) {
        agentRequestsSent += 1;
      }
      const response = await page.request.fetch(target.toString(), {
        method,
        data,
        headers: data === undefined ? undefined : { "content-type": "application/json" },
        maxRedirects: 0,
      });
      const bytes = await response.body();
      const text = bytes.toString("utf8");
      let body = null;
      try {
        body = JSON.parse(text);
      } catch {
        // Artifact and HTML routes intentionally return non-JSON bytes.
      }
      return { status: response.status(), bytes, text, body };
    };

    const suffix = `${Date.now()}-${process.pid}`;
    const projectResult = await requestApi("/api/v1/projects", {
      method: "POST",
      data: { name: `stage0-acceptance-${suffix}` },
    });
    projectId = projectResult.body && (projectResult.body.project_id || projectResult.body.id);
    requireFact(projectResult.status < 300 && !!projectId, "could not create project");
    if (!projectId) throw new Error("project creation did not return an id");
    summary.resource_identity_sha256.project = resourceIdentitySha256(
      "project",
      projectId,
    );

    const frameResult = await requestApi("/api/v1/frames", {
      method: "POST",
      data: { project_id: projectId },
    });
    const frameId = frameResult.body && (frameResult.body.frame_id || frameResult.body.id);
    requireFact(frameResult.status < 300 && !!frameId, "could not create session");
    if (!frameId) throw new Error("session creation did not return an id");
    summary.resource_identity_sha256.frame = resourceIdentitySha256("frame", frameId);

    const frameKernelPath = `/api/v1/frames/${encodeURIComponent(frameId)}/kernel`;
    const frameKernelPreflight = await requestApi(frameKernelPath);
    summary.frame_kernel_preflight = {
      status: frameKernelPreflight.status,
      repl_enabled: frameKernelPreflight.body && frameKernelPreflight.body.repl_enabled,
    };
    if (
      frameKernelPreflight.status !== 200 ||
      frameKernelPreflight.body?.repl_enabled !== false
    ) {
      throw new Error("frame kernel preflight did not prove repl_enabled=false");
    }

    const artifactBytes = Buffer.from("stage0 artifact truth\n", "utf8");
    const artifactSha256 = sha256(artifactBytes);
    const filename = "stage0-artifact.txt";
    const uploadResult = await requestApi("/api/v1/uploads", {
      method: "POST",
      data: {
        frame_id: frameId,
        project_id: projectId,
        filename,
        content_base64: artifactBytes.toString("base64"),
      },
    });
    artifactId = uploadResult.body && uploadResult.body.artifact_id;
    requireFact(uploadResult.status < 300 && !!artifactId, "could not upload artifact");
    if (!artifactId) throw new Error("artifact upload did not return an id");
    summary.resource_identity_sha256.artifact = resourceIdentitySha256(
      "artifact",
      artifactId,
    );

    const encodedArtifactId = encodeURIComponent(artifactId);
    const canonicalArtifactPath = `/api/v1/artifacts/${encodedArtifactId}`;
    const deepLink = new URL(
      `projects/${encodeURIComponent(projectId)}/frames/${encodeURIComponent(frameId)}`,
      baseUrl,
    ).toString();

    async function openNotebook() {
      const response = await page.goto(deepLink, { waitUntil: "domcontentloaded" });
      if (!response || !response.ok()) {
        throw new Error(`workspace deep link returned HTTP ${response?.status() ?? "unknown"}`);
      }
      await page.locator("#workspace:not(.hidden)").waitFor({ state: "visible" });
      await page.waitForFunction(() => typeof setActiveTab === "function");
      await page.evaluate(() => setActiveTab("notebook"));
      await page.locator("#dock-notebook:not(.hidden)").waitFor({ state: "visible" });
      await page.locator("#dock-notebook .nb-status").waitFor({ state: "attached" });
    }

    await openNotebook();
    const completionMarkdown = productionCompletionMessage(artifactId, filename);
    await page.evaluate(
      (markdown) => {
        const probe = document.createElement("div");
        probe.id = "stage0-completion-link-probe";
        probe.innerHTML = renderMd(markdown);
        document.body.appendChild(probe);
        const link = probe.querySelector("a");
        if (link) link.target = "_self";
      },
      completionMarkdown,
    );
    const renderedHref = await page.locator("#stage0-completion-link-probe a").getAttribute("href");
    if (!renderedHref) throw new Error("production completion projection rendered no Artifact link");
    const renderedTarget = new URL(renderedHref, baseUrl);
    if (renderedTarget.origin !== base.origin) {
      externalNetworkCalls += 1;
      throw new Error("production completion projection rendered an external link");
    }
    const currentCompletionPath = renderedTarget.pathname;
    summary.completion_projection_source = "openai4s.server.completions.completion_message+webui.renderMd";

    async function captureFacts() {
      const kernel = await requestApi(frameKernelPath);
      if (kernel.status !== 200 || kernel.body?.repl_enabled !== false) {
        throw new Error("frame kernel state did not permit the execute refusal probe");
      }
      const rejectedExecution = await requestApi(
        `/api/v1/frames/${encodeURIComponent(frameId)}/kernel/execute`,
        {
          method: "POST",
          data: {
            language: "python",
            code: "raise AssertionError('read-only Notebook must not execute this')",
          },
        },
      );
      const notebookDom = await page.evaluate(() => ({
        passive_status_count: document.querySelectorAll("#dock-notebook .nb-status").length,
        repl_count: document.querySelectorAll("#dock-notebook .nb-repl").length,
        repl_input_count: document.querySelectorAll("#dock-notebook .nb-repl-input").length,
      }));
      const ketcher = await requestApi("/ketcher");
      const currentArtifact = await requestApi(currentCompletionPath);
      const canonicalArtifact = await requestApi(canonicalArtifactPath);
      return {
        notebook: {
          kernel_status: kernel.status,
          repl_enabled: kernel.body && kernel.body.repl_enabled,
          execute_status: rejectedExecution.status,
          execute_error_code:
            rejectedExecution.body &&
            (/notebook REPL is disabled/i.test(rejectedExecution.body.error || "")
              ? "notebook_repl_disabled"
              : "unexpected"),
          dom: notebookDom,
        },
        ketcher: {
          status: ketcher.status,
          explicit_placeholder: /chemical structure editor placeholder/i.test(ketcher.text),
        },
        completion_artifact_url: {
          current_path: normalizedArtifactPath(currentCompletionPath, artifactId),
          current_status: currentArtifact.status,
          canonical_path: normalizedArtifactPath(canonicalArtifactPath, artifactId),
          canonical_status: canonicalArtifact.status,
          canonical_sha256: sha256(canonicalArtifact.bytes),
          expected_sha256: artifactSha256,
        },
      };
    }

    function checkFacts(facts, label) {
      requireFact(facts.notebook.kernel_status === 200, `${label}: kernel status was not 200`);
      requireFact(facts.notebook.repl_enabled === false, `${label}: default Notebook unexpectedly enabled the REPL`);
      requireFact(
        facts.notebook.execute_status === 403 && facts.notebook.execute_error_code === "notebook_repl_disabled",
        `${label}: disabled Notebook execution did not fail closed with 403`,
      );
      requireFact(
        facts.notebook.dom.passive_status_count === 1 &&
          facts.notebook.dom.repl_count === 0 &&
          facts.notebook.dom.repl_input_count === 0,
        `${label}: Notebook DOM was not passive/read-only`,
      );
      requireFact(
        facts.ketcher.status === 200 && facts.ketcher.explicit_placeholder === true,
        `${label}: /ketcher was not the explicit placeholder`,
      );
      requireFact(
        facts.completion_artifact_url.current_path === "/api/artifacts/<artifact_id>" &&
          facts.completion_artifact_url.current_status === 404,
        `${label}: production completion Artifact URL did not reproduce the known 404`,
      );
      requireFact(
        facts.completion_artifact_url.canonical_path === "/api/v1/artifacts/<artifact_id>" &&
          facts.completion_artifact_url.canonical_status === 200 &&
          facts.completion_artifact_url.canonical_sha256 === artifactSha256,
        `${label}: canonical Artifact URL did not return the uploaded bytes`,
      );
    }

    summary.before_refresh = await captureFacts();
    checkFacts(summary.before_refresh, "before refresh");

    const clickResponsePromise = page.waitForResponse(
      (response) => new URL(response.url()).pathname === currentCompletionPath,
    );
    await page.locator("#stage0-completion-link-probe a").click();
    const clickResponse = await clickResponsePromise;
    summary.completion_link_click = {
      rendered_href: normalizedArtifactPath(currentCompletionPath, artifactId),
      response_status: clickResponse.status(),
    };
    requireFact(clickResponse.status() === 404, "current completion Artifact link did not reproduce the known 404 on click");

    await openNotebook();
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#workspace:not(.hidden)").waitFor({ state: "visible" });
    await page.waitForFunction(() => typeof setActiveTab === "function");
    await page.evaluate(() => setActiveTab("notebook"));
    await page.locator("#dock-notebook .nb-status").waitFor({ state: "attached" });
    summary.after_refresh = await captureFacts();
    checkFacts(summary.after_refresh, "after refresh");
    summary.refresh_facts_unchanged = JSON.stringify(summary.before_refresh) === JSON.stringify(summary.after_refresh);
    requireFact(summary.refresh_facts_unchanged, "acceptance facts changed after refresh");
    requireFact(pageErrors.length === 0, `page errors: ${pageErrors.join(" | ")}`);
  } catch (error) {
    fail(error && error.message ? error.message : error);
  } finally {
    if (projectId && requestApi) {
      summary.cleanup.attempted = true;
      let deleteOk = false;
      let projectAbsent = false;
      let artifactReadbackOk = artifactId === null;
      try {
        const deletion = await requestApi(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
        summary.cleanup.delete_status = deletion.status;
        deleteOk =
          deletion.status >= 200 &&
          deletion.status < 300 &&
          deletion.body?.ok === true;
      } catch (error) {
        fail(`disposable project DELETE failed: ${error && error.message ? error.message : error}`);
      }
      try {
        const projectList = await requestApi("/api/v1/projects");
        summary.cleanup.project_list_status = projectList.status;
        projectAbsent =
          projectList.status === 200 &&
          Array.isArray(projectList.body?.projects) &&
          !projectList.body.projects.some(
            (project) =>
              project &&
              (project.project_id === projectId || project.id === projectId),
          );
        summary.cleanup.project_absent = projectAbsent;
      } catch (error) {
        summary.cleanup.project_absent = false;
        fail(`disposable project-list readback failed: ${error && error.message ? error.message : error}`);
      }
      if (artifactId !== null) {
        try {
          const artifactReadback = await requestApi(
            `/api/v1/artifacts/${encodeURIComponent(artifactId)}`,
          );
          summary.cleanup.artifact_readback_status = artifactReadback.status;
          artifactReadbackOk = artifactReadback.status === 404;
        } catch (error) {
          fail(`disposable Artifact readback failed: ${error && error.message ? error.message : error}`);
        }
      }
      summary.cleanup.ok = deleteOk && projectAbsent && artifactReadbackOk;
      if (!summary.cleanup.ok) {
        fail("disposable cleanup did not prove project-list and Artifact absence");
      }
    }
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
  }

  summary.agent_requests_sent = agentRequestsSent;
  summary.external_network_calls = externalNetworkCalls;
  requireFact(agentRequestsSent === 0, "an Agent request was sent by the acceptance harness");
  requireFact(externalNetworkCalls === 0, "a non-base-origin network request was attempted");
  summary.failures = summary.failures.map(sanitizeDiagnostic);
  summary.passed = summary.failures.length === 0 && summary.cleanup.ok === true;

  let serialized = JSON.stringify(summary);
  if (presentedToken && serialized.includes(presentedToken)) {
    fail("SUMMARY credential-containment self-check failed");
    summary.failures = summary.failures.map(sanitizeDiagnostic);
    summary.passed = false;
    serialized = JSON.stringify(summary).split(presentedToken).join("<redacted>");
  }
  try {
    validateSummarySchema(summary);
  } catch {
    fail("SUMMARY schema validation failed");
    summary.passed = false;
  }
  // Always serialize once more through the credential scrubber.  In
  // particular, the schema-error branch above must not restore the original
  // unsanitized object after the containment check already noticed a leak.
  serialized = serializeSummaryForOutput(summary, presentedToken);
  process.exitCode = summary.passed ? 0 : 1;
  console.log(`SUMMARY ${serialized}`);
}

if (process.env.OPENAI4S_STAGE0_SELF_TEST === "1") {
  try {
    summary = await redactionAndSchemaSelfTest();
  } catch (error) {
    summary = makeSummary("self_test");
    fail(error && error.message ? error.message : error);
    summary.passed = false;
  }
  process.exitCode = summary.passed ? 0 : 1;
  console.log(`SUMMARY ${JSON.stringify(summary)}`);
} else {
  await runAcceptance();
}
