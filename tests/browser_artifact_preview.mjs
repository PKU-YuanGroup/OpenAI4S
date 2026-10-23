// End-to-end acceptance for the A1–A4 artifact-preview work, driven in the
// real committed Preact workbench against a disposable daemon.
//
// Opening a saved artifact is the one path where "the bytes are fine" and
// "the viewer told the truth about them" are different claims. Unit tests can
// prove the budgets and parsers; only a browser can prove the failure card is
// actually shown, that Retry re-reads, that a partial Markdown says so, that a
// fixed version stays fixed after a second version exists, and that opening a
// notebook executes nothing and calls no other origin.
//
// This harness owns its daemon, port, data directory and artifacts:
//   OPENAI4S_PYTHON          python used to start the daemon (default: repo .venv)
//   OPENAI4S_BROWSER_EXECUTABLE  optional Chromium path
//   OPENAI4S_PLAYWRIGHT_MODULE   optional playwright entry (for checkouts whose
//                                node_modules live outside this worktree)
// It drives whatever `openai4s/server/webui/dist/` this checkout serves, so run
// `npm run build --prefix frontend` first; the lane that changes frontend source
// verifies with a local build and leaves the committed dist to the integrator.
// Stdout carries exactly one machine-readable record: SUMMARY { ... }.
// It never prints the access token; a failure summary is redacted.

import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  authenticate,
  boundedLogCollector,
  minimalChildEnvironment,
  redactSecrets,
  waitUntil,
} from "./browser_auth.mjs";

const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pythonPath = process.env.OPENAI4S_PYTHON
  ? path.resolve(process.env.OPENAI4S_PYTHON)
  : path.join(workspaceRoot, ".venv", "bin", "python");
const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;

const summary = {
  schema_version: 1,
  name: "artifact_preview_browser_acceptance",
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

const PNG_1X1 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

// A small multiple-sequence alignment: the sequence-family renderer a file can
// select by extension (a bare .fasta capture is stamped text/plain by the
// Gateway's content-type guess, so it would exercise the text path instead).
const SMALL_MSA = [
  ">spike_human",
  "ACDEFGHIKLMNPQRSTVWY",
  ">spike_mouse",
  "ACDEFGHIKLMNPQRSAVWY",
  ">spike_bat",
  "ACDEYGHIKLMNPQRSTVWY",
].join("\n");
const PDB = [
  "ATOM      1  N   ALA A   1      11.000  12.000  13.000  1.00  0.00           N",
  "ATOM      2  CA  ALA A   1      21.000  22.000  23.000  1.00  0.00           C",
  "ATOM      3  C   ALA A   1      31.000  32.000  33.000  1.00  0.00           C",
  "ATOM      4  CA  ALA A   2      41.000  42.000  43.000  1.00  0.00           C",
  "ATOM      5  CA  ALA A   3      51.000  52.000  53.000  1.00  0.00           C",
  "ATOM      6  CA  ALA A   4      61.000  62.000  63.000  1.00  0.00           C",
].join("\n");
const SMALL_CSV = "gene,logFC\nTP53,2.4\nBRCA1,-1.1\n";
const SMALL_MARKDOWN = "# Short report\n\nAll rows are present.\n";
const NOTEBOOK = JSON.stringify({
  nbformat: 4,
  nbformat_minor: 5,
  metadata: { kernelspec: { display_name: "Python 3", language: "python", name: "python3" } },
  cells: [
    {
      cell_type: "markdown",
      metadata: {},
      source: [
        "# Saved analysis\n\n",
        "![inline](data:image/png;base64," + PNG_1X1 + ")\n\n",
        "![external](https://example.invalid/x.png)\n\n",
        "[paper](https://example.com/paper)\n",
      ],
    },
    {
      cell_type: "code",
      execution_count: 1,
      metadata: {},
      source: "print('saved output')\n",
      outputs: [
        { output_type: "stream", name: "stdout", text: ["saved output\n", "[OpenAI4S artifacts] figure.png\n"] },
        {
          output_type: "display_data",
          metadata: {},
          data: { "image/png": PNG_1X1, "text/plain": "<Figure size 1x1>" },
        },
        {
          output_type: "display_data",
          metadata: {},
          data: { "text/html": "<img src=x onerror=window.__notebookHtmlRan=1>" },
        },
      ],
    },
  ],
});

async function runCase(page, name, body) {
  const result = { name, ok: false };
  try {
    await body();
    result.ok = true;
  } catch (error) {
    result.error = redactSecrets(error?.stack || String(error));
    process.exitCode = 1;
  }
  summary.cases.push(result);
  return result;
}

async function main() {
  const port = await allocateLoopbackPort();
  assertion(port !== 8760, "the acceptance must not use the user's daemon port");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "openai4s-artifact-preview-"));
  const appOrigin = `http://127.0.0.1:${port}`;
  const daemon = spawn(
    pythonPath,
    ["-m", "openai4s", "serve", "--no-browser", "--port", String(port)],
    {
      cwd: workspaceRoot,
      env: minimalChildEnvironment({
        OPENAI4S_DATA_DIR: dataDir,
        OPENAI4S_HOST: "127.0.0.1",
        OPENAI4S_PORT: String(port),
        OPENAI4S_ALLOW_NETWORK: "0",
        OPENAI4S_WEBUI: "",
      }),
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  const daemonStdout = boundedLogCollector(daemon.stdout);
  const daemonStderr = boundedLogCollector(daemon.stderr);
  let startupError = null;
  daemon.once("error", (error) => {
    startupError = error;
  });
  let browser = null;
  let token = null;
  let page = null;
  try {
    await waitUntil(
      "daemon startup",
      async () => {
        if (startupError) throw startupError;
        if (daemon.exitCode !== null) throw new Error(`daemon exited with ${daemon.exitCode}`);
        if (!fs.existsSync(path.join(dataDir, "access-token"))) return false;
        return (await fetch(`${appOrigin}/health`)).ok;
      },
      60000,
      250,
    );
    token = fs.readFileSync(path.join(dataDir, "access-token"), "utf8").trim();
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
    const frame = await api("/frames", { title: "artifact preview", project_id: projectId });
    const fid = frame.id || frame.frame_id;
    const upload = async (filename, contentType, bytes) => {
      const result = await api("/uploads", {
        frame_id: fid,
        project_id: projectId,
        filename,
        content_type: contentType,
        content_base64: Buffer.from(bytes).toString("base64"),
      });
      const artifactId = result.artifact_id || result.id;
      assertion(artifactId, `${filename} upload returned no artifact id`);
      const versions = await api(`/artifacts/${artifactId}/versions`);
      const versionId = versions.versions?.[0]?.version_id;
      assertion(versionId, `${filename} upload has no immutable version`);
      return { artifactId, versionId };
    };

    const sequence = await upload("alignment.a3m", "application/octet-stream", SMALL_MSA);
    const broken = await upload("broken-stream.a3m", "application/octet-stream", SMALL_MSA);
    const molecule = await upload("peptide.pdb", "chemical/x-pdb", PDB);
    const csv = await upload("counts.csv", "text/csv", SMALL_CSV);
    const markdown = await upload("report.md", "text/markdown", SMALL_MARKDOWN);
    const largeMarkdown = await upload(
      "large-report.md",
      "text/markdown",
      "# Large report\n\n" + "row of a long report\n".repeat(60000),
    );
    const oversized = await upload(
      "oversized.a3m",
      "application/octet-stream",
      ">oversized\n" + "ACDEFGHIKLMNPQRSTVWY".repeat(Math.ceil((8 * 1024 * 1024 + 64) / 20)),
    );
    const notebook = await upload("session.python.ipynb", "application/x-ipynb+json", NOTEBOOK);
    // Two immutable versions of the same artifact, to prove a fixed-version
    // preview and its download keep addressing version one.
    const history = await upload("history.csv", "text/csv", "revision,value\n1,11\n");
    await api(`/artifacts/${history.artifactId}/edit`, { content: "revision,value\n2,22\n" });
    const historyVersions = (await api(`/artifacts/${history.artifactId}/versions`)).versions || [];
    assertion(historyVersions.length >= 2, "history.csv did not keep two immutable versions");
    const firstVersion =
      historyVersions.find((row) => row.is_latest === false) || historyVersions[historyVersions.length - 1];

    const playwrightModule = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
    let playwright;
    try {
      playwright = await import("playwright");
    } catch (error) {
      if (!playwrightModule) throw error;
      playwright = await import(playwrightModule);
    }
    const { chromium } = playwright;
    browser = await chromium.launch({ headless: true, executablePath });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    const requests = [];
    page.on("request", (request) => requests.push(request.url()));
    // Pass the token this harness read from its own data dir: authenticate()'s
    // default lookup would read the developer's ~/.openai4s token instead.
    await authenticate(page, appOrigin + "/", token);
    await page.waitForFunction(() => typeof window.openViewer === "function", null, {
      timeout: 30000,
    });
    // The dock only has a box once the workspace it belongs to is open; a bare
    // home page renders every pane with a zero-size parent.
    await page.evaluate(
      async ({ frameId, project }) => window.openConversation(frameId, project),
      { frameId: fid, project: projectId },
    );
    await page.locator("#workspace:not(.hidden)").waitFor({ state: "visible", timeout: 30000 });

    const open = async ({ artifactId, versionId }, filename) => {
      await page.evaluate(async (a) => window.openViewer(a), {
        id: artifactId,
        filename,
        version_id: versionId,
      });
      await page.locator("#dock-viewer .renderer-shell, #dock-viewer .dock-empty").waitFor({
        state: "visible",
        timeout: 30000,
      });
    };

    await runCase(page, "small sequence alignment renders", async () => {
      await open(sequence, "alignment.a3m");
      await page.locator("#dock-viewer .msa-viewport").waitFor({ timeout: 30000 });
      const text = await page.locator("#dock-viewer .bio-summary").innerText();
      assertion(/sequences|序列/i.test(text), `alignment summary missing: ${text}`);
    });

    await runCase(page, "over-budget file fails honestly with retry and exact download", async () => {
      await open(oversized, "oversized.a3m");
      await page.locator("#dock-viewer .renderer-fallback").waitFor({ timeout: 30000 });
      const card = await page.locator("#dock-viewer .renderer-fallback").innerText();
      assertion(/read limit|读取上限/i.test(card), `failure card did not name the limit: ${card}`);
      assertion(
        (await page.locator("#dock-viewer .renderer-fallback button").count()) >= 1,
        "failure card has no retry",
      );
      const href = await page
        .locator("#dock-viewer .renderer-fallback a")
        .first()
        .getAttribute("href");
      assertion(
        href === `/api/v1/artifacts/versions/${oversized.versionId}`,
        `download is not the exact version: ${href}`,
      );
      // F2 rejects from Content-Length when it is oversized; the header request
      // itself is expected, the body must never be parsed into a preview.
      const reads = requests.filter((url) => url.includes(`/artifacts/versions/${oversized.versionId}`));
      assertion(reads.length <= 1, `over-budget artifact was fetched ${reads.length} times`);
      assertion(
        (await page.locator("#dock-viewer .bio-summary").count()) === 0,
        "an over-budget structure was parsed anyway",
      );
    });

    await runCase(page, "interrupted read shows failure and Retry re-reads", async () => {
      const byteUrl = `**/api/v1/artifacts/versions/${broken.versionId}`;
      let aborted = 0;
      await page.route(byteUrl, async (route) => {
        aborted += 1;
        await route.abort("failed");
      });
      try {
        await open(broken, "broken-stream.a3m");
        await page.locator("#dock-viewer .renderer-fallback").waitFor({ timeout: 30000 });
        assertion(aborted >= 1, "the byte read was never intercepted");
      } finally {
        await page.unroute(byteUrl);
      }
      await page.locator("#dock-viewer .renderer-fallback button").first().click();
      await page.locator("#dock-viewer .msa-viewport").waitFor({ timeout: 30000 });
    });

    await runCase(page, "molecule opens without falling back to a blank", async () => {
      await open(molecule, "peptide.pdb");
      await page.locator("#dock-viewer .mol-wrap").waitFor({ timeout: 30000 });
      // The frame is built before the bytes are read; wait for either the drawn
      // canvas or the documented text fallback rather than racing the read.
      await page
        .locator("#dock-viewer .mol-wrap canvas, #dock-viewer .mol-wrap pre")
        .first()
        .waitFor({ timeout: 30000 });
    });

    await runCase(page, "csv renders as a table", async () => {
      await open(csv, "counts.csv");
      await page.locator("#dock-viewer table.sheet").waitFor({ timeout: 30000 });
      const text = await page.locator("#dock-viewer table.sheet").innerText();
      assertion(text.includes("TP53") && text.includes("BRCA1"), "table did not show the saved rows");
    });

    await runCase(page, "markdown states complete vs partial with the full-file entry", async () => {
      await open(markdown, "report.md");
      await page.locator("#dock-viewer .md.renderer-markdown").waitFor({ timeout: 30000 });
      // A complete preview renders as before: no truncation notice is added.
      assertion(
        (await page.locator("#dock-viewer .renderer-completeness").count()) === 0,
        "a complete markdown preview claimed to be truncated",
      );
      assertion(
        (await page.locator("#dock-viewer h1").innerText()).includes("Short report"),
        "markdown heading missing",
      );
      await open(largeMarkdown, "large-report.md");
      const partial = page.locator('#dock-viewer .renderer-completeness[data-state="partial"]');
      await partial.first().waitFor({ timeout: 30000 });
      // One notice above and one below the rendered body (F3/A3).
      assertion(
        (await partial.count()) === 2,
        `expected two partial notices, saw ${await partial.count()}`,
      );
      const note = await partial.first().innerText();
      assertion(/Partial preview|部分预览/i.test(note), `partial state not announced: ${note}`);
      const href = await page
        .locator("#dock-viewer a.outline-btn")
        .last()
        .getAttribute("href");
      assertion(
        href === `/api/v1/artifacts/versions/${largeMarkdown.versionId}`,
        `partial preview full-file link is not the exact version: ${href}`,
      );
    });

    await runCase(page, "notebook renders saved cells without executing or calling out", async () => {
      const versionsBefore = await api(`/artifacts/${notebook.artifactId}/versions`);
      const before = requests.length;
      await open(notebook, "session.python.ipynb");
      await page.locator("#dock-viewer .notebook-preview").waitFor({ timeout: 30000 });
      await page.locator("#dock-viewer .notebook-png").waitFor({ timeout: 30000 });
      const banner = await page.locator("#dock-viewer .notebook-readonly").innerText();
      assertion(
        /nothing was executed|未在本次查看时执行/i.test(banner),
        `read-only label missing: ${banner}`,
      );
      assertion(
        (await page.locator("#dock-viewer .notebook-cell").count()) === 2,
        "notebook cells were not rendered",
      );
      assertion(
        (await page.locator("#dock-viewer .notebook-markdown img").count()) === 0,
        "markdown cell produced an image node",
      );
      assertion(
        (await page.locator("#dock-viewer .notebook-markdown a").count()) === 0,
        "markdown cell produced a navigable link",
      );
      assertion(
        (await page.locator("#dock-viewer .notebook-output-unsupported").count()) === 1,
        "html output was not handled as unsupported",
      );
      // F5: the code cell is the copy-enabled code block.
      assertion(
        (await page.locator("#dock-viewer .notebook-code .cb-copy").count()) === 1,
        "the notebook code cell has no Copy button",
      );
      // The saved artifact line stays plain text; it must not be fetched.
      assertion(
        (await page.locator("#dock-viewer .notebook-output-text").innerText()).includes(
          "[OpenAI4S artifacts] figure.png",
        ),
        "the saved artifact line is missing",
      );
      assertion(
        (await page.locator('#dock-viewer img[src^="http"]').count()) === 0,
        "the notebook rendered an external image",
      );
      assertion(
        (await page.locator("#dock-viewer script, #dock-viewer iframe").count()) === 0,
        "the notebook inserted a script or iframe",
      );
      assertion(
        (await page.evaluate(() => window.__notebookHtmlRan)) === undefined,
        "notebook HTML output executed",
      );
      const during = requests.slice(before);
      const forbidden = during.filter((url) =>
        /\/kernel|\/execut|\/host\b|renderers\?/.test(new URL(url).pathname + new URL(url).search),
      );
      assertion(forbidden.length === 0, `notebook preview called ${forbidden.join(", ")}`);
      const foreign = during.filter((url) => new URL(url).origin !== appOrigin);
      assertion(foreign.length === 0, `notebook preview reached another origin: ${foreign.join(", ")}`);
      const kernel = await api(`/frames/${fid}/kernel`);
      const python = (kernel.kernels || []).find((item) => item.language === "python") || {};
      assertion(
        kernel.alive !== true && python.alive !== true,
        "opening the notebook started a kernel",
      );
      // Viewing never rewrote the file: same version identity and bytes hash.
      const versionsAfter = await api(`/artifacts/${notebook.artifactId}/versions`);
      assertion(
        JSON.stringify(versionsAfter) === JSON.stringify(versionsBefore),
        "opening the notebook changed the artifact version history",
      );
    });

    await runCase(page, "fixed-version preview and download keep version one", async () => {
      await open(
        { artifactId: history.artifactId, versionId: firstVersion.version_id },
        "history.csv",
      );
      await page.locator("#dock-viewer table.sheet").waitFor({ timeout: 30000 });
      const href = await page
        .locator("#dock-viewer .viewer-head a[download]")
        .first()
        .getAttribute("href");
      assertion(
        href === `/api/v1/artifacts/versions/${firstVersion.version_id}`,
        `fixed-version download moved off version one: ${href}`,
      );
      const text = await page.locator("#dock-viewer table.sheet").innerText();
      assertion(text.includes("1") && !text.includes("2,22"), "fixed version preview showed latest bytes");
    });

    await runCase(page, "no page errors during the preview cases", async () => {
      assertion(pageErrors.length === 0, `page errors: ${pageErrors.join(" | ")}`);
    });

    summary.ok = summary.cases.every((result) => result.ok);
    if (!summary.ok) process.exitCode = 1;
    await context.close();
  } finally {
    try {
      if (browser) await browser.close();
    } catch {
      /* already closed */
    }
    daemon.kill("SIGTERM");
    const exited = await Promise.race([
      new Promise((resolve) => daemon.once("exit", () => resolve(true))),
      new Promise((resolve) => setTimeout(() => resolve(false), 15000)),
    ]);
    summary.daemon_cleanup = exited;
    if (!exited) {
      daemon.kill("SIGKILL");
      summary.ok = false;
      process.exitCode = 1;
    }
    if (!summary.ok) {
      summary.daemon_log = redactSecrets(
        `${daemonStdout()}\n${daemonStderr()}`,
        token,
      ).trim().slice(-16 * 1024);
    }
    fs.rmSync(dataDir, { recursive: true, force: true });
    console.log("SUMMARY " + JSON.stringify(summary));
  }
}

await main();
