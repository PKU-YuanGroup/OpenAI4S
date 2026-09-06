// Drive the committed Preact workbench, including the real Artifact Viewer.
// Header-only tests cannot prove that the shell permits the sandbox frame, or
// that the renderer actually requests a grant. Both loopback directions run in
// fresh browser contexts so an app cookie never leaks from an earlier case.
// This harness owns a disposable daemon, port, data directory and artifacts.
// Stdout carries exactly one machine-readable record: SUMMARY { ... }.

import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { authenticate, waitUntil } from "./browser_auth.mjs";

const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pythonPath = process.env.OPENAI4S_PYTHON
  ? path.resolve(process.env.OPENAI4S_PYTHON)
  : path.join(workspaceRoot, ".venv", "bin", "python");
const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const summary = {
  schema_version: 2,
  name: "artifact_sandbox_preview_browser_acceptance",
  cases: [],
  ok: false,
};

function assertion(condition, message) {
  if (!condition) throw new Error(message);
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

function reportDocument(otherArtifactId) {
  return `<!doctype html><html><body>
    <h1 id="heading">static report</h1>
    <img id="figure" src="figure.png" width="8" height="8"
      onload="say('sibling-loaded')" onerror="say('sibling-failed')">
    <button id="cross-frame">Open another session's artifact</button>
    <button id="navigate-app">Navigate to the app with the same grant</button>
    <script>
      function say(probe) {
        parent.postMessage({type: 'sandbox-probe', probe, origin: location.origin}, '*');
      }
      document.getElementById('heading').textContent = 'drawn by script';
      say('script-ran');
      try { void parent.document.body; say('parent-readable'); }
      catch (error) { say('parent-refused'); }
      document.addEventListener('securitypolicyviolation', function (event) {
        if (event.effectiveDirective === 'connect-src') say('connect-src-refused');
      });
      fetch('/api/v1/projects').then(function () { say('api-fetch-allowed'); },
        function () { say('api-fetch-refused'); });
      document.getElementById('cross-frame').onclick = function () {
        const url = new URL(location.href);
        url.pathname = url.pathname.split('/preview/')[0] + '/preview/${otherArtifactId}';
        location.href = url.href;
      };
      document.getElementById('navigate-app').onclick = function () {
        const url = new URL(location.href);
        url.hostname = location.hostname === 'localhost' ? '127.0.0.1' : 'localhost';
        location.href = url.href;
      };
    </script>
  </body></html>`;
}

async function runDirection(browser, port, token, fixtures, appHost) {
  const appOrigin = `http://${appHost}:${port}`;
  const sandboxHost = appHost === "127.0.0.1" ? "localhost" : "127.0.0.1";
  const sandboxOrigin = `http://${sandboxHost}:${port}`;
  const result = { app_origin: appOrigin, sandbox_origin: sandboxOrigin, ok: false };
  summary.cases.push(result);
  const phase = (name) => {
    result.phase = name;
    process.stderr.write(`sandbox-preview ${appHost}: ${name}\n`);
  };
  const context = await browser.newContext({
    serviceWorkers: "block",
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const browserErrors = [];
  page.on("pageerror", (error) => browserErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error" && message.location().url.includes("ketcher")) {
      browserErrors.push(message.text());
    }
  });
  await page.addInitScript(() => {
    window.__sandboxProbes = [];
    window.addEventListener("message", (event) => {
      if (event.data?.type === "sandbox-probe") {
        window.__sandboxProbes.push({ ...event.data, eventOrigin: event.origin });
      }
    });
  });
  try {
    phase("authenticate");
    await authenticate(page, `${appOrigin}/`, token);
    const shell = await page.request.get(`${appOrigin}/`);
    const shellHtml = await shell.text();
    assertion(/\/static\/dist\/assets\//.test(shellHtml), "the acceptance must run the Preact shell");
    result.frame_src = (shell.headers()["content-security-policy"] || "")
      .split(";")
      .map((directive) => directive.trim())
      .find((directive) => directive.startsWith("frame-src "));
    assertion(result.frame_src?.includes(sandboxOrigin), "the shell does not permit its sandbox origin");

    const viewer = page.locator("#dock-viewer iframe");
    const openReport = async (versionId = null) => {
      const url = new URL(`${appOrigin}/projects/${fixtures.projectId}/frames/${fixtures.frameId}`);
      url.searchParams.set("artifact", fixtures.reportId);
      if (versionId) url.searchParams.set("version_id", versionId);
      await page.goto(url.href, { waitUntil: "domcontentloaded" });
      await viewer.waitFor({ state: "visible", timeout: 30000 });
      return viewer;
    };
    const waitForReport = async () => {
      await waitUntil("report script and sibling image", () => page.evaluate(() => {
        const probes = window.__sandboxProbes.map((entry) => entry.probe);
        return probes.includes("script-ran") && probes.includes("sibling-loaded");
      }));
      const src = await viewer.getAttribute("src");
      assertion(src?.startsWith(`${sandboxOrigin}/sandbox/`), `preview used the wrong origin: ${src}`);
      assertion(await viewer.getAttribute("sandbox") === "allow-scripts allow-same-origin",
        "the authorized sandbox preview never enabled scripts");
      return src;
    };

    // These are the production deep-link and dock-renderer paths; no legacy
    // renderHtmlPreview global and no hand-built iframe participate.
    phase("interactive preview");
    await openReport();
    const grantedSrc = await waitForReport();
    const reported = await page.evaluate(() => window.__sandboxProbes);
    result.script_ran = reported.some((entry) => entry.probe === "script-ran");
    result.sibling_loaded = reported.some((entry) => entry.probe === "sibling-loaded");
    result.parent_refused = reported.some((entry) => entry.probe === "parent-refused")
      && !reported.some((entry) => entry.probe === "parent-readable");
    result.reported_origin = reported[0]?.origin;
    assertion(result.parent_refused, "the artifact could read its parent document");
    assertion(reported.every((entry) => entry.origin === sandboxOrigin && entry.eventOrigin === sandboxOrigin),
      "the preview script did not execute on the sandbox origin");
    result.sandbox_has_no_app_cookie = (await context.cookies(sandboxOrigin)).length === 0;
    assertion(result.sandbox_has_no_app_cookie, "the app login installed a cookie on the sandbox origin");
    const contentFrame = await (await viewer.elementHandle()).contentFrame();
    assertion(await contentFrame.locator("#heading").innerText() === "drawn by script",
      "the script did not update the visible report");
    assertion(await contentFrame.locator("#figure").evaluate((image) => image.complete && image.naturalWidth > 0),
      "the report's sibling image did not decode");

    // Let the untrusted document spend its own grant on a different frame.
    // A browser navigation, observed at the response, proves actual rejection;
    // an opaque no-cors fetch would reveal neither the status nor the content.
    phase("cross-session navigation");
    const crossUrl = new URL(grantedSrc);
    crossUrl.pathname = crossUrl.pathname.replace(/preview\/[^/]+$/, `preview/${fixtures.secretId}`);
    const crossResponse = page.waitForResponse((response) => response.url() === crossUrl.href);
    await contentFrame.locator("#cross-frame").click();
    const refusedCross = await crossResponse;
    result.cross_frame_status = refusedCross.status();
    result.cross_frame_refused = refusedCross.status() === 404;
    assertion(result.cross_frame_refused, `a grant read another session (${refusedCross.status()})`);

    // A sandboxed frame can navigate itself. Reusing the authorized path on
    // the app origin must fail before any model-authored bytes are served.
    phase("app-origin grant replay");
    await openReport();
    const newSrc = await waitForReport();
    const sameGrantAppUrl = new URL(newSrc);
    sameGrantAppUrl.hostname = appHost;
    const appResponse = page.waitForResponse((response) => response.url() === sameGrantAppUrl.href);
    const navigatingFrame = await (await viewer.elementHandle()).contentFrame();
    await navigatingFrame.locator("#navigate-app").click();
    const refusedApp = await appResponse;
    result.app_origin_replay_status = refusedApp.status();
    result.app_origin_replay_refused = refusedApp.status() === 404;
    assertion(result.app_origin_replay_refused,
      `the same grant served executable artifact bytes on the app origin (${refusedApp.status()})`);

    // A historical Artifact deep link must keep its bytes after a newer edit.
    phase("exact version");
    await openReport(fixtures.reportVersionId);
    await waitForReport();
    const exactFrame = await (await viewer.elementHandle()).contentFrame();
    result.exact_version_preserved = await exactFrame.locator("#revision").innerText() === "original revision";
    assertion(result.exact_version_preserved, "the granted preview substituted the latest version for an exact deep link");

    // Only the grant response is fault-injected. The artifact, inert preview
    // response, Viewer, and renderer are real, so this proves a useful static
    // report survives failure without ever running the embedded script.
    phase("inert fallback");
    await page.route("**/api/v1/artifacts/*/sandbox-grant*", (route) => route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "grant unavailable in browser acceptance" }),
    }));
    await openReport();
    await page.locator("#dock-viewer .renderer-noscript").waitFor({ state: "visible" });
    const inertFrame = await (await viewer.elementHandle()).contentFrame();
    await inertFrame.locator("#heading").waitFor({ state: "visible" });
    result.inert_without_grant = {
      sandbox: await viewer.getAttribute("sandbox"),
      app_origin: new URL(await viewer.getAttribute("src"), appOrigin).origin === appOrigin,
      static_content: await inertFrame.locator("#heading").innerText() === "static report",
      script_ran: await page.evaluate(() => window.__sandboxProbes.some((entry) => entry.probe === "script-ran")),
    };
    assertion(result.inert_without_grant.sandbox === ""
      && result.inert_without_grant.app_origin
      && result.inert_without_grant.static_content
      && !result.inert_without_grant.script_ran,
    `a refused grant did not leave a useful inert preview: ${JSON.stringify(result.inert_without_grant)}`);
    await page.unroute("**/api/v1/artifacts/*/sandbox-grant*");

    // Both loopback names are valid app entry points. If a user has logged in
    // to both, the sandbox name can already hold a cookie: the artifact CSP
    // must still prevent it from calling that origin's authenticated API.
    phase("authenticated sandbox API refusal");
    const alternateLogin = await context.newPage();
    await alternateLogin.goto(`${sandboxOrigin}/?token=${encodeURIComponent(token)}`, {
      waitUntil: "domcontentloaded",
    });
    assertion(!new URL(alternateLogin.url()).searchParams.has("token"),
      "alternate-host login did not consume the credential");
    assertion((await context.cookies(sandboxOrigin)).length > 0,
      "alternate-host scenario did not establish an authenticated cookie");
    await alternateLogin.close();
    await openReport();
    await waitForReport();
    await waitUntil("authenticated sandbox API refusal", () => page.evaluate(() => {
      const probes = window.__sandboxProbes.map((entry) => entry.probe);
      return probes.includes("connect-src-refused") && probes.includes("api-fetch-refused");
    }));
    result.authenticated_sandbox_api_refused = await page.evaluate(() =>
      !window.__sandboxProbes.some((entry) => entry.probe === "api-fetch-allowed"));
    assertion(result.authenticated_sandbox_api_refused,
      "an artifact could call the API after both loopback origins were authenticated");

    // Ketcher is first-party UI, so it must stay on the app origin even after
    // a successful preview has established the alternate sandbox origin.
    phase("Ketcher edit and save");
    await openReport();
    await waitForReport();
    await page.evaluate((artifactId) => window.openKetcher({ id: artifactId }), fixtures.moleculeId);
    const ketcherFrameElement = page.locator("#modal-body > iframe");
    await ketcherFrameElement.waitFor({ state: "visible" });
    const ketcherSrc = new URL(await ketcherFrameElement.getAttribute("src"), appOrigin);
    assertion(ketcherSrc.origin === appOrigin && ketcherSrc.pathname === "/ketcher",
      "Ketcher was moved off the first-party app origin");
    assertion(await ketcherFrameElement.getAttribute("sandbox") === null, "Ketcher inherited the artifact sandbox");
    const ketcherFrame = await (await ketcherFrameElement.elementHandle()).contentFrame();
    await waitUntil("Ketcher artifact load", async () =>
      (await ketcherFrame.locator("#ketcher-status").innerText()).startsWith("loaded "), 45000);
    const editor = ketcherFrame.childFrames().find((frame) => frame.url().includes("/static/vendor/ketcher/"));
    assertion(editor, "Ketcher never loaded its vendored editor");
    await waitUntil("real Ketcher editor", () => editor.evaluate(() =>
      typeof window.ketcher?.setMolecule === "function"
        && typeof window.ketcher?.getSmiles === "function"), 45000);
    const atom = appHost === "127.0.0.1" ? "O" : "N";
    const editedMolfile = "edited molecule\n  OpenAI4S\n\n"
      + "  2  1  0  0  0  0            999 V2000\n"
      + "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
      + `    1.0000    0.0000    0.0000 ${atom}   0  0  0  0  0  0  0  0  0  0  0  0\n`
      + "  1  2  1  0  0  0  0\nM  END\n";
    // The pinned editor's setMolecule returns before its import action
    // completes. Wait for the actual canvas serialization to change before
    // clicking Save, just as a user waits for the editor to finish drawing.
    await editor.evaluate((molfile) => window.ketcher.setMolecule(molfile), editedMolfile);
    await waitUntil("Ketcher molecule edit", async () => {
      const edited = await editor.evaluate(() => Promise.race([
        window.ketcher.getMolfile(),
        new Promise((_, reject) => setTimeout(() => reject(new Error("Ketcher serialization timed out")), 10000)),
      ]));
      return edited.includes(` ${atom} `);
    }, 30000);
    result.ketcher_edited_atom = atom;
    await ketcherFrame.locator("#ketcher-save").click();
    await waitUntil("Ketcher artifact save", async () =>
      (await ketcherFrame.locator("#ketcher-status").innerText()).startsWith("saved "));
    const savedMolecule = await page.request.get(`${appOrigin}/api/v1/artifacts/${fixtures.moleculeId}`);
    assertion(savedMolecule.ok() && (await savedMolecule.text()).includes(` ${atom} `),
      "Ketcher reported a save without persisting the edited molecule");
    result.ketcher_first_party = true;
    result.ketcher_saved = true;
    phase("complete");
    result.ok = true;
  } catch (error) {
    if (browserErrors.length) result.browser_errors = browserErrors.slice(-10);
    result.error = error?.stack || String(error);
  } finally {
    await context.close();
  }
}

async function main() {
  const port = await allocateLoopbackPort();
  assertion(port !== 8760, "the acceptance must not use the user's daemon port");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "openai4s-sandbox-preview-"));
  const appOrigin = `http://127.0.0.1:${port}`;
  const daemon = spawn(pythonPath,
    ["-m", "openai4s", "serve", "--no-browser", "--port", String(port)], {
      cwd: workspaceRoot,
      env: {
        ...process.env,
        OPENAI4S_DATA_DIR: dataDir,
        OPENAI4S_HOST: "127.0.0.1",
        OPENAI4S_PORT: String(port),
        OPENAI4S_REQUIRE_TOKEN: "1",
        OPENAI4S_ALLOW_NETWORK: "0",
        OPENAI4S_SKIP_DOTENV: "1",
        OPENAI4S_SECRET_STORE: "plaintext",
        OPENAI4S_WEBUI: "",
        OPENAI4S_STAGE9_ARTIFACT_WORKBENCH: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
  daemon.stdout.resume();
  daemon.stderr.resume();
  let startupError = null;
  daemon.once("error", (error) => { startupError = error; });
  let browser = null;
  try {
    await waitUntil("daemon startup", async () => {
      if (startupError) throw startupError;
      if (daemon.exitCode !== null) throw new Error(`daemon exited with ${daemon.exitCode}`);
      if (!fs.existsSync(path.join(dataDir, "access-token"))) return false;
      return (await fetch(`${appOrigin}/health`)).ok;
    }, 60000, 250);
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
    const projectId = projects.projects?.[0]?.project_id;
    assertion(projectId, "no project to attach a session to");
    const frame = await api("/frames", { title: "sandbox preview", project_id: projectId });
    const other = await api("/frames", { title: "other session", project_id: projectId });
    const upload = async (frameId, filename, contentType, bytes) => {
      const result = await api("/uploads", {
        frame_id: frameId,
        filename,
        content_type: contentType,
        content_base64: bytes.toString("base64"),
      });
      return result.artifact_id || result.id;
    };
    const secretId = await upload(other.id, "secret.html", "text/html", Buffer.from("<html>OTHER SESSION</html>"));
    const report = reportDocument(secretId);
    const reportId = await upload(frame.id, "report.html", "text/html",
      Buffer.from(report.replace("<h1", '<p id="revision">original revision</p><h1')));
    const reportVersions = await api(`/artifacts/${reportId}/versions`);
    const reportVersionId = reportVersions.versions?.[0]?.version_id;
    assertion(reportVersionId, "report upload has no immutable version");
    await api(`/artifacts/${reportId}/edit`, {
      content: report.replace("<h1", '<p id="revision">latest revision</p><h1'),
    });
    await upload(frame.id, "figure.png", "image/png", Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));
    const moleculeId = await upload(frame.id, "molecule.mol", "chemical/x-mdl-molfile", Buffer.from(
      "methane\n  OpenAI4S\n\n  1  0  0  0  0  0            999 V2000\n    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n"));
    const { chromium } = await import("playwright");
    browser = await chromium.launch({ headless: true, executablePath });
    const fixtures = { reportId, reportVersionId, secretId, moleculeId, projectId, frameId: frame.id };
    for (const host of ["127.0.0.1", "localhost"]) {
      await runDirection(browser, port, token, fixtures, host);
    }
    summary.ok = summary.cases.every((result) => result.ok);
    if (!summary.ok) process.exitCode = 1;
  } finally {
    if (browser) await browser.close().catch(() => {});
    daemon.kill("SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 500));
    if (daemon.exitCode === null) daemon.kill("SIGKILL");
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  summary.error = error?.stack || String(error);
  process.exitCode = 1;
}).finally(() => {
  process.stdout.write(`SUMMARY ${JSON.stringify(summary)}\n`);
});
