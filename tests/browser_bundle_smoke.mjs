// The Linux package's science chain, driven through a real browser.
//
// The wheel smoke proves an installed package serves its Web shell; the
// installed-runtime probe proves a packaged daemon can execute Cells and
// capture an Artifact. Neither touches the surface a user actually works
// through. This harness closes the last gap for the Linux tarball: it unpacks
// the exact artifact the release publishes, starts its own packaged daemon in
// an isolated data directory on a random loopback port (Notebook REPL enabled
// for this process only — the shipped default is untouched), opens the real
// workbench in headless Chromium, and drives one deterministic science
// scenario end to end:
//
//   authenticate → open a session → a Python Cell writes a fixed CSV →
//   a second Cell produces a figure → both are captured as immutable
//   Artifact versions (version_id + SHA-256 recorded) → the same filename is
//   written again, and the first version still opens by its exact id while a
//   new version exists → the browser is closed, the daemon is stopped and
//   waited for, the same data directory is reopened, and the same version
//   bytes and execution history are read back in a fresh browser → the daemon
//   and its kernel worker are gone.
//
// It calls no model (there is no key and no turn is started) and reaches
// nothing but loopback. Budgets are the R6 numbers: 90 s for each service
// start, 60 s per Cell, 120 s per browser scene, 30 s to clean up, 420 s
// overall, with the total authoritative. A timeout produces a bounded
// diagnostic and a failed report; cleanup runs in `finally` and only ever
// signals the daemon this run started.
//
// The report (`--output`) is the `bundle_smoke` document the Linux build
// receipt carries (scripts/release_receipts.py validates its fixed shape). On
// a host that cannot execute a linux-x86_64 bundle it records `not_run` with
// the missing run named — and exit code 3 — never a pass.
//
//   node tests/browser_bundle_smoke.mjs --bundle dist/OpenAI4S-*-linux-x86_64.tar.gz \
//     --source-sha "$SHA" --output "$RUNNER_TEMP/bundle-smoke.json"
//
// A development mode repeats the same scenes against a prepared source tree
// (`--python … --source-root …`, as the installed-runtime probe does): it
// exercises the browser plumbing, but its report records `figure_tool:
// "stdlib-png"` and no package digest, so the receipt gate refuses it. The
// packaged run is the evidence.

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import { spawn, spawnSync } from "node:child_process";

import {
  authenticate,
  boundedLogCollector,
  minimalChildEnvironment,
  redactSecrets,
  skipFirstRunWizard,
  waitUntil,
} from "./browser_auth.mjs";

const FORMAT = "openai4s-bundle-browser-smoke";
const SCHEMA_VERSION = 1;
const SCENARIO_VERSION = 1;

const PASSED = "passed";
const FAILED = "failed";
const NOT_RUN = "not_run";

const EXIT_PASSED = 0;
const EXIT_FAILED = 1;
const EXIT_USAGE = 2;
const EXIT_NOT_RUN = 3;

//: R6 budgets, seconds. `total` is authoritative: each phase gets the smaller
//: of its own budget and what is left.
const BUDGETS = Object.freeze({
  service: 90,
  cell: 60,
  browser: 120,
  cleanup: 30,
  total: 420,
});

const CHECKS = Object.freeze([
  "service",
  "login",
  "kernel",
  "figure",
  "history",
  "restart",
  "processes",
]);

const CSV_FILENAME = "bundle-smoke.csv";
const PNG_FILENAME = "bundle-smoke.png";
const CSV_ONE = "revision,value\n1,11\n";
const CSV_TWO = "revision,value\n2,22\n";
const CSV_ONE_MARKER = "csv revision 1";
const CSV_TWO_MARKER = "csv revision 2";
const FIGURE_MARKER = "figure drawn";
const PNG_MARKER = "png written";
const PNG_MAGIC = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

class SmokeError extends Error {}

// ---------------------------------------------------------------------------
// Command line
// ---------------------------------------------------------------------------
function usage(stream = process.stderr) {
  stream.write(
    "usage: node tests/browser_bundle_smoke.mjs --bundle <tar.gz|dir> --output <report.json>\n" +
      "         [--source-sha <sha>] [--package-sha256 <sha>]\n" +
      "       node tests/browser_bundle_smoke.mjs --python <interpreter> --source-root <tree>\n" +
      "         --output <report.json> [--source-sha <sha>]\n"
  );
}

function parseArguments(argv) {
  const options = {
    bundle: "",
    python: "",
    sourceRoot: "",
    output: "",
    sourceSha: "",
    packageSha256: "",
    keepWorkDir: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const take = () => {
      const value = argv[index + 1];
      if (value === undefined || value.startsWith("--")) {
        throw new SmokeError(`${flag} needs a value`);
      }
      index += 1;
      return value;
    };
    switch (flag) {
      case "--bundle":
        options.bundle = take();
        break;
      case "--python":
        options.python = take();
        break;
      case "--source-root":
        options.sourceRoot = take();
        break;
      case "--output":
        options.output = take();
        break;
      case "--source-sha":
        options.sourceSha = take();
        break;
      case "--package-sha256":
        options.packageSha256 = take();
        break;
      case "--keep-work-dir":
        options.keepWorkDir = true;
        break;
      case "--help":
      case "-h":
        options.help = true;
        break;
      default:
        throw new SmokeError(`unknown argument: ${flag}`);
    }
  }
  if (options.help) return options;
  if (!options.output) throw new SmokeError("--output is required");
  if (Boolean(options.bundle) === Boolean(options.python)) {
    throw new SmokeError("pass exactly one of --bundle or --python");
  }
  if (options.python && !options.sourceRoot) {
    throw new SmokeError("--python needs --source-root (the tree it must import)");
  }
  return options;
}

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sha256Bytes(data) {
  return crypto.createHash("sha256").update(data).digest("hex");
}

function sha256File(target) {
  const hasher = crypto.createHash("sha256");
  const descriptor = fs.openSync(target, "r");
  try {
    const buffer = Buffer.alloc(1024 * 1024);
    for (;;) {
      const read = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (!read) break;
      hasher.update(buffer.subarray(0, read));
    }
  } finally {
    fs.closeSync(descriptor);
  }
  return hasher.digest("hex");
}

function bounded(text, limit = 4000) {
  const value = String(text ?? "");
  return value.length <= limit ? value : `${value.slice(0, limit)}… (${value.length - limit} more characters)`;
}

function normalizedArch(arch = os.arch()) {
  if (arch === "x64") return "x86_64";
  if (arch === "arm64") return "aarch64";
  return arch.toLowerCase();
}

function hostOs() {
  return os.platform() === "darwin" ? "darwin" : os.platform();
}

function bundleArch(name) {
  const matched = /^OpenAI4S-([^-]+)-linux-(x86_64|aarch64)(?:\.tar\.gz)?$/.exec(name);
  if (!matched) {
    throw new SmokeError(`bundle name ${JSON.stringify(name)} is not OpenAI4S-<version>-linux-<arch>`);
  }
  return matched[2];
}

function freeLoopbackPort() {
  // The daemon binds the port moments later; a collision exits it with a bind
  // error that the boot phase reports, rather than silently using another one.
  const server = net.createServer();
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function processGroupAlive(pgid) {
  if (!pgid) return false;
  try {
    process.kill(-pgid, 0);
    return true;
  } catch (error) {
    return error.code !== "ESRCH";
  }
}

async function waitForGroupExit(pgid, graceMs) {
  const deadline = Date.now() + graceMs;
  while (Date.now() < deadline) {
    if (!processGroupAlive(pgid)) return true;
    await sleep(100);
  }
  return false;
}

// ---------------------------------------------------------------------------
// The runtime under test
// ---------------------------------------------------------------------------
function runtimeForBundle(app, packageSha) {
  const runtime = path.join(app, "runtime", "bin", "python3");
  const sourceRoot = path.join(app, "src");
  for (const [target, label] of [
    [runtime, "embedded interpreter"],
    [path.join(sourceRoot, "openai4s"), "source tree"],
  ]) {
    if (!fs.existsSync(target)) {
      throw new SmokeError(`bundle is missing its ${label}: ${target}`);
    }
  }
  return {
    kind: "bundle",
    name: path.basename(app),
    interpreter: runtime,
    prefix: ["-P"],
    pythonPath: sourceRoot,
    packageSha,
    platform: { os: "linux", arch: bundleArch(path.basename(app)) },
  };
}

function runtimeForSource(interpreter, sourceRoot) {
  if (!fs.existsSync(interpreter)) {
    throw new SmokeError(`runtime interpreter does not exist: ${interpreter}`);
  }
  return {
    kind: "source",
    name: path.basename(sourceRoot),
    interpreter,
    prefix: ["-P"],
    pythonPath: sourceRoot,
    packageSha: "",
    platform: { os: hostOs(), arch: normalizedArch() },
  };
}

function runtimeCommand(runtime, ...arguments_) {
  return [runtime.interpreter, ...runtime.prefix, "-m", "openai4s", ...arguments_];
}

function spawnCommand(argv, options) {
  // `spawn` wants the program and its arguments separately; every command this
  // file runs goes through one place so a missing interpreter reports as a
  // spawn error rather than a shell string.
  return spawn(argv[0], argv.slice(1), options);
}

function daemonEnvironment(runtime, { dataDir, home, tempDir, port }) {
  const environment = minimalChildEnvironment({
    OPENAI4S_DATA_DIR: dataDir,
    OPENAI4S_HOST: "127.0.0.1",
    OPENAI4S_PORT: String(port),
    // Enabled here, for this process only: the shipped default stays
    // read-only because the release runs the Notebook as a scripted
    // verification client, not because the product changed.
    OPENAI4S_NOTEBOOK_REPL: "1",
    // This run measures the science chain, not the OS boundary. The boundary
    // has its own gate (the Linux bubblewrap smokes attested as release check
    // suites); a nested CI runner cannot create the user namespaces `auto`
    // needs, and failing closed here would abort the chain for a reason that
    // gate already covers.
    OPENAI4S_KERNEL_SANDBOX: "off",
    MPLBACKEND: "Agg",
    HOME: home,
    TMPDIR: tempDir,
    // The bundled launcher sets this so an on-demand `pip install` from a Cell
    // lands in a private user site under the data directory; the kernel
    // allowlists it, so set it here too rather than inheriting a bare ~/.local.
    PYTHONUSERBASE: path.join(dataDir, "pysite"),
  });
  if (runtime.pythonPath) environment.PYTHONPATH = runtime.pythonPath;
  return environment;
}

// ---------------------------------------------------------------------------
// Daemon lifecycle
// ---------------------------------------------------------------------------
class Daemon {
  constructor({ runtime, environment, cwd, logPath, port }) {
    this.runtime = runtime;
    this.environment = environment;
    this.cwd = cwd;
    this.logPath = logPath;
    this.port = port;
    this.token = "";
    this.child = null;
    this.pgid = 0;
    this.stopResult = null;
  }

  start() {
    fs.mkdirSync(path.dirname(this.logPath), { recursive: true });
    const descriptor = fs.openSync(this.logPath, "w");
    try {
      this.child = spawnCommand(runtimeCommand(this.runtime, "serve", "--no-open"), {
        cwd: this.cwd,
        env: this.environment,
        stdio: ["ignore", descriptor, descriptor],
        // Its own session: the harness can wait for, and if necessary stop,
        // exactly this tree without ever signalling a process it did not start.
        detached: true,
      });
      this.pgid = this.child.pid;
      this.child.on("error", () => {});
      this.child.unref();
    } finally {
      fs.closeSync(descriptor);
    }
  }

  tail(limit = 2000) {
    try {
      return bounded(
        redactSecrets(fs.readFileSync(this.logPath, "utf8").slice(-16384), this.token),
        limit
      );
    } catch {
      return "";
    }
  }

  alive() {
    if (!this.child) return false;
    return this.child.exitCode === null && this.child.signalCode === null;
  }

  async waitUntilServing(timeoutMs) {
    if (!this.child) throw new SmokeError("daemon was never started");
    const deadline = Date.now() + timeoutMs;
    let last = "no response";
    while (Date.now() < deadline) {
      if (!this.alive()) {
        throw new SmokeError(`the runtime daemon exited before serving: ${this.tail()}`);
      }
      try {
        const response = await fetch(`http://127.0.0.1:${this.port}/health`, {
          signal: AbortSignal.timeout(2000),
        });
        const payload = await response.json();
        if (response.ok && payload && payload.status === "ok") return;
        last = `health status ${response.status}`;
      } catch (error) {
        last = error.name || "request failed";
      }
      await sleep(200);
    }
    throw new SmokeError(
      `the runtime daemon did not answer /health within ${(timeoutMs / 1000).toFixed(0)}s (${last})`
    );
  }

  async readToken(dataDir, timeoutMs) {
    const tokenFile = path.join(dataDir, "access-token");
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        const token = fs.readFileSync(tokenFile, "utf8").trim();
        if (token) return token;
      } catch {
        // not minted yet
      }
      if (!this.alive()) {
        throw new SmokeError(
          `the runtime daemon exited before minting its access token: ${this.tail()}`
        );
      }
      await sleep(100);
    }
    throw new SmokeError("the runtime daemon minted no access token");
  }

  async stop(timeoutMs) {
    if (this.stopResult) return this.stopResult;
    const result = {
      attempted: Boolean(this.child),
      daemonExited: false,
      workersExited: false,
      cliReturnCode: null,
      note: "",
    };
    if (!this.child) {
      result.note = "no daemon was started";
      this.stopResult = result;
      return result;
    }
    let failure = "";
    const stopper = spawnCommand(runtimeCommand(this.runtime, "stop"), {
      cwd: this.cwd,
      env: this.environment,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const collector = boundedLogCollector(stopper.stderr, 8192);
    const deadline = Date.now() + timeoutMs;
    let returnCode = null;
    while (returnCode === null && Date.now() < deadline) {
      // Reap the daemon as it exits; only then can the group check believe a
      // worker has left.
      if (!this.alive()) this.child.exitCode;
      await sleep(50);
      returnCode = stopper.exitCode;
    }
    if (returnCode === null) {
      stopper.kill("SIGKILL");
      failure = `\`openai4s stop\` did not return within ${(timeoutMs / 1000).toFixed(0)}s`;
    } else {
      result.cliReturnCode = stopper.exitCode;
      if (stopper.exitCode !== 0) {
        failure = `\`openai4s stop\` exited ${stopper.exitCode}: ${bounded(collector(), 800)}`;
      }
    }
    // Wait for the daemon to be reaped, then for its kernel worker to leave
    // the process group. Only this run's own tree is ever signalled.
    const settle = Math.max(1000, Math.min(10000, timeoutMs / 2));
    if (this.alive()) {
      if (!failure) failure = "`openai4s stop` exited 0 but the daemon kept running";
      await waitUntil("the daemon to exit", () => !this.alive(), settle).catch(() => {});
    }
    if (this.alive()) {
      try {
        process.kill(this.child.pid, "SIGTERM");
      } catch {
        // already gone
      }
      await waitUntil("the daemon to exit", () => !this.alive(), settle).catch(() => {});
    }
    if (this.alive()) {
      try {
        process.kill(this.child.pid, "SIGKILL");
      } catch {
        // already gone
      }
    }
    result.daemonExited = !this.alive();
    if (result.daemonExited) {
      result.workersExited = await waitForGroupExit(this.pgid, Math.max(1000, timeoutMs / 2));
    }
    if (!result.daemonExited) result.note = failure || "the daemon did not exit";
    else if (!result.workersExited) result.note = failure || "a kernel worker outlived the daemon";
    else result.note = failure;
    this.stopResult = result;
    return result;
  }
}

// ---------------------------------------------------------------------------
// REST helpers (cookie-authenticated, like the workbench itself)
// ---------------------------------------------------------------------------
function api(page, baseUrl, route, { method = "GET", data } = {}) {
  const response = page.request.fetch(new URL(`api/v1${route}`, baseUrl).toString(), {
    method,
    data,
    headers: data === undefined ? undefined : { "Content-Type": "application/json" },
    failOnStatusCode: false,
  });
  return response.then(async (result) => {
    const body = await result.text();
    if (!result.ok()) {
      throw new SmokeError(`${method} ${route} returned HTTP ${result.status()}: ${bounded(body, 800)}`);
    }
    return body ? JSON.parse(body) : null;
  });
}

async function downloadExact(page, url) {
  const response = await page.request.get(url.startsWith("http") ? url : new URL(url, page.url()).toString());
  if (!response.ok()) {
    throw new SmokeError(`artifact version download returned HTTP ${response.status()}`);
  }
  return Buffer.from(await response.body());
}

function artifactBinding(entry, filename) {
  const rows = (entry?.output_artifacts || []).filter((row) => row && row.filename === filename);
  if (rows.length !== 1) {
    throw new SmokeError(
      `execution-log entry captured ${rows.length} ${JSON.stringify(filename)} version(s), expected 1`
    );
  }
  return rows[0];
}

async function versionRow(page, baseUrl, artifactId, versionId) {
  const listing = await api(page, baseUrl, `/artifacts/${encodeURIComponent(artifactId)}/versions`);
  const row = (listing?.versions || []).find((item) => item.version_id === versionId);
  if (!row) throw new SmokeError(`artifact ${artifactId} does not list version ${versionId}`);
  return row;
}

// ---------------------------------------------------------------------------
// Browser scenes
// ---------------------------------------------------------------------------
async function openSession(page, frameId, projectId) {
  await page.evaluate(
    async ({ fid, pid }) => window.openConversation(fid, pid),
    { fid: frameId, pid: projectId }
  );
  await waitUntil("the session opened", async () => {
    const current = await page.evaluate(() => (window.S && window.S.currentId) || "");
    return current === frameId || page.url().includes(frameId);
  });
}

async function showExactVersion(page, binding, expectedText, forbiddenText) {
  await page.evaluate(
    async (row) => {
      await window.openViewer({
        id: row.artifact_id,
        filename: row.filename,
        version_id: row.version_id,
      });
    },
    binding
  );
  await waitUntil(`exact version ${binding.version_id} renders`, async () => {
    const seen = await page.locator("#dock-viewer").innerText().catch(() => "");
    return seen.includes(expectedText);
  });
  const text = await page.locator("#dock-viewer").innerText();
  if (forbiddenText && text.includes(forbiddenText)) {
    throw new SmokeError(
      `the exact version ${binding.version_id} showed ${JSON.stringify(forbiddenText)}, which belongs to another version`
    );
  }
  return text;
}

async function showFigure(page, binding) {
  await page.evaluate(
    async (row) => {
      await window.openViewer({
        id: row.artifact_id,
        filename: row.filename,
        version_id: row.version_id,
      });
    },
    binding
  );
  await waitUntil("the captured figure renders", async () => {
    const image = page.locator("#dock-viewer img").first();
    if ((await image.count()) === 0) return false;
    return image.evaluate((node) => node.complete && node.naturalWidth > 0).catch(() => false);
  });
}

// ---------------------------------------------------------------------------
// The acceptance run
// ---------------------------------------------------------------------------
class Acceptance {
  constructor(options) {
    this.options = options;
    this.deadline = Date.now() + BUDGETS.total * 1000;
    this.startedAt = Date.now();
    this.workRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openai4s bundle smoke "));
    this.diagnostics = [];
    this.checks = Object.fromEntries(CHECKS.map((name) => [name, false]));
    this.checks.figure_tool = "";
    this.daemonEnvironmentNames = [];
    this.daemons = [];
    this.timings = {};
    this.runtime = null;
    this.browser = null;
    this.package = { kind: "", name: "", sha256: "" };
    this.platform = { os: "", arch: "" };
    this.artifact = {};
    this.firstVersion = null;
    this.secondVersion = null;
  }

  remaining() {
    return this.deadline - Date.now();
  }

  budget(name) {
    const left = this.remaining();
    if (left <= 0) {
      throw new SmokeError(`the overall ${BUDGETS.total}s acceptance budget expired`);
    }
    return Math.max(1000, Math.min(BUDGETS[name] * 1000, left));
  }

  async timed(name, milliseconds, action) {
    const started = Date.now();
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new SmokeError(`${name} timed out after ${(milliseconds / 1000).toFixed(0)}s`)),
        milliseconds
      );
    });
    try {
      return await Promise.race([action(), timeout]);
    } finally {
      clearTimeout(timer);
      this.timings[`${name}_s`] = Number(((Date.now() - started) / 1000).toFixed(3));
    }
  }

  async prepare() {
    const { options } = this;
    if (options.bundle) {
      const target = path.resolve(options.bundle);
      const name = path.basename(target);
      const arch = bundleArch(name);
      const hostArch = normalizedArch();
      const executable = hostOs().startsWith("linux") && hostArch === arch;
      this.package = {
        kind: "bundle",
        name,
        sha256: fs.statSync(target).isFile() ? sha256File(target) : options.packageSha256,
      };
      this.platform = { os: "linux", arch, hostOs: hostOs(), hostArch };
      if (!executable) {
        this.notRunReason =
          `a linux-${arch} bundle cannot be executed on ${hostOs()}/${hostArch}; ` +
          "run this acceptance on the matching Linux release runner";
        this.notRun = true;
        return;
      }
      const app =
        fs.statSync(target).isDirectory()
          ? target
          : this.unpack(target);
      this.runtime = runtimeForBundle(app, this.package.sha256);
      this.appDir = app;
    } else {
      this.runtime = runtimeForSource(path.resolve(options.python), path.resolve(options.sourceRoot));
      this.package = {
        kind: "source",
        name: this.runtime.name,
        sha256: options.packageSha256,
      };
      this.platform = { os: hostOs(), arch: normalizedArch(), hostOs: hostOs(), hostArch: normalizedArch() };
    }
    for (const name of ["data", "home", "tmp", "logs"]) {
      fs.mkdirSync(path.join(this.workRoot, name), { recursive: true });
    }
  }

  unpack(target) {
    const unpacked = path.join(this.workRoot, "unpacked");
    fs.mkdirSync(unpacked, { recursive: true });
    const result = spawnSync("tar", ["-xzf", target, "-C", unpacked], {
      encoding: "utf8",
    });
    if (result.status !== 0) {
      throw new SmokeError(`could not unpack the bundle: ${bounded(result.stderr || result.error, 800)}`);
    }
    const roots = fs.readdirSync(unpacked).map((entry) => path.join(unpacked, entry));
    if (roots.length !== 1) {
      throw new SmokeError(`expected one top-level directory in the archive, found ${roots.length}`);
    }
    return roots[0];
  }

  environment(dataDir, port) {
    return daemonEnvironment(this.runtime, {
      dataDir,
      home: path.join(this.workRoot, "home"),
      tempDir: path.join(this.workRoot, "tmp"),
      port,
    });
  }

  async launchDaemon(suffix) {
    const dataDir = path.join(this.workRoot, "data");
    const port = await freeLoopbackPort();
    const environment = this.environment(dataDir, port);
    if (this.daemonEnvironmentNames.length === 0) {
      // Variable names only, sorted: the receipt gate requires the knobs the
      // run actually handed the daemon, not a claim in prose.
      this.daemonEnvironmentNames = Object.keys(environment).sort();
    }
    const daemon = new Daemon({
      runtime: this.runtime,
      environment,
      cwd: this.workRoot,
      logPath: path.join(this.workRoot, "logs", `daemon-${suffix}.log`),
      port,
    });
    this.daemons.push(daemon);
    daemon.start();
    daemon.token = await this.timed(`service-${suffix}`, this.budget("service"), async () => {
      await daemon.waitUntilServing(this.budget("service"));
      return daemon.readToken(dataDir, 20000);
    });
    return { daemon, dataDir, port };
  }

  async launchBrowser(daemon) {
    const playwright = await this.importPlaywright();
    const { chromium } = playwright;
    const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
    const browser = await chromium.launch({ headless: true, executablePath });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    const baseUrl = `http://127.0.0.1:${daemon.port}/`;
    const token = await this.timed("login", this.budget("browser"), async () => {
      const accessToken = await authenticate(page, baseUrl, daemon.token);
      if (!accessToken) throw new SmokeError("the workbench did not accept the access token");
      await page.goto(baseUrl, { waitUntil: "networkidle" }).catch(() => {});
      await skipFirstRunWizard(page, baseUrl);
      await page.locator("#dashboard").waitFor({ state: "visible", timeout: 30000 });
      return accessToken;
    });
    return { browser, context, page, baseUrl, pageErrors, token };
  }

  async importPlaywright() {
    try {
      return await import("playwright");
    } catch (error) {
      const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
      if (!fallback) throw new SmokeError(`playwright is not installed: ${error.message}`);
      return import(fallback);
    }
  }

  async executeCell(page, baseUrl, frameId, code, marker) {
    const payload = await this.timed(
      "cell",
      this.budget("cell"),
      () =>
        api(page, baseUrl, `/frames/${encodeURIComponent(frameId)}/kernel/execute`, {
          method: "POST",
          data: { language: "python", code, wait: true },
        })
    );
    if (payload?.error) throw new SmokeError(`a Cell was refused: ${bounded(payload.error, 800)}`);
    const cell = payload?.cell;
    if (!cell || cell.status !== "ok" || cell.error) {
      throw new SmokeError(
        `a Cell did not execute successfully: status=${JSON.stringify(cell?.status)} error=${JSON.stringify(bounded(cell?.error))}`
      );
    }
    if (!String(cell.stdout || "").includes(marker)) {
      throw new SmokeError(
        `a Cell printed ${JSON.stringify(bounded(cell.stdout, 400))}, expected the marker ${JSON.stringify(marker)}`
      );
    }
    return cell;
  }

  async entries(page, baseUrl, frameId) {
    const log = await api(page, baseUrl, `/frames/${encodeURIComponent(frameId)}/execution-log`);
    const entries = log?.entries;
    if (!Array.isArray(entries) || entries.length === 0) {
      throw new SmokeError("the execution history is empty");
    }
    return entries;
  }

  entryFor(entries, marker) {
    const matches = entries.filter((entry) => String(entry.source || "").includes(marker));
    if (matches.length !== 1) {
      throw new SmokeError(
        `execution history has ${matches.length} Cell(s) matching ${JSON.stringify(marker)}, expected 1`
      );
    }
    if (matches[0].status !== "ok" || matches[0].error) {
      throw new SmokeError(`the persisted Cell matching ${JSON.stringify(marker)} is not a success`);
    }
    return matches[0];
  }

  // -- scenes ------------------------------------------------------------
  async sceneScience(session) {
    const { page, baseUrl } = session;
    const project = await api(page, baseUrl, "/projects", {
      method: "POST",
      data: { name: "bundle browser smoke" },
    });
    const projectId = project?.project_id || project?.id;
    if (!projectId) throw new SmokeError("the runtime refused to create a project");
    const frame = await api(page, baseUrl, "/frames", {
      method: "POST",
      data: { project_id: projectId },
    });
    const frameId = frame?.id || frame?.frame_id;
    if (!frameId) throw new SmokeError("the runtime refused to create a session");
    await this.timed("session-open", this.budget("browser"), () => openSession(page, frameId, projectId));
    session.frameId = frameId;
    session.projectId = projectId;

    // Cell 1: the deterministic CSV through the real kernel.
    await this.executeCell(
      page,
      baseUrl,
      frameId,
      [
        "from pathlib import Path",
        `Path(${JSON.stringify(CSV_FILENAME)}).write_text(${JSON.stringify(CSV_ONE)}, encoding="utf-8")`,
        `print(${JSON.stringify(CSV_ONE_MARKER)})`,
      ].join("\n"),
      CSV_ONE_MARKER
    );
    this.checks.kernel = true;

    // Cell 2: a figure captured through the production artifact path.
    const figureCell =
      this.runtime.kind === "bundle"
        ? [
            "import matplotlib",
            "matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "plt.figure(figsize=(3, 2))",
            "plt.plot([0, 1, 2], [0, 1, 0])",
            "plt.title('bundle smoke')",
            `print(${JSON.stringify(FIGURE_MARKER)})`,
          ].join("\n")
        : [
            "from pathlib import Path",
            "import struct, zlib",
            "def png_chunk(kind, data):",
            "    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)",
            "image = (bytes([137, 80, 78, 71, 13, 10, 26, 10])",
            "         + png_chunk(b'IHDR', struct.pack('>2I5B', 1, 1, 8, 2, 0, 0, 0))",
            "         + png_chunk(b'IDAT', zlib.compress(bytes([0, 255, 0, 0])))",
            "         + png_chunk(b'IEND', b''))",
            `Path(${JSON.stringify(PNG_FILENAME)}).write_bytes(image)`,
            `print(${JSON.stringify(PNG_MARKER)})`,
          ].join("\n");
    const figureMarker = this.runtime.kind === "bundle" ? FIGURE_MARKER : PNG_MARKER;
    await this.executeCell(page, baseUrl, frameId, figureCell, figureMarker);
    this.checks.figure_tool = this.runtime.kind === "bundle" ? "matplotlib" : "stdlib-png";

    const entries = await this.entries(page, baseUrl, frameId);
    const csvEntry = this.entryFor(entries, CSV_ONE_MARKER);
    const figureEntry = this.entryFor(entries, figureMarker);
    const csvBinding = artifactBinding(csvEntry, CSV_FILENAME);
    const figureName =
      this.runtime.kind === "bundle" ? (figureEntry.figures || [])[0] : PNG_FILENAME;
    if (!figureName) {
      throw new SmokeError("the figure Cell captured no figure through the daemon");
    }
    const figureBinding = artifactBinding(figureEntry, figureName);

    // Both versions opened in the real viewer, and their exact bytes hashed.
    await this.timed("csv-viewer", this.budget("browser"), () =>
      showExactVersion(page, csvBinding, "11")
    );
    await this.timed("figure-viewer", this.budget("browser"), () => showFigure(page, figureBinding));
    const csvRow = await versionRow(page, baseUrl, csvBinding.artifact_id, csvBinding.version_id);
    const csvBytes = await downloadExact(page, csvBinding.url);
    if (csvBytes.toString("utf8") !== CSV_ONE) {
      throw new SmokeError(`${CSV_FILENAME} does not contain the deterministic science result`);
    }
    if (csvRow.checksum !== sha256Bytes(csvBytes)) {
      throw new SmokeError(`${CSV_FILENAME} bytes do not match the version checksum`);
    }
    const figureRow = await versionRow(page, baseUrl, figureBinding.artifact_id, figureBinding.version_id);
    const figureBytes = await downloadExact(page, figureBinding.url);
    if (!figureBytes.subarray(0, 8).equals(PNG_MAGIC)) {
      throw new SmokeError("the captured figure is not a PNG");
    }
    if (figureRow.checksum !== sha256Bytes(figureBytes)) {
      throw new SmokeError("the captured figure bytes do not match the version checksum");
    }
    this.checks.figure = true;
    this.firstVersion = {
      artifact_id: csvBinding.artifact_id,
      version_id: csvBinding.version_id,
      url: csvBinding.url,
      filename: CSV_FILENAME,
      sha256: sha256Bytes(csvBytes),
      size_bytes: csvBytes.length,
    };
    this.firstFigure = {
      artifact_id: figureBinding.artifact_id,
      version_id: figureBinding.version_id,
      sha256: sha256Bytes(figureBytes),
    };
  }

  async sceneHistory(session) {
    const { page, baseUrl, frameId } = session;
    // Same filename again: a new immutable version, and the first one still
    // opens by its exact id.
    await this.executeCell(
      page,
      baseUrl,
      frameId,
      [
        "from pathlib import Path",
        `Path(${JSON.stringify(CSV_FILENAME)}).write_text(${JSON.stringify(CSV_TWO)}, encoding="utf-8")`,
        `print(${JSON.stringify(CSV_TWO_MARKER)})`,
      ].join("\n"),
      CSV_TWO_MARKER
    );
    const entries = await this.entries(page, baseUrl, frameId);
    const secondBinding = artifactBinding(this.entryFor(entries, CSV_TWO_MARKER), CSV_FILENAME);
    if (secondBinding.artifact_id !== this.firstVersion.artifact_id) {
      throw new SmokeError("writing the same filename again minted a different Artifact");
    }
    if (secondBinding.version_id === this.firstVersion.version_id) {
      throw new SmokeError("writing the same filename again did not mint a new version");
    }
    const secondRow = await versionRow(page, baseUrl, secondBinding.artifact_id, secondBinding.version_id);
    if (!secondRow.is_latest) throw new SmokeError("the newest version is not the Artifact's latest");
    const firstRow = await versionRow(page, baseUrl, this.firstVersion.artifact_id, this.firstVersion.version_id);
    if (firstRow.is_latest) throw new SmokeError("the first version is still marked latest");
    // The exact version the viewer opened before the second write still shows
    // its own content, after the second write landed.
    await this.timed("exact-version-after-write", this.budget("browser"), () =>
      showExactVersion(page, this.firstVersion, "11", "22")
    );
    const bytes = await downloadExact(page, this.firstVersion.url);
    if (sha256Bytes(bytes) !== this.firstVersion.sha256) {
      throw new SmokeError("the first version's bytes changed after a newer version was written");
    }
    this.secondVersion = {
      artifact_id: secondBinding.artifact_id,
      version_id: secondBinding.version_id,
      sha256: sha256Bytes(await downloadExact(page, secondBinding.url)),
    };
    this.checks.history = true;
  }

  async sceneRestart(session, firstDaemon) {
    const stop = await this.timed("cleanup", this.budget("cleanup"), () => firstDaemon.stop(this.budget("cleanup")));
    this.firstStop = stop;
    if (!stop.daemonExited || !stop.workersExited) {
      throw new SmokeError("the first daemon (or its kernel worker) did not exit, so the restart is not fresh");
    }
    await session.context.close();
    this.browser.close().catch(() => {});
    this.browser = null;

    const { daemon } = await this.launchDaemon("restart");
    this.restartDaemon = daemon;
    const reopened = await this.launchBrowser(daemon);
    this.browser = reopened.browser;
    this.restartSession = reopened;
    const { page, baseUrl } = reopened;
    await openSession(page, session.frameId, session.projectId);
    const entries = await this.entries(page, baseUrl, session.frameId);
    if (entries.length !== 3) {
      throw new SmokeError(
        `the reopened daemon reports ${entries.length} execution-history entries, expected 3`
      );
    }
    for (const entry of entries) {
      if (entry.status !== "ok" || entry.error) {
        throw new SmokeError("the reopened daemon reports a Cell that is not a success");
      }
    }
    const row = await versionRow(
      page,
      baseUrl,
      this.firstVersion.artifact_id,
      this.firstVersion.version_id
    );
    if (row.checksum !== this.firstVersion.sha256) {
      throw new SmokeError("the reopened daemon reports a different digest for the exact version");
    }
    const bytes = await downloadExact(page, this.firstVersion.url);
    if (sha256Bytes(bytes) !== this.firstVersion.sha256) {
      throw new SmokeError("the reopened daemon serves different bytes for the same version");
    }
    await this.timed("restart-viewer", this.budget("browser"), () =>
      showExactVersion(page, this.firstVersion, "11")
    );
    this.checks.restart = true;
    if (reopened.pageErrors.length) {
      throw new SmokeError(`browser page errors after restart: ${reopened.pageErrors.join(" | ")}`);
    }
  }

  async run() {
    try {
      await this.prepare();
      if (this.notRun) return this.notRunReport();
      const { daemon } = await this.launchDaemon("first");
      this.firstDaemon = daemon;
      const browser = await this.launchBrowser(daemon);
      this.browser = browser.browser;
      this.checks.service = true;
      this.checks.login = true;
      const session = { ...browser };
      await this.sceneScience(session);
      await this.sceneHistory(session);
      await this.sceneRestart(session, daemon);
      const finalStop = await this.timed("cleanup-final", this.budget("cleanup"), () =>
        this.restartDaemon.stop(this.budget("cleanup"))
      );
      this.finalStop = finalStop;
      if (!finalStop.daemonExited || !finalStop.workersExited) {
        throw new SmokeError("the reopened daemon (or its kernel worker) did not exit");
      }
      this.checks.processes = Boolean(
        this.firstStop?.daemonExited &&
          this.firstStop?.workersExited &&
          finalStop.daemonExited &&
          finalStop.workersExited
      );
      if (browser.pageErrors.length) {
        throw new SmokeError(`browser page errors: ${browser.pageErrors.join(" | ")}`);
      }
      return this.passedReport();
    } catch (error) {
      this.diagnostics.push(bounded(redactSecrets(String(error?.message || error))));
      return this.failedReport();
    } finally {
      await this.cleanup();
    }
  }

  async cleanup() {
    try {
      if (this.browser) await this.browser.close().catch(() => {});
    } catch {
      // ignore
    }
    for (const daemon of this.daemons) {
      if (daemon.stopResult) continue;
      try {
        await daemon.stop(Math.max(1000, Math.min(BUDGETS.cleanup * 1000, this.remaining())));
      } catch (error) {
        this.diagnostics.push(bounded(`shutdown raised ${error?.name || "error"}`));
      }
    }
    if (!this.options.keepWorkDir) {
      fs.rmSync(this.workRoot, { recursive: true, force: true });
    } else {
      this.diagnostics.push(`work directory kept at ${this.workRoot}`);
    }
  }

  // -- report ------------------------------------------------------------
  baseReport(status) {
    return {
      format: FORMAT,
      schema_version: SCHEMA_VERSION,
      scenario_version: SCENARIO_VERSION,
      status,
      artifact_sha256: this.package.sha256 || "",
      source_sha: this.options.sourceSha || "",
      platform: { os: this.platform.os, arch: this.platform.arch },
      package: { kind: this.package.kind, name: this.package.name },
      figure_tool: this.checks.figure_tool || "",
      checks: Object.fromEntries(CHECKS.map((name) => [name, Boolean(this.checks[name])])),
      artifact: this.firstVersion
        ? { version_id: this.firstVersion.version_id, sha256: this.firstVersion.sha256 }
        : {},
      timings: {
        ...this.timings,
        total_s: Number(((Date.now() - this.startedAt) / 1000).toFixed(3)),
        budgets_s: { ...BUDGETS },
      },
      diagnostics: this.diagnostics.slice(-8).map((item) => bounded(item)),
      posture: {
        notebook_repl: "enabled for this run only",
        kernel_sandbox: "off (the OS boundary has its own release gate)",
        model_calls: "none",
        daemon_environment: this.daemonEnvironmentNames,
      },
    };
  }

  passedReport() {
    return this.baseReport(PASSED);
  }

  failedReport() {
    const report = this.baseReport(FAILED);
    report.checks = Object.fromEntries(CHECKS.map((name) => [name, Boolean(this.checks[name])]));
    return report;
  }

  notRunReport() {
    return {
      ...this.baseReport(NOT_RUN),
      not_run_reason: bounded(this.notRunReason || "this host cannot execute the bundle"),
      checks: {},
      artifact: {},
      timings: {},
    };
  }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------
function writeReport(target, report) {
  const destination = path.resolve(target);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, `${JSON.stringify(report, null, 2)}\n`, "utf8");
}

let options;
try {
  options = parseArguments(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`bundle browser smoke: ${error.message}\n`);
  usage();
  process.exit(EXIT_USAGE);
}
if (options.help) {
  usage(process.stdout);
  process.exit(EXIT_PASSED);
}

const acceptance = new Acceptance(options);
let report;
try {
  report = await acceptance.run();
} catch (error) {
  // `run` handles its own failures; this only catches a programming error in
  // the harness itself, which must still leave a failed report rather than a
  // bare stack trace.
  report = acceptance.failedReport();
  report.diagnostics = [
    bounded(redactSecrets(`harness error: ${error?.stack || error}`)),
    ...(report.diagnostics || []),
  ].slice(-8);
}
writeReport(options.output, report);

if (report.status === NOT_RUN) {
  process.stdout.write(`bundle browser smoke: not_run — ${report.not_run_reason}\n`);
  process.exit(EXIT_NOT_RUN);
}
if (report.status !== PASSED) {
  const first = (report.diagnostics || ["no diagnostic recorded"])[0];
  process.stderr.write(`bundle browser smoke: FAILED — ${first}\n`);
  process.stderr.write(`report: ${path.resolve(options.output)}\n`);
  process.exit(EXIT_FAILED);
}
process.stdout.write(
  `bundle browser smoke: passed — ${report.artifact.version_id} ` +
    `${String(report.artifact.sha256).slice(0, 12)} exact version, ` +
    `${report.figure_tool} figure, restart readback, processes exited ` +
    `(${report.timings.total_s}s)\n`
);
process.exit(EXIT_PASSED);
