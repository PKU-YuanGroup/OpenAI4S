// Artifact preview on the sandbox origin: the acceptance that header checks cannot give.
//
// Every part of this feature verifies clean at the HTTP boundary while being
// broken in a browser -- that is not hypothetical, it is what happened: the
// sandbox responses were correct and the *shell's* own `frame-src` still
// refused to embed them, with nothing on the server able to notice. So the
// assertion here is the one that matters and the one only a browser can make:
// a model-authored document, framed by the workbench, ran its own script and
// loaded its own sibling file, from an origin that is not the app's.
//
// The harness owns everything it touches: a random loopback port, a temporary
// data directory, a daemon child, one project and two artifacts. Stdout
// carries exactly one machine-readable record:
//   SUMMARY { ... }

import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pythonPath = process.env.OPENAI4S_PYTHON
  ? path.resolve(process.env.OPENAI4S_PYTHON)
  : path.join(workspaceRoot, ".venv", "bin", "python");
const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;

const summary = {
  schema_version: 1,
  name: "artifact_sandbox_preview_browser_acceptance",
  app_origin: null,
  sandbox_origin: null,
  origins_differ: null,
  frame_src: null,
  script_ran: null,
  sibling_loaded: null,
  reported_origin: null,
  cross_frame_refused: null,
  inert_without_grant: null,
  ok: false,
};

function fail(message) {
  summary.error = message;
  process.stdout.write(`SUMMARY ${JSON.stringify(summary)}\n`);
  process.exit(1);
}

function assertion(condition, message) {
  if (!condition) fail(message);
}

async function allocateLoopbackPort() {
  return await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function waitUntil(label, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    let ready = false;
    try {
      ready = await predicate();
    } catch (error) {
      if (Date.now() > deadline) fail(`${label}: ${error.message || error}`);
    }
    if (ready) return;
    if (Date.now() > deadline) fail(`${label}: timed out after ${timeoutMs}ms`);
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    fail("playwright is not installed; run `npm ci --ignore-scripts` on Node 20+");
  }
}

async function main() {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "openai4s-sandbox-preview-"));
  const port = await allocateLoopbackPort();
  const appOrigin = `http://127.0.0.1:${port}`;
  // The sandbox origin the client derives: the other loopback name, same port.
  const sandboxOrigin = `http://localhost:${port}`;
  summary.app_origin = appOrigin;
  summary.sandbox_origin = sandboxOrigin;
  summary.origins_differ = appOrigin !== sandboxOrigin;

  const daemon = spawn(
    pythonPath,
    ["-m", "openai4s", "serve", "--no-browser", "--port", String(port)],
    {
      cwd: workspaceRoot,
      env: {
        ...process.env,
        OPENAI4S_DATA_DIR: dataDir,
        OPENAI4S_HOST: "127.0.0.1",
        OPENAI4S_PORT: String(port),
        OPENAI4S_REQUIRE_TOKEN: "1",
        OPENAI4S_ALLOW_NETWORK: "0",
        OPENAI4S_SKIP_DOTENV: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  daemon.stdout.resume();
  daemon.stderr.resume();

  let browser = null;
  try {
    await waitUntil(
      "daemon startup",
      async () => {
        if (daemon.exitCode !== null) throw new Error("daemon exited during startup");
        if (!fs.existsSync(path.join(dataDir, "access-token"))) return false;
        const response = await fetch(`${appOrigin}/health`).catch(() => null);
        return Boolean(response && response.ok);
      },
      60000,
    );
    const token = fs.readFileSync(path.join(dataDir, "access-token"), "utf8").trim();
    const headers = { "content-type": "application/json", "X-OpenAI4S-Token": token };
    const api = async (route, body) => {
      const response = await fetch(`${appOrigin}/api/v1${route}`, {
        method: body === undefined ? "GET" : "POST",
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      assertion(response.ok, `${route} answered ${response.status}`);
      return await response.json();
    };

    const projects = await api("/projects");
    const projectId = (projects.projects || [])[0]?.project_id;
    assertion(projectId, "no project to attach a session to");
    const frame = await api("/frames", { title: "sandbox preview", project_id: projectId });
    const other = await api("/frames", { title: "other session", project_id: projectId });

    // A document that reports what it managed to do, because from the app
    // origin nothing else about it is observable -- which is the point.
    const report = [
      "<!doctype html><html><body><h1 id=h>static</h1>",
      "<img id=i src='figure.png' width=8 height=8",
      " onload=\"say('sibling-loaded')\" onerror=\"say('sibling-failed')\">",
      "<script>function say(m){try{parent.postMessage({probe:m,origin:location.origin},'*')}catch(e){}}",
      "document.getElementById('h').textContent='drawn by script';say('script-ran');</script>",
      "</body></html>",
    ].join("");
    const pixel =
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
    const upload = async (frameId, filename, contentType, base64) =>
      await api("/uploads", {
        frame_id: frameId,
        filename,
        content_type: contentType,
        content_base64: base64,
      });
    const reportArtifact = await upload(
      frame.id,
      "report.html",
      "text/html",
      Buffer.from(report, "utf8").toString("base64"),
    );
    await upload(frame.id, "figure.png", "image/png", pixel);
    const secret = await upload(
      other.id,
      "secret.html",
      "text/html",
      Buffer.from("<html>OTHER SESSION</html>", "utf8").toString("base64"),
    );

    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ headless: true, executablePath });
    const context = await browser.newContext({ serviceWorkers: "block" });
    const page = await context.newPage();
    await page.goto(`${appOrigin}/?token=${encodeURIComponent(token)}`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => typeof window.renderHtmlPreview === "function", null, {
      timeout: 30000,
    });

    summary.frame_src = await page.evaluate(async () => {
      const response = await fetch("/", { headers: { accept: "text/html" } });
      const policy = response.headers.get("content-security-policy") || "";
      return (policy.split("; ").find((d) => d.startsWith("frame-src ")) || "").trim();
    });
    assertion(
      summary.frame_src.includes("http://localhost:"),
      `the shell must permit framing the sandbox origin; frame-src was ${summary.frame_src}`,
    );

    const reported = await page.evaluate(async (artifactId) => {
      const seen = [];
      window.addEventListener("message", (event) => seen.push(event.data));
      const host = document.createElement("div");
      document.body.appendChild(host);
      window.renderHtmlPreview(host, { id: artifactId });
      const deadline = Date.now() + 20000;
      while (Date.now() < deadline && seen.length < 2) {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      const frameElement = host.querySelector("iframe");
      return {
        seen,
        src: frameElement ? frameElement.src : "",
        sandbox: frameElement ? frameElement.getAttribute("sandbox") : "",
      };
    }, reportArtifact.artifact_id || reportArtifact.id);

    const probes = reported.seen.map((entry) => entry && entry.probe);
    summary.script_ran = probes.includes("script-ran");
    summary.sibling_loaded = probes.includes("sibling-loaded");
    summary.reported_origin = (reported.seen.find((entry) => entry && entry.origin) || {}).origin;
    assertion(summary.script_ran, "the framed document never executed its own script");
    assertion(summary.sibling_loaded, "the framed document could not load its sibling file");
    assertion(
      summary.reported_origin === sandboxOrigin,
      `the document ran on ${summary.reported_origin}, not the sandbox origin`,
    );
    assertion(
      reported.src.startsWith(`${sandboxOrigin}/sandbox/`),
      `the frame pointed at ${reported.src}`,
    );
    assertion(
      reported.sandbox === "allow-scripts allow-same-origin",
      `the frame carried sandbox="${reported.sandbox}"`,
    );

    // A grant is one session's. The whole scope of the credential, checked
    // from the browser rather than from curl, because this is where a leak
    // would actually be spent.
    const grantToken = reported.src.split("/sandbox/")[1].split("/")[0];
    const crossFrame = await page.evaluate(
      async ([origin, grant, ident]) => {
        const response = await fetch(`${origin}/sandbox/${grant}/preview/${ident}`, {
          mode: "no-cors",
        }).catch(() => null);
        // `no-cors` hides the status, so ask the document instead: a refusal
        // never becomes a readable frame.
        return response ? response.type : "network-error";
      },
      [sandboxOrigin, grantToken, secret.artifact_id || secret.id],
    );
    const crossFrameStatus = await fetch(
      `${sandboxOrigin}/sandbox/${grantToken}/preview/${secret.artifact_id || secret.id}`,
    );
    summary.cross_frame_refused = crossFrameStatus.status === 404;
    assertion(
      summary.cross_frame_refused,
      `a grant read another session's artifact (${crossFrameStatus.status}, ${crossFrame})`,
    );

    // When no grant can be had the preview must stay inert rather than fall
    // back to running model-authored script on the app origin. Driven through
    // the real failure path -- an artifact the grant route refuses -- because
    // that is the branch a user actually reaches.
    summary.inert_without_grant = await page.evaluate(async () => {
      const host = document.createElement("div");
      document.body.appendChild(host);
      window.renderHtmlPreview(host, { id: "a-does-not-exist" });
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const frameElement = host.querySelector("iframe");
      return {
        sandbox: frameElement.getAttribute("sandbox"),
        offSandboxOrigin: !frameElement.src.includes("/sandbox/"),
        noteShown: Boolean(host.querySelector(".renderer-noscript")),
      };
    });
    assertion(
      summary.inert_without_grant.sandbox === ""
        && summary.inert_without_grant.offSandboxOrigin
        && summary.inert_without_grant.noteShown,
      `a refused grant did not leave the preview inert: ${JSON.stringify(summary.inert_without_grant)}`,
    );

    summary.ok = true;
    process.stdout.write(`SUMMARY ${JSON.stringify(summary)}\n`);
  } finally {
    if (browser) await browser.close().catch(() => {});
    daemon.kill("SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 500));
    if (daemon.exitCode === null) daemon.kill("SIGKILL");
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

main().catch((error) => fail(error && error.stack ? error.stack : String(error)));
